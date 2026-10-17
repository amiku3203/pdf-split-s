"""
nepali_text.py
--------------
Nepali-aware text extraction for the PDF Toolkit.

Why this exists
    Many Nepali textbooks are NOT real Unicode. They use legacy "ASCII-mapped"
    fonts (Preeti, Kantipur, ...) that *look* like Devanagari on screen but whose
    underlying characters are just Latin letters. A plain text extractor returns
    garbage for them (e.g. "g]kfn" instead of "नेपाल").

How a page is read (decided per page)
    1. clean-unicode : the text layer is already real Devanagari -> use it as is.
    2. font-map      : Preeti / Kantipur spans are converted to Unicode with the
                       `npttf2utf` package. Latin-font spans (page numbers, English
                       words) are kept as they are.
    3. ocr           : unknown legacy font, a failed conversion, or a scanned page
                       (no text layer) -> rasterize + Tesseract Nepali OCR.

    Chapters / lessons are then detected (PDF bookmarks first, then Nepali heading
    patterns like "एकाइ-एक" / "पाठ १" on the *converted* text), and one .txt
    (optionally also the matching .pdf) is written per lesson, in a folder per chapter.

System requirements (only for the OCR fallback)
    tesseract-ocr + the Nepali language data, e.g. on Debian/Ubuntu:
        sudo apt-get install tesseract-ocr tesseract-ocr-nep
    Without them, Preeti/Kantipur and clean-Unicode PDFs still work; only pages
    that need OCR are reported as unreadable.
"""

import csv
import io
import os
import re
import time
import unicodedata
from collections import Counter, defaultdict

import pymupdf
from pypdf import PdfReader, PdfWriter

import spellcheck
from pdf_splitter import _parse_page_ranges

try:
    import npttf2utf
    from npttf2utf import FontMapper
    # FontMapper needs the path of the map file that ships inside the package.
    _mapper = FontMapper(os.path.join(os.path.dirname(npttf2utf.__file__), "map.json"))
except Exception:  # package missing or map not loadable
    _mapper = None

try:
    import pytesseract
    from PIL import Image
except ImportError:
    pytesseract = None
    Image = None


# ============================================================ EXTRACTION ===

# Embedded font names look like "IKVGWX+Preeti" (subset tag + real name), so we
# match by substring, case-insensitive. npttf2utf also ships maps for a few more
# fonts (Sagarmatha, PCS Nepali, Fontasy Himali) - add them here once you have a
# real PDF to verify them against.
FONT_FAMILY_MAP = {
    "preeti": "Preeti",
    "kantipur": "Kantipur",
    # Preeti-compatible keyboards (seen in a real textbook: their lesson/unit numbers and
    # text decode correctly with the Preeti map)
    "aakar": "Preeti",
    "shangrila": "Preeti",
}

# Ordinary Latin fonts. Text in these (page numbers, English words) is kept as is
# instead of forcing the whole page into OCR.
LATIN_FONT_HINTS = (
    "arial", "times", "calibri", "helvetica", "cambria", "verdana", "tahoma",
    "georgia", "courier", "symbol", "wingding", "segoe", "garamond", "palatino",
    "bookman", "franklin", "century", "consolas", "lucida", "trebuchet",
    "myriad", "minion", "dejavu", "liberation",
)

_DEV_RE = re.compile(r"[\u0900-\u097F]")
CLEAN_RATIO_THRESHOLD = 0.15   # >= this share of Devanagari chars -> already Unicode
CONVERT_SANITY_RATIO = 0.5     # converted legacy text should be mostly Devanagari
OCR_DPI = 200


def _font_key(font_name: str) -> str:
    """'ABCDEF+Himalb' -> 'himalb' (drops the subset tag)."""
    return font_name.split("+")[-1].lower()


def classify_font(font_name: str, overrides: dict = None):
    """npttf2utf family name for a known legacy font, else None.
    `overrides` = maps auto-detected for this PDF (see _detect_font_maps)."""
    if overrides and _font_key(font_name) in overrides:
        return overrides[_font_key(font_name)]
    lname = font_name.lower()
    for key, family in FONT_FAMILY_MAP.items():
        if key in lname:
            return family
    return None


def _devanagari_ratio(text: str) -> float:
    non_space = [c for c in text if not c.isspace()]
    if not non_space:
        return 0.0
    return sum(1 for c in non_space if _DEV_RE.match(c)) / len(non_space)


def _classify_span(font: str, text: str, overrides: dict = None):
    """-> ("legacy", family) | ("keep", None) | ("unknown", None)"""
    family = classify_font(font, overrides)
    if family:
        return "legacy", family
    if _DEV_RE.search(text):                       # already real Devanagari
        return "keep", None
    if not re.search(r"[A-Za-z]", text):           # digits / punctuation only
        return "keep", None
    if any(h in font.lower() for h in LATIN_FONT_HINTS):
        return "keep", None
    return "unknown", None                         # probably an unmapped legacy font


AUTODETECT_MIN_WORDS = 8      # need at least this many words of a font to judge it
AUTODETECT_MIN_HIT = 0.5      # share of converted words that must be in the dictionary
AUTODETECT_SAMPLE_CHARS = 6000


def _detect_font_maps(doc) -> dict:
    """
    Many legacy fonts (Himalb, Aakriti, Ganess, ...) have no table of their own but use
    a keyboard layout that matches a known map. For every unknown font, convert a text
    sample with each available map and keep the one whose output is made of real
    dictionary words. -> {font_key: (map_name, hit_rate)}
    """
    if _mapper is None:
        return {}
    try:
        checker = _get_checker()
    except ValueError:
        return {}

    samples = defaultdict(list)
    sizes = defaultdict(int)
    for page in doc:
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text, font = span.get("text", ""), span.get("font", "")
                    if not text.strip() or classify_font(font) or sizes[font] >= AUTODETECT_SAMPLE_CHARS:
                        continue
                    if _classify_span(font, text)[0] != "unknown":
                        continue
                    samples[font].append(text)
                    sizes[font] += len(text)

    candidates = [m for m in _mapper.supported_maps if m.lower() != "unicode"]
    found = {}
    for font, texts in samples.items():
        sample = " ".join(texts)
        best_map, best_hit = None, 0.0
        for name in candidates:
            try:
                words = [w for w in spellcheck.tokenize(convert_run(sample, name)) if len(w) > 1]
            except Exception:
                continue
            if len(words) < AUTODETECT_MIN_WORDS:
                continue
            hit = sum(1 for w in words if checker.is_correct(w)) / len(words)
            if hit > best_hit:
                best_map, best_hit = name, hit
        if best_map and best_hit >= AUTODETECT_MIN_HIT:
            found[_font_key(font)] = (best_map, round(best_hit, 2))
    return found


# Control characters (e.g. \x03) that some legacy-font PDFs put *inside* words as zero-width
# spacer glyphs. They split words ("ने\x03पाल") and break the mapper's reordering rules.
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Raw-text corrections for quirks seen in real textbook PDFs, applied before conversion.
# (regex, replacement). Each one was found by comparing the converted text with the book.
_REPH = "\ue000"       # stands in for "र्" while the mapper runs (it would mangle the raw "¥")
_QUOTE = "\ue001"      # stands in for an opening "‘" (the mapper turns it into "ॅ": ‘सामाजिक -> ॅसामाजिक)
RAW_FIXUPS = (
    # "क्र" / "कृ" are stored as j|m / j[m; the plain map reads that as "व्र"/"वृ" plus a stray modifier
    (re.compile(r"j([|\[])m"), r"s\1"),
    # "Î" and "Œ" = "त्त्" (महत्त्वपूर्ण, तत्त्वहरू); the map reads Î as "फ्" and leaves Œ as it is
    (re.compile("[\u00ce\u0152]"), "Q\\\\"),
    # an opening curly quote glued to the letter before it is the chandrabindu:
    # साथीस“ग -> साथीसँग, कहा“ -> कहाँ  (a real opening quote has a space/bracket before it)
    (re.compile("(?<=[^\\s\u201c\u201d\u2018\u2019\"'(\\[{])\u201c"), "F"),
    # चन्द्रबिन्दु typed between O and {  (तपाईँले): the map only knows "O{"
    (re.compile("OF\\{"), "O{F"),
    # "¥" = the reph in "गर्यो", "पुर्‍याएको"
    (re.compile("\u00a5"), _REPH),
    # an opening single quote at the start of a word stays a quote
    (re.compile("(?<![^\\s(\\[{])\u2018"), _QUOTE),
)


def convert_run(text: str, family: str) -> str:
    if _mapper is None:
        raise RuntimeError("npttf2utf is not installed (pip install npttf2utf).")
    text = _CTRL_RE.sub("", text)
    for pattern, replacement in RAW_FIXUPS:
        text = pattern.sub(replacement, text)
    out = _mapper.map_to_unicode(text, from_font=family)
    out = out.replace(_REPH, "\u0930\u094d").replace(_QUOTE, "\u2018")
    # a halant straight before a vowel sign never occurs in real Devanagari (हुँदैन, not ह्ुँदैन):
    # some PDFs carry a hidden halant glyph there
    return re.sub("\u094d(?=[\u093e-\u094c\u0962\u0963])", "", out)


_OCR_STATE = {}


def _ocr_lang() -> str:
    """Checks (once) that Tesseract + Nepali data are available; returns the lang string."""
    if "v" not in _OCR_STATE:
        if pytesseract is None:
            _OCR_STATE["v"] = (None, "pytesseract/Pillow are not installed")
        else:
            try:
                langs = set(pytesseract.get_languages(config=""))
                if "nep" not in langs:
                    _OCR_STATE["v"] = (None, "Tesseract has no Nepali data (install tesseract-ocr-nep)")
                else:
                    _OCR_STATE["v"] = ("nep+eng" if "eng" in langs else "nep", None)
            except Exception:
                _OCR_STATE["v"] = (None, "Tesseract OCR is not installed on this server")
    lang, err = _OCR_STATE["v"]
    if err:
        raise RuntimeError(err)
    return lang


def ocr_page(page) -> str:
    lang = _ocr_lang()
    pix = page.get_pixmap(dpi=OCR_DPI)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(img, lang=lang)


_DEV_WORD = re.compile(r"^[\u0900-\u0963\u0970-\u097F]+$")
# a space right before a vowel sign / halant / anusvara can never be a real word break
_SPACE_BEFORE_MARK = re.compile(r"[ \t]+(?=[\u0900-\u0903\u093A-\u093C\u093E-\u094D])")


def _repair_split_words(line: str, checker) -> str:
    """
    Legacy-font PDFs often make the extractor insert spaces inside words
    ("प्रस् तुत", "प्रदे श"). Re-joins neighbouring pieces when the joined word is in
    the dictionary but the pieces were not all valid words on their own.
    """
    line = _SPACE_BEFORE_MARK.sub("", line)
    if checker is None:
        return line
    toks, out, i = line.split(" "), [], 0
    while i < len(toks):
        for k in (3, 2):
            group = toks[i:i + k]
            if len(group) == k and all(_DEV_WORD.match(g) for g in group):
                cand = "".join(group)
                if checker.is_correct(cand) and not all(checker.is_correct(g) for g in group):
                    out.append(cand)
                    i += k
                    break
        else:
            out.append(toks[i])
            i += 1
    return " ".join(out)


def _token_boxes(chars: list) -> list:
    """[(char, bbox)] of one line -> [(x0, x1)] for every whitespace-separated token."""
    out, cur = [], None
    for c, (x0, _y0, x1, _y1) in chars:
        if c.isspace():
            if cur:
                out.append(tuple(cur))
            cur = None
        elif cur is None:
            cur = [x0, x1]
        else:
            cur[0], cur[1] = min(cur[0], x0), max(cur[1], x1)
    if cur:
        out.append(tuple(cur))
    return out


def _read_text_layer(page, overrides: dict = None):
    """
    Walks blocks -> lines -> spans, converting legacy-font runs. Consecutive spans of
    the same font are merged before converting so multi-span words stay together.
    Returns (text, converted_legacy_text, needs_ocr, fonts_seen, unknown_fonts, boxes, tokens):
    `boxes` = one (x0, y0, x1, y1) per output line and `tokens` = for each line the x-range of
    every word on the page (or None if words could not be matched one to one) - used to show
    exactly where a word sits on the PDF page.
    """
    try:
        checker = _get_checker()
    except ValueError:
        checker = None
    blocks_out, legacy_parts, fonts_seen = [], [], set()
    boxes, tokens = [], []
    needs_ocr = False
    unknown_fonts = set()

    for block in page.get_text("rawdict").get("blocks", []):
        lines_out = []
        for line in block.get("lines", []):
            segs = []        # [kind, family, text, font]
            line_chars = []  # [(char, bbox)] of the raw text, same order as the seg texts
            for span in line.get("spans", []):
                chars = [(ch["c"], ch["bbox"]) for ch in span.get("chars", []) if not _CTRL_RE.match(ch["c"])]
                text = "".join(c for c, _ in chars)
                if "wingding" in span.get("font", "").lower() and text.strip():
                    text, chars = "\u2022", chars[:1]      # a Wingdings bullet glyph ("\uf073")
                if not text.strip():                       # a bare space between runs
                    if segs:
                        segs[-1][2] += text
                        line_chars.extend(chars)
                    continue
                font = span.get("font", "")
                fonts_seen.add(font)
                kind, family = _classify_span(font, text, overrides)
                line_chars.extend(chars)
                if segs and segs[-1][0] == kind and segs[-1][1] == family:
                    segs[-1][2] += text
                else:
                    segs.append([kind, family, text, font])

            parts = []
            for kind, family, text, font in segs:
                if kind == "legacy":
                    try:
                        converted = convert_run(text, family)
                        legacy_parts.append(converted)
                        parts.append(converted)
                    except Exception:
                        needs_ocr = True
                else:
                    if kind == "unknown":
                        needs_ocr = True
                        unknown_fonts.add(font.split("+")[-1])
                    parts.append(text)
            if parts:
                joined = re.sub(r"\s*\n\s*", " ", "".join(parts))     # a line must stay one line
                if any(seg[0] == "legacy" for seg in segs):
                    joined = _repair_split_words(joined, checker)
                if joined.strip():
                    lines_out.append(joined)
                    boxes.append(tuple(line.get("bbox", (0, 0, 0, 0))))
                    toks = _token_boxes(line_chars)
                    tokens.append(toks if len(toks) == len(joined.split()) else None)
        if lines_out:
            blocks_out.append("\n".join(lines_out))

    return "\n\n".join(blocks_out), "".join(legacy_parts), needs_ocr, fonts_seen, unknown_fonts, boxes, tokens


# Sequences that cannot occur in correct Nepali (or are known mis-conversions): the debug
# sample shows the raw text behind them, which is what's needed to add a new fixup.
_SUSPECT_RE = re.compile("[\u0904-\u0914][\u093E-\u094C\u0943]|वृषि|फ्रा|ग्रयो|प्रुया|तफ्व|महफ्व")


def _debug_dump(doc, page_numbers: list, overrides: dict, only=None) -> str:
    """Raw vs converted text per span for a few pages - to diagnose a font's mapping."""
    out = []
    for n in page_numbers:
        out.append(f"=== page {n} ===")
        for b_i, block in enumerate(doc[n - 1].get_text("dict").get("blocks", [])):
            for l_i, line in enumerate(block.get("lines", [])):
                for span in line.get("spans", []):
                    text, font = span.get("text", ""), span.get("font", "")
                    fam = classify_font(font, overrides)
                    conv = ""
                    if fam:
                        try:
                            conv = convert_run(text, fam)
                        except Exception as e:
                            conv = f"<convert error {e}>"
                    if only is not None and not only.search(conv):
                        continue
                    out.append(f"b{b_i} l{l_i} font={font} map={fam} size={span.get('size', 0):.1f}\n"
                               f"   raw ={text!r}\n   conv={conv!r}")
    return "\n".join(out)


def extract_pdf(pdf_path: str) -> dict:
    """
    -> {"pages": [{"page": 1, "text": "...", "method": "clean-unicode" | "font-map" |
                   "latin-text" | "ocr" | "blank" | "ocr-failed: <why>"}, ...],
        "font_report": {...}}
    """
    doc = pymupdf.open(pdf_path)
    pages_out, font_report = [], defaultdict(int)
    try:
        detected = _detect_font_maps(doc)
        overrides = {k: v[0] for k, v in detected.items()}
        for i, page in enumerate(doc):
            plain = page.get_text("text") or ""
            if _devanagari_ratio(plain) >= CLEAN_RATIO_THRESHOLD:
                pages_out.append({"page": i + 1, "text": plain, "method": "clean-unicode"})
                continue

            text, legacy_text, needs_ocr, fonts, unknown_fonts, boxes, tokens = _read_text_layer(page, overrides)
            why = ""
            if unknown_fonts:
                why = "unsupported font: " + ", ".join(sorted(unknown_fonts))
            elif needs_ocr:
                why = "font conversion failed"
            for f in fonts:
                font_report[classify_font(f, overrides) or f] += 1

            if not text.strip():
                # No text layer: a scanned page (has an image) or a genuinely blank one.
                if page.get_images():
                    needs_ocr = True
                    why = "scanned page (no text layer)"
                else:
                    pages_out.append({"page": i + 1, "text": "", "method": "blank"})
                    continue

            # A "known" legacy font whose conversion didn't give Devanagari = wrong map.
            if not needs_ocr and legacy_text and _devanagari_ratio(legacy_text) < CONVERT_SANITY_RATIO:
                needs_ocr = True
                why = "converted text wasn't Devanagari (wrong font map)"

            if needs_ocr:
                try:
                    pages_out.append({"page": i + 1, "text": ocr_page(page), "method": "ocr", "why": why})
                except Exception as e:
                    pages_out.append({"page": i + 1, "text": "", "method": f"ocr-failed: {e}"})
            else:
                pages_out.append({
                    "page": i + 1, "text": text,
                    "method": "font-map" if legacy_text else "latin-text",
                    "boxes": boxes,                       # where each text line sits on the page (PDF points)
                    "tokens": tokens,                     # x-range of every word in each line
                })
    finally:
        doc.close()
    debug = ""
    if detected:
        ov = {k: v[0] for k, v in detected.items()}
        sample = [p["page"] for p in pages_out if p["method"] in ("font-map", "ocr")][:2]
        suspect = [p["page"] for p in pages_out if _SUSPECT_RE.search(p["text"])][:6]
        try:
            d2 = pymupdf.open(pdf_path)
            debug = (f"auto-detected maps: {detected}\n\n" + _debug_dump(d2, sample, ov)
                     + "\n\n##### spans that still convert to suspicious text #####\n"
                     + _debug_dump(d2, suspect, ov, only=_SUSPECT_RE))
            d2.close()
        except Exception:
            debug = ""
    return {"pages": pages_out, "font_report": dict(font_report), "font_maps": detected, "debug": debug}


# ======================================================= CHAPTER DETECTION ===

_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff"), None)

CHAPTER_PATTERNS = [
    re.compile(r"^\s*(chapter|unit)\s+([0-9]+|[ivxlcdm]+)\b", re.IGNORECASE),
    re.compile(r"^\s*एकाइ[\s\-–—]*([०-९0-9]+|एक|दुई|तीन|चार|पाँच|छ|सात|आठ|नौ|दश)"),
    re.compile(r"^\s*इकाई\s*([०-९0-9]+)"),
]
LESSON_PATTERNS = [
    re.compile(r"^\s*(lesson)\s+([0-9]+)\b", re.IGNORECASE),
    re.compile(r"^\s*पाठ\s*([०-९0-9]+)"),
]
# Extend the two lists above for other conventions (e.g. "अध्याय").

TOC_SCAN_PAGES = 15   # pages this early that list >= 3 headings are a table of contents


def _norm(s: str) -> str:
    return unicodedata.normalize("NFC", s).translate(_INVISIBLE).strip()


def _matches(patterns, line: str) -> bool:
    return any(p.match(line) for p in patterns)


def _finish_ranges(chapters: list, total_pages: int) -> list:
    """Fills in end pages (1-indexed, inclusive) for chapters and their lessons."""
    chapters.sort(key=lambda c: c["start"])
    for i, ch in enumerate(chapters):
        nxt = chapters[i + 1]["start"] - 1 if i + 1 < len(chapters) else total_pages
        ch["end"] = max(nxt, ch["start"])
        lessons = sorted(
            (l for l in ch["lessons"] if ch["start"] <= l["start"] <= ch["end"]),
            key=lambda l: l["start"],
        )
        for j, ls in enumerate(lessons):
            lnxt = lessons[j + 1]["start"] - 1 if j + 1 < len(lessons) else ch["end"]
            ls["end"] = max(lnxt, ls["start"])
        ch["lessons"] = lessons
    return chapters


def _from_bookmarks(pdf_path: str, total_pages: int) -> list:
    """Level-1 bookmarks = chapters, level-2 = lessons (deeper levels are ignored)."""
    doc = pymupdf.open(pdf_path)
    try:
        toc = doc.get_toc()
    finally:
        doc.close()

    chapters = []
    for level, title, page in toc:
        if page < 1 or page > total_pages:
            continue
        title = _norm(title)
        if level == 1 or not chapters:
            chapters.append({"title": title, "start": page, "lessons": []})
        elif level == 2:
            chapters[-1]["lessons"].append({"title": title, "start": page})
    return _finish_ranges(chapters, total_pages) if chapters else []


def _from_headings(pages: list, total_pages: int) -> list:
    """pages: [(page_no, converted_text)]. Looks for headings in each page's first lines."""
    chapters = []
    last_lesson_title = None

    for page_no, text in pages:
        all_lines = [_norm(l) for l in text.splitlines() if l.strip()]
        if page_no <= TOC_SCAN_PAGES:
            heading_lines = sum(
                1 for l in all_lines if _matches(CHAPTER_PATTERNS + LESSON_PATTERNS, l)
            )
            if heading_lines >= 3:      # table of contents, not a real heading page
                continue

        chapter_line = lesson_line = None
        for line in all_lines[:4]:
            if chapter_line is None and _matches(CHAPTER_PATTERNS, line):
                chapter_line = line
            elif lesson_line is None and _matches(LESSON_PATTERNS, line):
                lesson_line = line

        # A running header repeats the same heading on every page - only the first
        # occurrence starts a new section.
        if chapter_line and (not chapters or chapters[-1]["title"] != chapter_line):
            chapters.append({"title": chapter_line, "start": page_no, "lessons": []})
            last_lesson_title = None
        if lesson_line and lesson_line != last_lesson_title:
            if not chapters:
                chapters.append({"title": "Lessons", "start": page_no, "lessons": []})
            chapters[-1]["lessons"].append({"title": lesson_line, "start": page_no})
            last_lesson_title = lesson_line

    return _finish_ranges(chapters, total_pages) if chapters else []


# ---- chapters from the printed table of contents (विषयसूची) ----------------------------
_DEVANAGARI_DIGITS = {ord(c): str(i) for i, c in enumerate("०१२३४५६७८९")}
_TOC_UNIT_RE = re.compile(r"^एकाइ[\s\-–—]*\S*")
_TOC_LESSON_RE = re.compile(r"^पाठ\s*[०-९0-9]+$")
_TOC_RANGE_RE = re.compile(r"^(.*?)\s*([०-९0-9]+)\s*[—–\-]\s*[०-९0-9]+$")
_TOC_PAGE_RE = re.compile(r"^(.*?)\s*([०-९0-9]+)$")
_TOC_NOISE = {"विषयसूची", "एकाइ", "विषयवस्तु", "पृष्ठसङ्ख्या", "पृष्ठसंख्या"}


def _num(text: str) -> int:
    return int(text.translate(_DEVANAGARI_DIGITS))


def _squash(text: str) -> str:
    """Letters, vowel signs and digits only - to compare a TOC title with a page heading."""
    text = unicodedata.normalize("NFC", text).translate(_INVISIBLE)
    return "".join(c for c in text if unicodedata.category(c)[0] in "LMN")


def _parse_toc(pages: list):
    """
    Reads the contents pages. Returns ([{"label","title","printed","lessons":[{"label",
    "title","printed"}]}], last_toc_page) or ([], 0). Printed page numbers are the numbers
    printed in the book, not PDF page numbers.
    """
    total = len(pages)
    texts = {n: t for n, t in pages}
    # headings that repeat on many pages (running headers) must not be read as titles
    repeats = Counter(_norm(l) for _, t in pages for l in t.splitlines()[:4] if l.strip())
    running = {l for l, c in repeats.items() if c >= max(5, total * 0.3)}

    start = next((n for n in range(1, min(total, TOC_SCAN_PAGES) + 1)
                  if any("विषयसूची" in _norm(l) for l in texts[n].splitlines()[:8])), None)
    if start is None:
        return [], 0

    tokens, last = [], start
    for n in range(start, min(total, start + 6) + 1):
        parts = [x.strip() for x in re.split(r"[\t\n]+", unicodedata.normalize("NFC", texts[n]).translate(_INVISIBLE))]
        parts = [x for x in parts if x and x not in running and x not in _TOC_NOISE
                 and not re.fullmatch(r"[0-9]+", x)]
        if n > start and not any(_TOC_UNIT_RE.match(x) or _TOC_LESSON_RE.match(x) for x in parts):
            break
        tokens += parts
        last = n

    units, cur, i = [], None, 0
    while i < len(tokens):
        t = tokens[i]
        if _TOC_UNIT_RE.match(t):
            title, printed = [], None
            i += 1
            while i < len(tokens) and not (_TOC_UNIT_RE.match(tokens[i]) or _TOC_LESSON_RE.match(tokens[i])):
                m = _TOC_RANGE_RE.match(tokens[i])
                i += 1
                if m:
                    if m.group(1):
                        title.append(m.group(1))
                    printed = _num(m.group(2))
                    break
                title.append(tokens[i - 1])
            cur = {"label": t, "title": " ".join(title), "printed": printed, "lessons": []}
            units.append(cur)
        elif _TOC_LESSON_RE.match(t) and cur is not None:
            title, printed = [], None
            i += 1
            while i < len(tokens) and not (_TOC_UNIT_RE.match(tokens[i]) or _TOC_LESSON_RE.match(tokens[i])):
                u = tokens[i]
                i += 1
                if re.fullmatch(r"[०-९0-9]+", u):
                    printed = _num(u)
                    break
                m = _TOC_PAGE_RE.match(u)
                if m and m.group(1):
                    title.append(m.group(1))
                    printed = _num(m.group(2))
                    break
                title.append(u)
            cur["lessons"].append({"label": t, "title": " ".join(title), "printed": printed})
        else:
            i += 1
    return [u for u in units if u["printed"] is not None], last


def _from_toc(pages: list, total_pages: int) -> list:
    """Units/lessons from the printed contents page, placed on PDF pages by finding the
    first lessons' titles in the body (that gives the printed-page -> PDF-page offset)."""
    units, toc_last = _parse_toc(pages)
    if len(units) < 2:
        return []
    texts = {n: t for n, t in pages}

    def heading_text(n):
        lines = [_norm(l) for l in texts[n].splitlines() if l.strip()]
        return _squash(" ".join(lines[:8]))

    entries = [(u["title"], u["printed"]) for u in units] + \
              [(l["title"], l["printed"]) for u in units for l in u["lessons"] if l["printed"] is not None]
    votes = Counter()
    for title, printed in entries:
        key = _squash(title)
        if len(key) < 4:
            continue
        for n in range(toc_last + 1, total_pages + 1):
            if key in heading_text(n):
                votes[n - printed] += 1
                break
    if not votes:
        return []
    offset, count = votes.most_common(1)[0]
    if count < 3 or count < 0.4 * sum(votes.values()):
        return []

    chapters = []
    for u in units:
        start = u["printed"] + offset
        if not 1 <= start <= total_pages:
            continue
        lessons = [{"title": f'{l["label"]} {l["title"]}'.strip(), "start": l["printed"] + offset}
                   for l in u["lessons"]
                   if l["printed"] is not None and 1 <= l["printed"] + offset <= total_pages]
        chapters.append({"title": f'{u["label"]} {u["title"]}'.strip(), "start": start, "lessons": lessons,
                         "offset": offset})
    return _finish_ranges(chapters, total_pages) if len(chapters) >= 2 else []


def detect_chapters(pdf_path: str, pages: list):
    """-> (chapters, method). `pages` = [(page_no, converted_text)]."""
    total = len(pages)
    chapters = _from_bookmarks(pdf_path, total)
    if chapters:
        return chapters, "PDF bookmarks"
    chapters = _from_toc(pages, total)
    if chapters:
        return chapters, "the printed contents page (विषयसूची)"
    chapters = _from_headings(pages, total)
    if chapters:
        return chapters, "Nepali heading patterns (एकाइ / पाठ)"
    return [], "none"


# ============================================================ OUTPUT ======

def _safe_name(text: str, fallback: str) -> str:
    """Filename-safe but keeps Devanagari letters AND their vowel signs/viramas
    (a plain `\\w` regex would silently strip those)."""
    text = _norm(text or "")
    kept = "".join(
        ch if (unicodedata.category(ch)[0] in "LMN" or ch in " -_") else " " for ch in text
    )
    kept = re.sub(r"\s+", "_", kept.strip()).strip("_-")
    return kept[:60] or fallback


_CHECKER = None
CUSTOM_WORDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dictionary", "custom_words.txt")


def _read_custom_words() -> set:
    try:
        with open(CUSTOM_WORDS_PATH, encoding="utf-8") as f:
            return {unicodedata.normalize("NFC", w.strip()) for w in f if w.strip()}
    except OSError:
        return set()


def add_custom_word(word: str) -> bool:
    """'Not a mistake' - remembers a word (a name, a place, a new term) for all future checks."""
    word = unicodedata.normalize("NFC", (word or "").translate(_INVISIBLE)).strip()
    if not word or not _DEV_WORD.match(word):
        return False
    if word not in _read_custom_words():
        with open(CUSTOM_WORDS_PATH, "a", encoding="utf-8") as f:
            f.write(word + "\n")
    if _CHECKER is not None:
        _CHECKER.dictionary.add(word)
    return True


def _get_checker():
    """Loads the Nepali dictionary once (about 123k words)."""
    global _CHECKER
    if _CHECKER is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dictionary", "nepali_words.txt")
        if not os.path.exists(path):
            raise ValueError("Spell check needs dictionary/nepali_words.txt, but the file is missing.")
        hunspell = spellcheck.load_hunspell(os.path.join(os.path.dirname(path), "ne_NP"))
        _CHECKER = spellcheck.SpellChecker(spellcheck.load_dictionary(path) | _read_custom_words(), hunspell=hunspell)
    return _CHECKER


# Words after these are a person's name ("श्री टीकाराम गौतम", "डा. दिपिका शर्मा"): not spell-checked.
HONORIFICS = {"श्री", "श्रीमती", "सुश्री", "श्रीमान्", "श्रीमान", "डा", "प्रा", "स्व", "कुमारी"}
NAME_WORDS_AFTER_HONORIFIC = 3

CONTEXT_CHARS = 45          # characters of the line shown on each side of a wrong word
SUGGEST_BUDGET_SECONDS = 40  # the slow 2-edit search is skipped once this is used up


def _visual_rows(boxes: list, lines: list) -> list:
    """
    Row number (1 = topmost) of every line, the way a person counts lines on the printed page:
    a speaker label ("शिक्षक :") and its sentence sit on one row, and the big unit digit or a
    lone page number is not a line of text. Returns one int per line (0 = not counted).
    """
    order = sorted((i for i in range(len(boxes)) if len(lines[i].strip()) > 1),
                   key=lambda i: ((boxes[i][1] + boxes[i][3]) / 2, boxes[i][0]))
    rows = [0] * len(boxes)
    row_no, cur_y, cur_h = 0, None, None
    for i in order:
        y0, y1 = boxes[i][1], boxes[i][3]
        yc, h = (y0 + y1) / 2, max(y1 - y0, 1.0)
        if cur_y is None or abs(yc - cur_y) > 0.5 * min(h, cur_h):
            row_no += 1
            cur_y, cur_h = yc, h
        rows[i] = row_no
    return rows


def find_spelling_issues(plan: list, page_text: dict, page_boxes: dict = None,
                         printed_offset: int = None, page_tokens: dict = None) -> dict:
    """
    Every word the dictionary doesn't know, with where it is and what it probably should be.

    plan      : [{"start","end","group","title"}, ...]  (chapter/lesson page ranges)
    page_text : {page_no: converted text}
    page_boxes: {page_no: [bbox per non-empty line]} - lets us give the visual line number and
                a box to highlight on the PDF page
    printed_offset : PDF page - printed page number (e.g. 8: PDF page 10 is printed page 2)
    -> {"words_checked", "mistake_count", "unique_mistakes", "items": [
          {"unit", "lesson", "page", "line", "word", "suggestions", "before", "after"}, ...]}
       `line` = line counted from the top of the page as printed; `box` = [x0,y0,x1,y1] of that line
       on the PDF page (points); `wx` = where the word sits inside the line (0..1);
       `before`/`after` = the text around the word.
    """
    checker = _get_checker()
    entry_of = {}
    for entry in plan:
        for p in range(entry["start"], entry["end"] + 1):
            entry_of.setdefault(p, entry)

    ok_cache, doc_freq, found = {}, Counter(), []
    words_checked = 0
    for page in sorted(page_text):
        lines = [l for l in page_text[page].translate(_INVISIBLE).splitlines() if l.strip()]
        boxes = (page_boxes or {}).get(page)
        if boxes is not None and len(boxes) != len(lines):
            boxes = None                         # never show a wrong position: fall back to plain numbering
        rows = _visual_rows(boxes, lines) if boxes else None
        toks = (page_tokens or {}).get(page)
        if toks is not None and len(toks) != len(lines):
            toks = None
        for idx, raw_line in enumerate(lines):
            line_no = rows[idx] if rows else idx + 1
            line = unicodedata.normalize("NFC", raw_line)
            name_left, prev_end = 0, 0
            for m in spellcheck._DEV_WORD_RE.finditer(line):
                word = m.group()
                words_checked += 1
                # a name continues only while words are separated by spaces / full stops
                if name_left and line[prev_end:m.start()].strip(" .") != "":
                    name_left = 0
                prev_end = m.end()
                if word in HONORIFICS:
                    name_left = NAME_WORDS_AFTER_HONORIFIC
                    continue
                # a name at the start of a line followed by a colon is a speaker label
                # ("मतिना : गुरुआमा, ...") - not spell-checked
                if m.start() < 25 and re.match(r"\s*[:\uff1a]", line[m.end():m.end() + 3]):
                    continue
                if name_left:
                    name_left -= 1
                    continue
                if len(word) <= 1:
                    continue
                if word not in ok_cache:
                    ok_cache[word] = checker.is_correct(word)
                if ok_cache[word]:
                    doc_freq[word] += 1
                else:
                    found.append((page, line_no, word, line, m.start(), m.end(),
                                  boxes[idx] if boxes else None, toks[idx] if toks else None))

    suggester = spellcheck.Suggester(checker, doc_freq)
    suggester.deadline = time.time() + SUGGEST_BUDGET_SECONDS
    suggestions = {}
    items = []
    for page, line_no, word, line, start, end, box, tok in found:
        if word not in suggestions:
            suggestions[word] = suggester.suggest(word)
        entry = entry_of.get(page) or {}
        before = line[max(0, start - CONTEXT_CHARS):start]
        after = line[end:end + CONTEXT_CHARS]
        items.append({
            "likely": bool(suggestions[word]),      # looks like a slip of a known word (else: name / unknown)
            "unit": entry.get("group"),
            "lesson": entry.get("title"),
            "page": page,
            "printed": (page - printed_offset) if printed_offset and page > printed_offset else None,
            "line": line_no,
            "box": [round(v, 1) for v in box] if box else None,
            "wx": _word_span(line, start, end, box, tok) if box else None,
            "word": word,
            "suggestions": suggestions[word],
            "before": ("\u2026" if start > CONTEXT_CHARS else "") + before,
            "after": after + ("\u2026" if len(line) - end > CONTEXT_CHARS else ""),
        })
    likely = [it for it in items if it["likely"]]
    return {
        "words_checked": words_checked,
        "mistake_count": len(items),
        "unique_mistakes": len(suggestions),
        "likely_count": len(likely),
        "likely_unique": len({it["word"] for it in likely}),
        "items": items,
    }


def _word_span(line: str, start: int, end: int, box, tok) -> list:
    """Where the word sits inside its line box, as fractions 0..1 of the box width. With the
    on-page x-range of each word it is exact; otherwise it is estimated from character counts."""
    width = max(box[2] - box[0], 1e-6)
    chunks = list(re.finditer(r"\S+", line))
    for k, ch in enumerate(chunks):
        if tok and k < len(tok) and ch.start() <= start < ch.end():
            tx0, tx1 = tok[k]
            span = max(ch.end() - ch.start(), 1)
            x0 = tx0 + (start - ch.start()) / span * (tx1 - tx0)
            x1 = tx0 + (min(end, ch.end()) - ch.start()) / span * (tx1 - tx0)
            return [round(max(0.0, (x0 - box[0]) / width), 4), round(min(1.0, (x1 - box[0]) / width), 4)]
    n = max(len(line), 1)
    return [round(start / n, 3), round(end / n, 3)]


def write_spelling_csv(findings: dict, path: str) -> None:
    """Same data as the on-screen list; UTF-8 with BOM so Excel shows Devanagari correctly."""
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Unit", "Lesson", "PDF page", "Printed page", "Line (from top)", "Wrong word",
                    "Should probably be", "Context"])
        for it in findings["items"]:
            w.writerow([it["unit"] or "", it["lesson"] or "", it["page"], it.get("printed") or "", it["line"], it["word"],
                        " / ".join(it["suggestions"]), f'{it["before"]}[{it["word"]}]{it["after"]}'])


def _fmt_pages(nums: list, limit: int = 12) -> str:
    shown = ", ".join(str(n) for n in nums[:limit])
    return shown + (f" … (+{len(nums) - limit} more)" if len(nums) > limit else "")


def _build_plan(chapters: list, total_pages: int) -> list:
    """Turns the chapter/lesson tree into a flat list of files to write."""
    plan = []
    if chapters[0]["start"] > 1:
        plan.append({"folder": None, "name": "00_Front_Matter", "title": "Front Matter",
                     "start": 1, "end": chapters[0]["start"] - 1, "group": None})

    for c_idx, ch in enumerate(chapters, start=1):
        folder = f"{c_idx:02d}_{_safe_name(ch['title'], f'Chapter_{c_idx}')}"
        if not ch["lessons"]:
            plan.append({"folder": folder, "name": _safe_name(ch["title"], f"Chapter_{c_idx}"),
                         "title": ch["title"], "start": ch["start"], "end": ch["end"],
                         "group": ch["title"]})
            continue
        for l_idx, ls in enumerate(ch["lessons"], start=1):
            # the first lesson also takes the chapter-opener pages before it
            start = ch["start"] if l_idx == 1 else ls["start"]
            plan.append({"folder": folder,
                         "name": f"{l_idx:02d}_{_safe_name(ls['title'], f'Lesson_{l_idx}')}",
                         "title": ls["title"], "start": start, "end": ls["end"],
                         "group": ch["title"]})
    return plan


def extract_nepali_text_hierarchy(input_path: str, output_dir: str,
                                  ranges_str: str = "", with_pdf: bool = False,
                                  check_spelling: bool = False, findings_out: dict = None):
    """
    Reads a Nepali PDF (any of: Unicode / Preeti-Kantipur / scanned) and writes one
    .txt per lesson inside a folder per chapter:

        01_एकाइ-एक/
            01_पाठ_१_विकास_र_नेपाल.txt
            02_पाठ_२_विकासका_पूर्वाधार.txt

    ranges_str : optional "1-5, 6-10" - skips chapter detection and writes one file
                 per page range instead (use when headings aren't detected).
    with_pdf   : also write the matching PDF pages next to every .txt.
    check_spelling : dictionary-check every word. The findings (page, line, unit, lesson, wrong
                 word, suggestions) are put in findings_out["spellcheck"] for the UI, and also
                 written to Spellcheck_Report.csv.
    findings_out : optional dict that receives the spell-check findings.

    Returns (results, notice): `results` has the same shape as the other split_*
    functions; `notice` is a human-readable heads-up (OCR pages, unreadable pages…).
    """
    os.makedirs(output_dir, exist_ok=True)

    try:
        extracted = extract_pdf(input_path)
    except Exception as e:
        raise ValueError(f"Could not read that PDF: {e}")

    pages = extracted["pages"]
    total = len(pages)
    page_text = {p["page"]: p["text"] for p in pages}
    page_boxes = {p["page"]: p["boxes"] for p in pages if p.get("boxes") is not None}
    page_tokens = {p["page"]: p["tokens"] for p in pages if p.get("tokens") is not None}
    printed_offset = None
    notes = []

    failed = [p for p in pages if p["method"].startswith("ocr-failed")]
    if not any(t.strip() for t in page_text.values()):
        why = failed[0]["method"].split(": ", 1)[1] if failed else "no text found"
        raise ValueError(f"No text could be read from this PDF ({why}).")

    if ranges_str.strip():
        plan = [{"folder": None, "name": f"Pages_{s + 1}-{e}", "title": f"Pages {s + 1}\u2013{e}",
                 "start": s + 1, "end": e, "group": None}
                for s, e in _parse_page_ranges(ranges_str, total)]
    else:
        chapters, method = detect_chapters(input_path, [(p["page"], p["text"]) for p in pages])
        if chapters:
            printed_offset = chapters[0].get("offset")
            plan = _build_plan(chapters, total)
            notes.append(f"Chapters/lessons detected from: {method}.")
        else:
            plan = [{"folder": None, "name": "Full_Text", "title": "Full text (no chapters detected)",
                     "start": 1, "end": total, "group": None}]
            notes.append("No chapter/lesson headings were found, so everything is in one file — "
                         "enter page ranges (e.g. 1-12, 13-30) to split it by hand.")

    reader = PdfReader(input_path) if with_pdf else None
    results = []
    for entry in plan:
        folder_path = os.path.join(output_dir, entry["folder"]) if entry["folder"] else output_dir
        os.makedirs(folder_path, exist_ok=True)
        rel = f"{entry['folder']}/" if entry["folder"] else ""
        pages_label = f"{entry['start']}-{entry['end']}"

        body = "\n\n".join(page_text[p] for p in range(entry["start"], entry["end"] + 1)).strip()
        with open(os.path.join(folder_path, entry["name"] + ".txt"), "w", encoding="utf-8") as f:
            f.write(body)
        results.append({"title": entry["title"], "filename": f"{rel}{entry['name']}.txt",
                        "pages": pages_label, "group": entry["group"]})

        if with_pdf:
            writer = PdfWriter()
            for p in range(entry["start"] - 1, entry["end"]):
                writer.add_page(reader.pages[p])
            with open(os.path.join(folder_path, entry["name"] + ".pdf"), "wb") as f:
                writer.write(f)
            results.append({"title": entry["title"], "filename": f"{rel}{entry['name']}.pdf",
                            "pages": pages_label, "group": entry["group"]})

    if check_spelling:
        findings = find_spelling_issues(plan, page_text, page_boxes, printed_offset, page_tokens)
        write_spelling_csv(findings, os.path.join(output_dir, "Spellcheck_Report.csv"))
        results.insert(0, {"title": "Spell-check report (CSV)", "filename": "Spellcheck_Report.csv",
                           "pages": f"1-{total}", "group": None})
        if findings_out is not None:
            findings_out["spellcheck"] = findings

    if extracted.get("debug"):
        with open(os.path.join(output_dir, "Debug_Sample.txt"), "w", encoding="utf-8") as f:
            f.write(extracted["debug"])
        results.append({"title": "Debug sample (send this to the developer)",
                        "filename": "Debug_Sample.txt", "pages": "first 3", "group": None})

    if extracted.get("font_maps"):
        notes.append("Auto-matched fonts: " + ", ".join(
            f"{k} → {m} ({int(h * 100)}% dictionary match)" for k, (m, h) in extracted["font_maps"].items()) + ".")

    ocr_pages = [p["page"] for p in pages if p["method"] == "ocr"]
    if ocr_pages:
        notes.append(f"{len(ocr_pages)} page(s) were read with OCR (less exact than font conversion — "
                     f"worth proofreading): {_fmt_pages(ocr_pages)}.")
        reasons = {}
        for p in pages:
            if p["method"] == "ocr":
                k = p.get("why") or "unknown"
                reasons[k] = reasons.get(k, 0) + 1
        notes.append("Why OCR: " + "; ".join(f"{k} ({v} pages)" for k, v in reasons.items()) + ".")
    if failed:
        why = failed[0]["method"].split(": ", 1)[1]
        notes.append(f"{len(failed)} page(s) could not be read ({why}): "
                     f"{_fmt_pages([p['page'] for p in failed])}.")

    return results, " ".join(notes)
