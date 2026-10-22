"""PDF -> words with normalised boxes, lines, tables, images, anchors and cross-references.

All rectangles are [x0, y0, x1, y1] in 0..1 page space (top-left origin) so the frontend can
scale them to whatever zoom level PDF.js renders at.
"""
import re
from dataclasses import dataclass, field

import pymupdf

from .config import settings
from .textnorm import normalize, repair_arabic_word, text_layer_is_broken, western_digits


@dataclass
class Word:
    text: str
    rect: list[float]
    line: int


@dataclass
class Line:
    id: int
    block: int
    words: list[int]  # indices into PageData.words
    text: str = ""
    starts: list[int] = field(default_factory=list)  # char offset of each word inside text
    skip: bool = False  # repeated header/footer


@dataclass
class Table:
    rect: list[float]
    rows: list[list[str]]
    cells: list[list[list[float] | None]]


@dataclass
class PageData:
    number: int
    width: float
    height: float
    ocr: bool
    repaired: bool
    words: list[Word]
    lines: list[Line]
    tables: list[Table]
    images: list[list[float]]

    @property
    def text(self) -> str:
        return "\n".join(l.text for l in self.lines)

    def line_word_span(self, line: Line, a: int, b: int) -> list[int]:
        """Word indices of `line` overlapping char span [a, b)."""
        out = []
        for wi, s in zip(line.words, line.starts):
            e = s + len(self.words[wi].text)
            if s < b and e > a:
                out.append(wi)
        return out


def rects_for(words: list[Word], idx: list[int]) -> list[list[float]]:
    """Merge word boxes into one box per line (what the user sees as a highlight bar)."""
    by_line: dict[int, list[float]] = {}
    order: list[int] = []
    for i in idx:
        w = words[i]
        r = by_line.get(w.line)
        if r is None:
            by_line[w.line] = list(w.rect)
            order.append(w.line)
        else:
            r[0], r[1], r[2], r[3] = min(r[0], w.rect[0]), min(r[1], w.rect[1]), max(r[2], w.rect[2]), max(r[3], w.rect[3])
    return [[round(v, 5) for v in by_line[l]] for l in order]


def _norm_rect(r: pymupdf.Rect, page: pymupdf.Page) -> list[float]:
    if page.rotation:
        r = r * page.rotation_matrix
    W, H = page.rect.width, page.rect.height
    return [max(0.0, r.x0 / W), max(0.0, r.y0 / H), min(1.0, r.x1 / W), min(1.0, r.y1 / H)]


def _image_rects(page: pymupdf.Page) -> list[list[float]]:
    out = []
    try:
        for info in page.get_image_info():
            r = pymupdf.Rect(info["bbox"]) & page.rect
            if not r.is_empty:
                out.append(_norm_rect(r, page))
    except Exception:
        pass
    return out


def _area(r: list[float]) -> float:
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def extract_page(page: pymupdf.Page) -> PageData:
    raw = page.get_text("words")
    images = _image_rects(page)
    chars = sum(len(w[4].strip()) for w in raw)
    coverage = sum(_area(r) for r in images)

    ocr = False
    broken = text_layer_is_broken([w[4] for w in raw])
    if (chars < 25 and (coverage > 0.3 or chars == 0)) or (broken and settings.ocr_broken_arabic):
        try:
            tp = page.get_textpage_ocr(language=settings.ocr_languages, dpi=settings.ocr_dpi, full=True)
            raw = page.get_text("words", textpage=tp)
            ocr = True
        except Exception:
            pass  # Tesseract missing: keep whatever text layer exists

    repaired = not ocr and broken

    words: list[Word] = []
    lines: list[Line] = []
    line_ids: dict[tuple[int, int], int] = {}
    for x0, y0, x1, y1, text, block, line, _ in raw:
        text = text.strip()
        if not text:
            continue
        if repaired:
            text = repair_arabic_word(text)
        key = (block, line)
        if key not in line_ids:
            line_ids[key] = len(lines)
            lines.append(Line(id=len(lines), block=block, words=[]))
        lid = line_ids[key]
        lines[lid].words.append(len(words))
        words.append(Word(text, _norm_rect(pymupdf.Rect(x0, y0, x1, y1), page), lid))

    for ln in lines:
        pos, parts = 0, []
        for wi in ln.words:
            ln.starts.append(pos)
            parts.append(words[wi].text)
            pos += len(words[wi].text) + 1
        ln.text = " ".join(parts)

    tables: list[Table] = []
    if not ocr and words:
        try:
            for t in page.find_tables().tables:
                cells = [[_norm_rect(pymupdf.Rect(c), page) if c else None for c in row.cells] for row in t.rows]
                # PyMuPDF's table text is visual-order for RTL; rebuild it from our logical-order words.
                rows = [[_text_in(words, c) if c else "" for c in row] for row in cells]
                if len(rows) >= 2 and len(rows[0]) >= 2:
                    tables.append(Table(_norm_rect(pymupdf.Rect(t.bbox), page), rows, cells))
        except Exception:
            pass

    return PageData(page.number + 1, page.rect.width, page.rect.height, ocr, repaired, words, lines, tables, images)


def _text_in(words: list[Word], r: list[float]) -> str:
    inside = [w for w in words
              if r[0] <= (w.rect[0] + w.rect[2]) / 2 <= r[2] and r[1] <= (w.rect[1] + w.rect[3]) / 2 <= r[3]]
    return " ".join(w.text for w in inside)


_DIGITS_RE = re.compile(r"[0-9\u0660-\u0669]+")


def mark_boilerplate(pages: list[PageData]) -> None:
    """Flag running headers/footers (same text, top/bottom band, on most pages) so they are not indexed."""
    if len(pages) < 4:
        return
    seen: dict[str, int] = {}
    keyed = []
    for p in pages:
        keys = set()
        for ln in p.lines:
            y = p.words[ln.words[0]].rect[1]
            if y < 0.15 or y > 0.88:
                k = _DIGITS_RE.sub("#", normalize(ln.text))
                keys.add(k)
                keyed.append((ln, k))
        for k in keys:
            seen[k] = seen.get(k, 0) + 1
    limit = max(3, int(len(pages) * 0.4))
    for ln, k in keyed:
        if seen.get(k, 0) >= limit or k in ("#", ""):
            ln.skip = True


# ---------------------------------------------------------------------------------------------
# Cross-references
# ---------------------------------------------------------------------------------------------
_KINDS = {
    "table": r"tables?|tab\.|جدول|الجدول|بالجدول|والجدول",
    "figure": r"figures?|figs?\.|charts?|graphs?|exhibits?|شكل|الشكل|بالشكل|والشكل|الرسم البياني|رسم بياني",
    "appendix": r"appendix|appendices|annex(?:es)?|attachments?|schedules?|ملحق|الملحق|بالملحق|والملحق|مرفق|المرفق|بالمرفق",
    "section": r"sections?|clauses?|chapters?|parts?|§|قسم|القسم|بالقسم|البند|بند|بالبند|الفصل|فصل|الفقرة|فقرة",
    "article": r"articles?|art\.|المادة|مادة|بالمادة",
}
_KIND_OF = {}
_alts = []
for kind, alt in _KINDS.items():
    for kw in alt.split("|"):
        _alts.append(kw)
        _KIND_OF[kw] = kind
_alts.sort(key=len, reverse=True)
_ID = r"(?P<id>[0-9٠-٩]+(?:[.\-][0-9٠-٩]+)*|[A-Z](?:[.\-]?[0-9]+)?(?![A-Za-z])|[\u0621-\u064A](?![\u0621-\u064A]))"
REF_RE = re.compile(
    rf"(?<![\w\u0600-\u06FF])(?P<kw>{'|'.join(_alts)})(?:\s*(?:رقم|no\.?|number|#))?[\s\(\[]+{_ID}\s*[\)\]]?",
    re.IGNORECASE,
)
_TOC = re.compile(r"(\.{4,}|…{2,}|_{4,}|·{4,})|\s\d{1,4}\s*$")
_NUM_HEADING = re.compile(r"^(?P<id>[0-9٠-٩]+(?:\.[0-9٠-٩]+){0,3})[.)]?\s+\S")
_ALEF_MAP = str.maketrans("أإآ", "ااا")


def _kind_for(kw: str) -> str:
    k = kw.lower()
    if k in _KIND_OF:
        return _KIND_OF[k]
    for alt, kind in _KIND_OF.items():
        if re.fullmatch(alt, k, re.IGNORECASE):
            return kind
    return "section"


def ref_key(kind: str, ident: str) -> str:
    return f"{kind}:{western_digits(ident).lower().translate(_ALEF_MAP).strip('.-')}"


def find_refs(text: str):
    """Yield (key, start, end) for every cross-reference mention in `text`."""
    for m in REF_RE.finditer(text):
        yield ref_key(_kind_for(m.group("kw")), m.group("id")), m.start(), m.end()


def anchors_and_xrefs(pages: list[PageData]):
    anchors: dict[str, dict] = {}
    mentions: list[dict] = []
    for p in pages:
        for ln in p.lines:
            if ln.skip:
                continue
            text = ln.text.strip()
            is_toc = bool(_TOC.search(text)) and len(text) > 8
            block_lines = sum(1 for l in p.lines if l.block == ln.block)

            m = _NUM_HEADING.match(text)
            if m and not is_toc and len(text) < 120 and ("." in m.group("id") or (block_lines == 1 and len(text) < 70)):
                key = ref_key("section", m.group("id"))
                anchors.setdefault(key, {"key": key, "label": text[:200], "page": p.number,
                                         "rects": rects_for(p.words, ln.words), "_s": 2})

            for key, a, b in find_refs(text):
                idx = p.line_word_span(ln, a, b)
                mention = ({"key": key, "text": text[a:b], "page": p.number, "rects": rects_for(p.words, idx)}
                           if idx else None)
                after = text[b:b + 2].strip()
                # Strong caption: "Table 3.3: …" / "جدول ٣ - …". Weak: a short line merely starting with
                # the reference ("Table 3.2 states …"). A later strong caption replaces an earlier weak one.
                strength = 0
                if a == 0 and not is_toc:
                    short = len(text) < 90
                    if not after or after[0] in ":-–—|" or (after[0] == "." and short):
                        strength = 2
                    elif short:
                        strength = 1
                cur = anchors.get(key)
                if strength and (cur is None or strength > cur["_s"]):
                    if cur and cur.get("_m"):
                        mentions.append(cur["_m"])
                    anchors[key] = {"key": key, "label": text[:200], "page": p.number,
                                    "rects": rects_for(p.words, ln.words), "_s": strength, "_m": mention}
                    continue
                if mention:
                    mentions.append(mention)

    for a in anchors.values():
        a.pop("_s", None)
        a.pop("_m", None)

    # Section/article mentions also resolve to plain numbered headings ("see 4.2" style docs).
    for m in mentions:
        if m["key"] not in anchors and m["key"].startswith(("article:", "section:")):
            alt = "section:" + m["key"].split(":", 1)[1]
            if alt in anchors:
                m["key"] = alt
    xrefs = [m for m in mentions if m["key"] in anchors
             and not (anchors[m["key"]]["page"] == m["page"] and anchors[m["key"]]["rects"][:1] == m["rects"][:1])]
    return list(anchors.values()), xrefs


# ---------------------------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------------------------
def chunk_page(p: PageData, heading: str, target: int = 900, hard_max: int = 1600):
    """Split a page into ~target-char chunks on block/line boundaries.

    Returns (chunks, last_heading) where each chunk is {"text", "heading", "word_idx"}.
    """
    chunks, cur_lines, cur_len, cur_block = [], [], 0, None

    def flush():
        nonlocal cur_lines, cur_len
        if cur_lines:
            text = "\n".join(l.text for l in cur_lines).strip()
            if len(normalize(text)) >= 3:
                chunks.append({"text": text, "heading": heading,
                               "word_idx": [wi for l in cur_lines for wi in l.words]})
        cur_lines, cur_len = [], 0

    for ln in p.lines:
        if ln.skip:
            continue
        t = ln.text.strip()
        if _NUM_HEADING.match(t) and len(t) < 120 and not _TOC.search(t):
            flush()
            heading = t[:160]
        new_block = cur_block is not None and ln.block != cur_block
        if cur_len >= hard_max or (new_block and cur_len >= target):
            flush()
        cur_lines.append(ln)
        cur_len += len(t) + 1
        cur_block = ln.block
    flush()
    return chunks, heading
