"""Text normalisation shared by extraction, matching and scoring (Arabic + English)."""
import re
import unicodedata

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬٪", "01234567890123456789.,%")
_TASHKEEL = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u0640]")
_ARABIC = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")
_LATIN = re.compile(r"[A-Za-z]")
_PUNCT = re.compile(r"[^\w%.\u0600-\u06FF]+", re.UNICODE)

# Many Arabic PDFs map lam-ligatures (لا, لم, لج, لح, لخ) in visual order, so the extracted text
# reads "املنافسة" instead of "المنافسة" or "االصطناعي" instead of "الاصطناعي".
_BROKEN_AL = re.compile(r"^([وبفكل]?)ا([اأإآمجحخ])ل")
_BROKEN_HINT = re.compile(r"^[وبفكل]?ا[اأإآ]ل")


def western_digits(s: str) -> str:
    return s.translate(_DIGITS)


def has_arabic(s: str) -> bool:
    return bool(_ARABIC.search(s))


def detect_language(text: str) -> str:
    ar, en = len(_ARABIC.findall(text)), len(_LATIN.findall(text))
    if ar + en == 0:
        return ""
    return "ar" if ar >= en else "en"


def text_layer_is_broken(words: list[str]) -> bool:
    arabic = [w for w in words if has_arabic(w) and len(w) > 3]
    if len(arabic) < 10:
        return False
    hard = sum(1 for w in arabic if _BROKEN_HINT.match(w))
    soft = sum(1 for w in arabic if _BROKEN_AL.match(w))
    return hard >= 1 or soft / len(arabic) > 0.02


def repair_arabic_word(w: str) -> str:
    return _BROKEN_AL.sub(r"\1ال\2", w)


def normalize(s: str) -> str:
    """Aggressive normalisation for matching only — never shown to users."""
    s = unicodedata.normalize("NFKC", s)
    s = western_digits(s)
    s = _TASHKEEL.sub("", s)
    s = re.sub("[أإآٱ]", "ا", s)
    s = s.replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي")
    s = _PUNCT.sub(" ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def tokens(s: str) -> list[str]:
    return [t.strip(".") for t in normalize(s).split() if t.strip(".")]


_NUM_IN_TEXT = re.compile(r"\d+(?:[.,]\d+)*")


def numbers_in(s: str) -> set[str]:
    """Numbers as canonical strings ("1,250.50" -> "1250.5") for claim-vs-source checks."""
    out = set()
    for m in _NUM_IN_TEXT.findall(western_digits(s)):
        raw = m.replace(",", "")
        try:
            v = float(raw)
        except ValueError:
            continue
        out.add(f"{v:g}")
    return out
