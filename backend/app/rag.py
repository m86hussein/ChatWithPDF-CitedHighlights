"""Grounded Q&A over one document: retrieve -> rerank -> answer with verbatim quotes -> locate + score."""
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import bedrock, vectors
from .config import settings
from .db import Anchor, Chunk, Document, Message
from .pdf_extract import find_refs
from .textnorm import has_arabic, normalize, numbers_in

TOP_K_VECTOR = 24
TOP_K_CONTEXT = 8

ANSWER_TOOL = {
    "name": "grounded_answer",
    "description": "Return the answer as statements, each backed by verbatim quotes from the numbered sources.",
    "inputSchema": {"json": {
        "type": "object",
        "properties": {
            "answer_found": {"type": "boolean", "description": "false if the sources do not contain the answer"},
            "statements": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "One or two sentences of the answer, in the user's language"},
                    "citations": {"type": "array", "items": {
                        "type": "object",
                        "properties": {
                            "source": {"type": "integer", "description": "Source number"},
                            "quote": {"type": "string", "description": "Exact contiguous excerpt copied from that source (5-40 words), original language, not translated"},
                        },
                        "required": ["source", "quote"],
                    }},
                },
                "required": ["text", "citations"],
            }},
        },
        "required": ["answer_found", "statements"],
    }},
}

SYSTEM = """You answer questions about ONE uploaded document using ONLY the numbered sources provided.
Rules:
- Use no outside knowledge. If the sources do not answer the question, set answer_found=false and return a single statement saying the document does not contain this information (no citations).
- Reply in the language of the user's question (Arabic or English), even if the sources are in another language.
- Break the answer into short statements. Every statement must be fully supported by at least one citation.
- A citation's quote must be copied character-for-character from the source text (in the source's original language). Never translate, paraphrase or merge quotes.
- Numbers, dates, names and units must match the sources exactly.
- If a source points to another table, figure, section or appendix that is also provided, you may cite it too."""


def _locate(quote: str, words: list[list]) -> tuple[list[int], float]:
    """Find the quote inside a chunk's words; return (word indices, fidelity 0..1)."""
    q = normalize(quote)
    norm, spans = [], []
    pos = 0
    for w in words:
        t = normalize(str(w[0]))
        spans.append((pos, pos + len(t)))
        norm.append(t)
        pos += len(t) + 1
    hay = " ".join(norm)
    if not q or not hay:
        return list(range(len(words))), 0.0
    if len(q) >= len(hay):
        return list(range(len(words))), fuzz.ratio(q, hay) / 100
    al = fuzz.partial_ratio_alignment(q, hay)
    a, b = al.dest_start, al.dest_end
    idx = [i for i, (s, e) in enumerate(spans) if e > a and s < b and e > s]
    return idx or list(range(len(words))), al.score / 100


def _rects(words: list[list], idx: list[int]) -> list[list[float]]:
    by_line: dict[int, list[float]] = {}
    order = []
    for i in idx:
        _, x0, y0, x1, y1, line = words[i]
        r = by_line.get(line)
        if r is None:
            by_line[line] = [x0, y0, x1, y1]
            order.append(line)
        else:
            by_line[line] = [min(r[0], x0), min(r[1], y0), max(r[2], x1), max(r[3], y1)]
    return [[round(v, 5) for v in by_line[l]] for l in order]


def _level(score: float) -> str:
    return "high" if score >= 0.75 else "medium" if score >= 0.5 else "low"


def _cosine_support(statement: str, passages: list[str]) -> list[float]:
    vecs = np.array(bedrock.embed([statement, *passages], "search_document"))
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    sims = vecs[1:] @ vecs[0]
    # Cohere v4 cosine for a paraphrase sits roughly in 0.45-0.85; map that band onto 0..1.
    return [float(np.clip((s - 0.3) / 0.5, 0, 1)) for s in sims]


def _history(db: Session, doc: Document, user_id: str, limit: int = 6) -> list[dict]:
    msgs = db.scalars(select(Message).where(Message.document_id == doc.id, Message.user_id == user_id)
                      .order_by(Message.created_at.desc()).limit(limit)).all()[::-1]
    out = []
    for m in msgs:
        role = "user" if m.role == "user" else "assistant"
        if out and out[-1]["role"] == role:
            continue
        if not out and role != "user":
            continue
        out.append({"role": role, "content": [{"text": m.content[:3000]}]})
    if out and out[-1]["role"] == "user":
        out.pop()
    return out


def answer(db: Session, doc: Document, user_id: str, question: str) -> dict:
    history = _history(db, doc, user_id)
    last_q = next((h["content"][0]["text"] for h in reversed(history) if h["role"] == "user"), "")
    retrieval_q = f"{last_q}\n{question}" if last_q and len(question.split()) < 8 else question

    qv = bedrock.embed([retrieval_q], "search_query")[0]
    hits = vectors.search(doc.id, qv, TOP_K_VECTOR)
    by_id = {c.id: c for c in db.scalars(select(Chunk).where(Chunk.id.in_([h[0] for h in hits]))).all()}
    cands = [(by_id[cid], s) for cid, s in hits if cid in by_id]
    rr = bedrock.rerank(retrieval_q, [c.text for c, _ in cands])
    if rr:
        cands = [(c, r) for (c, _), r in zip(cands, rr)]
        cands.sort(key=lambda x: -x[1])
    sources = cands[:TOP_K_CONTEXT]

    if not sources:
        return {"answer_found": False, "statements": [{"text": _not_found(question), "citations": []}], "citations": []}

    blocks = "\n\n".join(
        f'<source id="{i}" page="{c.page}"' + (f' section="{c.heading[:120]}"' if c.heading else "") + f">\n{c.text}\n</source>"
        for i, (c, _) in enumerate(sources, start=1))
    messages = history + [{"role": "user", "content": [{"text": f"Sources:\n{blocks}\n\nQuestion: {question}"}]}]
    out = bedrock.converse_tool(settings.chat_model, SYSTEM, messages, ANSWER_TOOL, max_tokens=4000)

    anchors = {a.key: a for a in db.scalars(select(Anchor).where(Anchor.document_id == doc.id)).all()}
    return _ground(out, sources, anchors, question)


def _not_found(question: str) -> str:
    return "لا يحتوي المستند على معلومات تجيب عن هذا السؤال." if has_arabic(question) else \
        "The document does not contain information that answers this question."


def _ground(out: dict, sources: list[tuple[Chunk, float]], anchors: dict, question: str) -> dict:
    citations: list[dict] = []
    seen: dict[tuple, int] = {}
    statements = []
    for st in out.get("statements") or []:
        text = str(st.get("text", "")).strip()
        if not text:
            continue
        refs = []
        for c in st.get("citations") or []:
            sid = c.get("source")
            if not isinstance(sid, int) or not 1 <= sid <= len(sources):
                continue
            chunk = sources[sid - 1][0]
            idx, fidelity = _locate(str(c.get("quote", "")), chunk.words)
            key = (chunk.id, idx[0], idx[-1])
            if key not in seen:
                lo, hi = max(0, idx[0] - 15), min(len(chunk.words), idx[-1] + 16)
                seen[key] = len(citations)
                citations.append({
                    "n": len(citations) + 1, "chunk_id": chunk.id, "page": chunk.page, "kind": chunk.kind,
                    "quote": " ".join(str(chunk.words[i][0]) for i in idx),
                    "context": " ".join(str(w[0]) for w in chunk.words[lo:hi]),
                    "rects": _rects(chunk.words, idx) if chunk.kind == "text" else [chunk.words[0][1:5]],
                    "fidelity": round(fidelity, 3), "_chunk_text": chunk.text, "_statements": [],
                })
            ci = seen[key]
            citations[ci]["_statements"].append(text)
            refs.append(citations[ci]["n"])
        statements.append({"text": text, "citations": sorted(set(refs))})

    _score(statements, citations)

    for c in citations:
        xr = []
        for k, _, _ in find_refs(c.pop("_chunk_text")):
            a = anchors.get(k)
            if a and not any(x["key"] == k for x in xr) and not (a.page == c["page"] and a.label in c["context"]):
                xr.append({"key": k, "label": a.label[:120], "page": a.page, "rects": a.rects})
        c["xrefs"] = xr[:5]
        c.pop("_statements")

    found = bool(out.get("answer_found", True)) and bool(citations)
    if not statements:
        statements = [{"text": _not_found(question), "citations": []}]
    return {"answer_found": found, "statements": statements, "citations": citations}


def _score(statements: list[dict], citations: list[dict]) -> None:
    """Matching score = how strongly the cited passage supports the statement that cites it.

    support  : Cohere Rerank 3.5 relevance(statement -> passage incl. ±15 words context), 0..1
               (falls back to calibrated embedding cosine if rerank is unavailable)
    fidelity : fuzzy alignment of the model's quote against the real PDF text, 0..1
    score    = 0.7*support + 0.3*fidelity, capped at 0.45 when a number in the statement is
               absent from every passage it cites (hallucinated / altered figure).
    """
    by_n = {c["n"]: c for c in citations}

    def run(st):
        cited = [by_n[n] for n in st["citations"]]
        if not cited:
            return st, []
        passages = [c["context"] for c in cited]
        sup = bedrock.rerank(st["text"], passages) or _cosine_support(st["text"], passages)
        return st, sup

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(run, statements))

    best: dict[int, float] = {}
    for st, sup in results:
        cited = [by_n[n] for n in st["citations"]]
        missing = numbers_in(st["text"]) - set().union(*(numbers_in(c["_chunk_text"]) for c in cited)) if cited else set()
        scores = []
        for c, s in zip(cited, sup):
            score = 0.7 * s + 0.3 * c["fidelity"]
            if missing:
                score = min(score, 0.45)
            best[c["n"]] = max(best.get(c["n"], 0.0), score)
            c.setdefault("support", round(s, 3))
            if missing:
                c["numbers_missing"] = sorted(missing)
            scores.append(score)
        st["score"] = round(max(scores), 3) if scores else 0.0
        st["level"] = _level(st["score"]) if scores else "none"
    for c in citations:
        c["score"] = round(best.get(c["n"], 0.0), 3)
        c["level"] = _level(c["score"])
