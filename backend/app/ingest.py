"""Background ingestion pipeline: extract -> chunk -> (chart vision) -> embed -> statistics."""
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor

import pymupdf
from sqlalchemy import delete, select

from . import bedrock, vectors
from .config import settings
from .db import Anchor, Chunk, CrossRef, Document, SessionLocal, Statistic, _uuid
from .pdf_extract import PageData, anchors_and_xrefs, chunk_page, extract_page, mark_boilerplate
from .stats import document_language, extract_statistics
from .storage import storage

log = logging.getLogger("ingest")
_pool = ThreadPoolExecutor(max_workers=settings.ingest_workers, thread_name_prefix="ingest")


def submit(document_id: str) -> None:
    _pool.submit(_run, document_id)


def resume_pending() -> None:
    with SessionLocal() as db:
        ids = db.scalars(select(Document.id).where(Document.status.in_(("queued", "processing")))).all()
    for i in ids:
        submit(i)


def _update(document_id: str, **fields) -> None:
    with SessionLocal() as db:
        doc = db.get(Document, document_id)
        if doc:
            for k, v in fields.items():
                setattr(doc, k, v)
            db.commit()


def _run(document_id: str) -> None:
    try:
        _ingest(document_id)
    except Exception as e:
        log.error("ingest failed %s\n%s", document_id, traceback.format_exc())
        _update(document_id, status="failed", stage="", error=str(e)[:2000])


_CHART_TOOL = {
    "name": "read_figure",
    "description": "Describe a figure cropped from a document page.",
    "inputSchema": {"json": {
        "type": "object",
        "properties": {
            "is_chart": {"type": "boolean", "description": "true for charts/graphs/infographics/diagrams carrying data; false for logos, photos, decorations, signatures"},
            "title": {"type": "string"},
            "summary": {"type": "string", "description": "1-3 sentences on what the figure shows, in the language of the figure text"},
            "data_points": {"type": "array", "items": {"type": "object", "properties": {
                "label": {"type": "string"}, "value": {"type": "string"}, "unit": {"type": "string"}},
                "required": ["label", "value"]}},
        },
        "required": ["is_chart", "title", "summary", "data_points"],
    }},
}
_CHART_SYSTEM = ("Read the figure exactly as shown. Only report values that are printed or unambiguously "
                 "readable on the figure; never estimate or invent numbers.")


def _read_charts(pdf: pymupdf.Document, pages: list[PageData]) -> list[tuple[int, list[float], dict]]:
    if not settings.chart_vision:
        return []
    jobs = []
    for p in pages:
        if p.ocr:
            continue
        for r in p.images:
            w, h = r[2] - r[0], r[3] - r[1]
            if w * h >= 0.04 and w >= 0.2 and h >= 0.1:
                jobs.append((p.number, r))
    jobs.sort(key=lambda j: -((j[1][2] - j[1][0]) * (j[1][3] - j[1][1])))
    jobs = jobs[: settings.chart_vision_max]
    crops = []
    for pno, r in jobs:
        page = pdf[pno - 1]
        W, H = page.rect.width, page.rect.height
        clip = pymupdf.Rect(r[0] * W, r[1] * H, r[2] * W, r[3] * H)
        crops.append((pno, r, page.get_pixmap(clip=clip, dpi=150).tobytes("png")))

    def run(job):
        pno, r, png = job
        try:
            res = bedrock.converse_tool(settings.stats_model, _CHART_SYSTEM, [{"role": "user", "content": [
                bedrock.image_block(png), {"text": "Describe this figure."}]}], _CHART_TOOL, max_tokens=3000)
            return (pno, r, res) if res.get("is_chart") else None
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        return [x for x in pool.map(run, crops) if x]


def _ingest(document_id: str) -> None:
    with SessionLocal() as db:
        doc = db.get(Document, document_id)
        if not doc:
            return
        key = doc.storage_key
    _update(document_id, status="processing", stage="Reading pages", progress=0.02, error="")

    pdf = pymupdf.open(stream=storage.get(key), filetype="pdf")
    n = pdf.page_count
    pages: list[PageData] = []
    for i, page in enumerate(pdf):
        pages.append(extract_page(page))
        if i % 5 == 0 or i == n - 1:
            ocr = sum(p.ocr for p in pages)
            _update(document_id, stage=f"Reading page {i + 1}/{n}" + (f" ({ocr} OCR)" if ocr else ""),
                    progress=0.02 + 0.38 * (i + 1) / n)
    mark_boilerplate(pages)

    anchors, xrefs = anchors_and_xrefs(pages)
    chunks, heading = [], ""
    for p in pages:
        page_chunks, heading = chunk_page(p, heading)
        for seq, c in enumerate(page_chunks):
            words = [[p.words[i].text, *p.words[i].rect, p.words[i].line] for i in c["word_idx"]]
            chunks.append(Chunk(id=_uuid(), document_id=document_id, page=p.number, seq=seq, kind="text",
                                heading=c["heading"], text=c["text"], words=words))

    _update(document_id, stage="Reading charts", progress=0.42)
    charts = _read_charts(pdf, pages)
    chart_stats = []
    for pno, r, res in charts:
        pts = res.get("data_points") or []
        body = "\n".join(f"- {d.get('label', '')}: {d.get('value', '')} {d.get('unit', '') or ''}".strip() for d in pts)
        text = f"[Figure] {res.get('title', '')}\n{res.get('summary', '')}\n{body}".strip()
        chunks.append(Chunk(id=_uuid(), document_id=document_id, page=pno, seq=1000 + len(chart_stats), kind="chart",
                            heading=res.get("title", ""), text=text, words=[[text, *r, -1]]))
        for d in pts:
            chart_stats.append({"page": pno, "value": f"{d.get('value', '')} {d.get('unit', '') or ''}".strip(),
                                "numeric_value": _to_float(d.get("value")), "unit": d.get("unit") or "",
                                "category": "percentage" if "%" in f"{d.get('value')}{d.get('unit')}" else "number",
                                "metric": d.get("label", "")[:200], "context": res.get("title", "")[:1000],
                                "source": "chart", "rects": [r]})

    _update(document_id, stage=f"Indexing {len(chunks)} passages", progress=0.5)
    vectors.delete_document(document_id)
    texts = [(f"{c.heading}\n{c.text}" if c.heading and c.heading not in c.text[:200] else c.text) for c in chunks]
    vecs = bedrock.embed(texts, "search_document") if texts else []
    vectors.upsert(document_id, [(c.id, v, {"page": c.page}) for c, v in zip(chunks, vecs)])

    _update(document_id, stage="Extracting statistics", progress=0.75)
    stats = extract_statistics(pages) + chart_stats

    with SessionLocal() as db:
        for model in (Chunk, Statistic, Anchor, CrossRef):
            db.execute(delete(model).where(model.document_id == document_id))
        db.add_all(chunks)
        db.add_all(Statistic(document_id=document_id, **s) for s in stats)
        db.add_all(Anchor(document_id=document_id, **a) for a in anchors)
        db.add_all(CrossRef(document_id=document_id, **x) for x in xrefs)
        doc = db.get(Document, document_id)
        doc.page_count = n
        doc.pages = [{"w": p.width, "h": p.height, "ocr": p.ocr} for p in pages]
        doc.ocr_pages = sum(p.ocr for p in pages)
        doc.language = document_language(pages)
        doc.status, doc.stage, doc.progress = "ready", "", 1.0
        db.commit()
    log.info("ingested %s: %d pages, %d chunks, %d stats", document_id, n, len(chunks), len(stats))


def _to_float(v) -> float | None:
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None
