"""Statistics extraction: deterministic number detection (text + tables) with optional Claude labelling."""
import re
from concurrent.futures import ThreadPoolExecutor

from . import bedrock
from .config import settings
from .pdf_extract import REF_RE, PageData, rects_for
from .textnorm import detect_language, western_digits

_CUR_PRE = r"US\$|\$|€|£|SAR|USD|EUR|AED|EGP|GBP|ر\.س\.?|﷼"
_UNITS = (
    r"%|percent|per cent|في المائة|في المئة|بالمائة|بالمئة|"
    r"trillion|billion|million|thousand|bn|mn|m|k|x|"
    r"مليون|مليار|ألف|آلاف|ريال|ر\.س|دولار|جنيه|درهم|يورو|SAR|USD|EUR|AED|EGP|"
    r"days?|hours?|hrs?|minutes?|mins?|seconds?|secs?|weeks?|months?|years?|"
    r"يوما|يوم|أيام|ساعة|ساعات|دقيقة|دقائق|ثانية|ثوان|أسبوع|أسابيع|شهرا|شهر|أشهر|سنة|سنوات|عام|أعوام|"
    r"points?|pts|bps|نقطة|نقاط|kg|km|m2|sqm|tons?|MW|GW|kWh|users?|customers?|employees?|"
    r"مستخدم|عميل|موظف|متر|كم|طن"
)
NUM_RE = re.compile(
    rf"(?<![\w.,/:-])(?P<pre>(?:{_CUR_PRE})\s?)?"
    r"(?P<num>[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[-+]?\d+(?:\.\d+)?)(?![\d/:]|\.\d|,\d)"
    rf"(?P<post>\s?(?:{_UNITS})(?![\w\u0600-\u06FF]))?",
    re.IGNORECASE,
)
_DATE = re.compile(r"\b\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}\b|\b\d{1,2}:\d{2}\b")
_PAGE_LINE = re.compile(r"^(?:page|الصفحة|صفحة)?\s*\d+\s*(?:(?:of|من|/)\s*\d+)?$", re.IGNORECASE)
_LIST_PREFIX = re.compile(r"^\s*[\(\[]?\d{1,2}(?:\.\d{1,2})*[\)\].-]\s")
_SENT_SPLIT = re.compile(r"(?<=[.!?؟;؛])\s+")
_SCALE = {"trillion": 1e12, "billion": 1e9, "bn": 1e9, "مليار": 1e9, "million": 1e6, "mn": 1e6, "m": 1e6,
          "مليون": 1e6, "thousand": 1e3, "k": 1e3, "ألف": 1e3, "آلاف": 1e3}
_CURRENCY = {"$", "us$", "€", "£", "sar", "usd", "eur", "aed", "egp", "gbp", "ر.س", "ر.س.", "﷼",
             "ريال", "دولار", "جنيه", "درهم", "يورو"}
_DURATION = re.compile(r"^(day|hour|hr|minute|min|second|sec|week|month|year|يوم|أيام|ساع|دقي|ثان|أسبوع|أسابيع|شهر|أشهر|سن|عام|أعوام)", re.I)


def _categorise(pre: str, post: str) -> str:
    pre, post = pre.strip().lower(), post.strip().lower()
    if post in ("%", "percent", "per cent") or "مائة" in post or "مئة" in post:
        return "percentage"
    if pre in _CURRENCY or post in _CURRENCY:
        return "currency"
    if post and _DURATION.match(post):
        return "duration"
    if post in ("x", "bps", "points", "point", "pts", "نقطة", "نقاط"):
        return "ratio"
    return "number"


def _value(num: str, post: str) -> float | None:
    try:
        v = float(num.replace(",", ""))
    except ValueError:
        return None
    return v * _SCALE.get(post.strip().lower(), 1)


def _is_noise(num: str, pre: str, post: str, sentence: str, start: int) -> bool:
    raw = num.replace(",", "").lstrip("+-")
    if pre or post:
        return False
    if raw.isdigit() and len(raw) >= 9:  # phone / ID numbers
        return True
    if raw.isdigit() and 1900 <= int(raw) <= 2100 and "," not in num:  # bare years
        return True
    if start <= 3 and _LIST_PREFIX.match(sentence):  # "3. Item", "(2) Item"
        return True
    return False


def _sentences(p: PageData):
    """Yield (sentence_text, [(line, line_offset_of_sentence_start)...]) per text block, skipping tables."""
    blocks: dict[int, list] = {}
    for ln in p.lines:
        if ln.skip or _PAGE_LINE.match(western_digits(ln.text.strip())):
            continue
        blocks.setdefault(ln.block, []).append(ln)
    for lines in blocks.values():
        text, spans = "", []
        for ln in lines:
            if text:
                text += " "
            spans.append((len(text), ln))
            text += ln.text
        pos = 0
        for sent in _SENT_SPLIT.split(text):
            start = text.find(sent, pos)
            pos = start + len(sent)
            yield sent, start, spans


def _words_for_span(p: PageData, spans, a: int, b: int) -> list[int]:
    out = []
    for off, ln in spans:
        if off > b:
            break
        if off + len(ln.text) < a:
            continue
        out += p.line_word_span(ln, a - off, b - off)
    return out


def _in_table(p: PageData, rect: list[float]) -> bool:
    cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
    return any(t.rect[0] <= cx <= t.rect[2] and t.rect[1] <= cy <= t.rect[3] for t in p.tables)


def from_text(p: PageData) -> list[dict]:
    out = []
    for sent, s_off, spans in _sentences(p):
        w = western_digits(sent)  # 1:1 char mapping keeps offsets valid
        masked = _DATE.sub(lambda m: "#" * len(m.group()), w)
        for m in REF_RE.finditer(masked):  # "Table 3", "Section 4.2" are references, not statistics
            masked = masked[:m.start()] + "#" * (m.end() - m.start()) + masked[m.end():]
        for m in NUM_RE.finditer(masked):
            pre, num, post = m.group("pre") or "", m.group("num"), m.group("post") or ""
            if _is_noise(num, pre, post, w, m.start()):
                continue
            idx = _words_for_span(p, spans, s_off + m.start(), s_off + m.end())
            if not idx:
                continue
            rects = rects_for(p.words, idx)
            if _in_table(p, rects[0]):
                continue
            out.append({
                "page": p.number, "value": sent[m.start():m.end()].strip(), "numeric_value": _value(num, post),
                "unit": (pre or post).strip(), "category": _categorise(pre, post),
                "metric": _guess_metric(sent, m.start()), "context": sent.strip()[:1000],
                "source": "text", "rects": rects,
            })
    return out


def _guess_metric(sentence: str, start: int) -> str:
    before = re.sub(r"[\d.,%:()\[\]-]+", " ", sentence[:start]).split()
    return " ".join(before[-6:])[:120]


_CELL_NUM = re.compile(rf"^\s*\(?(?P<pre>(?:{_CUR_PRE})\s?)?(?P<num>[-+]?\d[\d,]*(?:\.\d+)?)\)?\s*(?P<post>{_UNITS})?\s*$", re.I)


def from_tables(p: PageData) -> list[dict]:
    out = []
    for t in p.tables:
        rows = [[western_digits(c) for c in r] for r in t.rows]
        ncols = max(len(r) for r in rows)
        # Label column = the one with the most non-numeric text (rightmost for RTL tables, leftmost for LTR).
        textiness = [sum(1 for r in rows[1:] if c < len(r) and r[c] and not _CELL_NUM.match(r[c])) for c in range(ncols)]
        label_col = max(range(ncols), key=lambda c: textiness[c])
        header = rows[0]
        for ri, r in enumerate(rows[1:], start=1):
            label = t.rows[ri][label_col] if label_col < len(r) else ""
            for ci, cell in enumerate(r):
                m = _CELL_NUM.match(cell) if cell else None
                if not m or ci == label_col:
                    continue
                pre, num, post = m.group("pre") or "", m.group("num"), m.group("post") or ""
                col = t.rows[0][ci] if ci < len(header) else ""
                rect = t.cells[ri][ci] if ci < len(t.cells[ri]) else None
                out.append({
                    "page": p.number, "value": t.rows[ri][ci], "numeric_value": _value(num, post),
                    "unit": (pre or post).strip(), "category": _categorise(pre, post),
                    "metric": " — ".join(x for x in (label, col) if x)[:200],
                    "context": " | ".join(c for c in t.rows[ri] if c)[:1000],
                    "source": "table", "rects": [rect] if rect else [t.rect],
                })
    return out


_LABEL_TOOL = {
    "name": "label_statistics",
    "description": "Label each numeric candidate extracted from a document.",
    "inputSchema": {"json": {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "keep": {"type": "boolean", "description": "true if this is a meaningful statistic/figure/KPI/amount; false for clause numbers, IDs, phone numbers, list numbering, page refs"},
                "metric": {"type": "string", "description": "Short label of what the number measures (max 8 words), in the sentence's language"},
                "category": {"type": "string", "enum": ["percentage", "currency", "count", "kpi", "ratio", "duration", "number"]},
            },
            "required": ["id", "keep", "metric", "category"],
        }}},
        "required": ["items"],
    }},
}
_LABEL_SYSTEM = (
    "You label numbers found in a document. For each item you get the sentence and the exact value. "
    "Decide whether it is a real statistic (amounts, percentages, KPIs, counts, targets, durations, scores) "
    "and give a concise metric name in the same language as the sentence. Never invent values."
)


def label_with_llm(items: list[dict]) -> list[dict]:
    if not settings.stats_llm or not items:
        return items
    batches = [items[i:i + 60] for i in range(0, len(items), 60)]

    def run(batch: list[dict]) -> list[dict]:
        listing = "\n".join(f'{i}. value="{s["value"]}" | sentence="{s["context"][:400]}"' for i, s in enumerate(batch))
        try:
            res = bedrock.converse_tool(settings.stats_model, _LABEL_SYSTEM,
                                        [{"role": "user", "content": [{"text": listing}]}], _LABEL_TOOL, max_tokens=8000)
        except Exception:
            return batch
        labels = {it["id"]: it for it in res.get("items", []) if isinstance(it, dict) and "id" in it}
        kept = []
        for i, s in enumerate(batch):
            lab = labels.get(i)
            if lab is None:
                kept.append(s)
                continue
            if not lab.get("keep", True):
                continue
            s["metric"] = (lab.get("metric") or s["metric"])[:200]
            if s["category"] == "number" and lab.get("category"):
                s["category"] = lab["category"]
            kept.append(s)
        return kept

    with ThreadPoolExecutor(max_workers=4) as pool:
        return [s for part in pool.map(run, batches) for s in part]


def extract_statistics(pages: list[PageData]) -> list[dict]:
    text_items = [s for p in pages for s in from_text(p)]
    table_items = [s for p in pages for s in from_tables(p)]
    return label_with_llm(text_items) + table_items


def document_language(pages: list[PageData]) -> str:
    sample = " ".join(p.text for p in pages[:20])
    return detect_language(sample)
