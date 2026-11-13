#!/usr/bin/env python3
"""
scanned_pdf_to_telugu_pdf.py — Read a SCANNED-images PDF and build a
Telugu PDF out of it.

Pipeline, one page at a time:
    scanned PDF page (photo)
      -> OCR with tesseract (Devanagari, Sanskrit+Hindi)
      -> transliterate to Telugu script, and/or translate to Telugu
      -> Telugu PDF (A4, Telugu font embedded)

The clearest example of why this exists: a scanned book has no text
inside it, so a plain text reader only finds the digitizer's footer
stamp on each page ("Digitized by eGangotri") and nothing else. OCR
reads the photographed letters instead.

Modes (--mode, or the MODE setting below):
  * both (default):             Telugu lipi first (the same words, written
                                in Telugu script), then "అనువాదం —" + the
                                Telugu translation under it.
  * translate:                  the meaning in Telugu only.
  * transliterate:              the same words in Telugu script only.

What you need installed (once):
  * Python packages:  pip install indic-transliteration python-docx pymupdf
                                  reportlab deep-translator
                      (deep-translator is only needed for translate/both)
  * Tesseract:  Windows — install from
                      https://github.com/UB-Mannheim/tesseract/wiki
                      and during setup tick Sanskrit + Hindi language data,
                      and tick "Add to PATH" (or set TESSERACT_CMD below).
                Linux — apt-get install tesseract-ocr tesseract-ocr-san
                        tesseract-ocr-hin
  * A Telugu font for the PDF:
      - Put NotoSansTelugu-Regular.ttf and NotoSansTelugu-Bold.ttf in a
        "fonts" folder next to this script (download from Google Fonts), or
      - on Windows the script will use C:\\Windows\\Fonts\\NirmalaUI.ttf
        (Nirmala UI, ships with Windows, covers Telugu), or
      - set FONT_DIR below to the folder that holds the .ttf files.
    Without a Telugu font the PDF cannot be built — the script stops and
    tells you, instead of printing empty boxes.

Start small: run it on 2 pages first (--pages 1-2), check the result,
then run the whole book. The work is saved as it goes (see checkpoints),
so stopping and re-running does not redo finished pages.

Usage:
    python scanned_pdf_to_telugu_pdf.py book.pdf --check   # setup only
    python scanned_pdf_to_telugu_pdf.py book.pdf
    python scanned_pdf_to_telugu_pdf.py book.pdf --pages 1-2 --mode translate
    python scanned_pdf_to_telugu_pdf.py book.pdf --pages 1-97 -o book_telugu.pdf
    python scanned_pdf_to_telugu_pdf.py book.pdf --resume
"""

import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------- settings
# Fill these in if you would rather not type them on the command line.
# Windows example:  INPUT_PDF = r"C:\Users\ishar\Downloads\book.pdf"
INPUT_PDF = r"C:\Users\ishar\Downloads\Telegram Desktop\వాంఛాకల్పలతాదేవనాగరం.pdf"
OUTPUT_PDF = r"F:\ConvertedPDFs\వాంఛాకల్పలతాదేవనాగరంTelugu.docx"         # leave "" for <name>_telugu.pdf
MODE = "both"          # "both" = Telugu lipi + Telugu translation (default),
                         # "translate" = translation only,
                         # "transliterate" = Telugu lipi only
SOURCE_LANG = "sa"       # OCR text language for translation: "sa" = Sanskrit,
                         # "hi" = Hindi. (Only used for translate/both.)
MYMEMORY_EMAIL = "isharatbabu@gmaol.com"      # your email address, e.g. "you@example.com".
                         # Optional but worth setting: MyMemory's free
                         # service allows about 5,000 characters a day
                         # anonymously, but about 50,000 a day once an
                         # email is sent with the request. No account or
                         # password — just your email address.
PAGES = ""               # e.g. "1-10", or "" for all pages
DPI = 300                # OCR resolution; 300 is right for printed books
TESSERACT_CMD = "tesseract"  # full path if tesseract is not on PATH
FONT_DIR = ""            # folder holding NotoSansTelugu-Regular/Bold.ttf

# Lines that are only the digitizer's stamp, not book content. They are
# dropped (and counted, so you can see how many went). Add your own
# patterns here if your book has other repeated stamps.
FOOTER_PATTERNS = [r"eGangotri", r"Dharmartha Trust"]

DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "౦౧౨౩౪౫౬౭౮౯")

try:
    from indic_transliteration import sanscript
except ImportError:
    sanscript = None


# ---------------------------------------------------------------- OCR

def _crop_to_page(png_bytes: bytes) -> bytes:
    """Crop a rendered page photo to the light page area.

    Books photographed on a dark surface have most of the frame as
    empty dark background, and tesseract then finds no text at all
    (it read 0 characters from one such full photo, and ~734 from the
    same page cropped to the page). This finds the bright page paper
    and crops to it, with a small margin. If the page cannot be told
    apart from the background, or already fills the frame, the image
    is returned unchanged. Pillow is only needed for this step; if it
    is missing, cropping is skipped.
    """
    try:
        from PIL import Image
    except ImportError:
        return png_bytes
    try:
        img = Image.open(io.BytesIO(png_bytes)).convert("L")
    except Exception:
        return png_bytes
    # Work on a small copy for speed; a row counts as "page" when at
    # least 30% of it is bright paper. Columns are then judged only
    # within those rows (the page is often a short strip of the frame,
    # so a column judged over the whole height would never qualify).
    tw, th = 120, 160
    small = img.resize((tw, th))
    px = small.load()
    bright = [[1 if px[x, y] > 100 else 0 for x in range(tw)] for y in range(th)]
    rows = [y for y in range(th) if sum(bright[y]) / tw > 0.30]
    if not rows:
        return png_bytes
    y_lo, y_hi = min(rows), max(rows)
    span = y_hi - y_lo + 1
    cols = [x for x in range(tw)
            if sum(bright[y][x] for y in range(y_lo, y_hi + 1)) / span > 0.30]
    if not cols:
        return png_bytes
    W, H = img.size
    pad_x, pad_y = int(0.02 * W), int(0.02 * H)
    x0 = max(0, int(min(cols) / tw * W) - pad_x)
    x1 = min(W, int((max(cols) + 1) / tw * W) + pad_x)
    y0 = max(0, int(min(rows) / th * H) - pad_y)
    y1 = min(H, int((max(rows) + 1) / th * H) + pad_y)
    # If the page already fills (almost) the whole frame, cropping
    # changes nothing — keep the original.
    if (x1 - x0) * (y1 - y0) > 0.85 * W * H:
        return png_bytes
    try:
        crop = Image.open(io.BytesIO(png_bytes)).convert("RGB").crop((x0, y0, x1, y1))
        out = io.BytesIO()
        crop.save(out, "PNG")
        return out.getvalue()
    except Exception:
        return png_bytes


def ocr_page(page, dpi: int = DPI, lang: str = "san+hin",
             tesseract: str = TESSERACT_CMD) -> str:
    """Render one PDF page as an image and read its Devanagari with OCR."""
    if shutil.which(tesseract) is None:
        raise RuntimeError(
            "OCR needs the 'tesseract' program, which was not found. "
            "Windows: install it from "
            "https://github.com/UB-Mannheim/tesseract/wiki (tick Sanskrit "
            "and Hindi during setup, and tick 'Add to PATH'), or set "
            "TESSERACT_CMD to its full path. Linux: apt-get install "
            "tesseract-ocr tesseract-ocr-san tesseract-ocr-hin."
        )
    png = page.get_pixmap(dpi=dpi).tobytes("png")
    png = _crop_to_page(png)
    result = subprocess.run(
        [tesseract, "stdin", "stdout", "--psm", "6", "-l", lang],
        input=png, capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "tesseract failed: " + result.stderr.decode("utf-8", "replace")[:200]
        )
    return result.stdout.decode("utf-8", "replace")


def clean_ocr_text(text: str) -> tuple[str, int]:
    """Drop footer-stamp lines and collapse blank lines. Returns
    (cleaned text, number of footer lines dropped)."""
    dropped = 0
    kept = []
    for line in text.splitlines():
        if any(re.search(p, line) for p in FOOTER_PATTERNS):
            dropped += 1
            continue
        kept.append(line.rstrip())
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return cleaned, dropped


# ---------------------------------------------------------------- convert

def transliterate(text: str) -> str:
    """Devanagari -> Telugu script. Words unchanged, script changed."""
    if sanscript is None:
        raise RuntimeError(
            "indic-transliteration is not installed. Run:\n"
            "    pip install indic-transliteration"
        )
    out = sanscript.transliterate(text, sanscript.DEVANAGARI, sanscript.TELUGU)
    out = out.translate(DEVANAGARI_DIGITS)
    # sanscript passes Devanagari dandas through unchanged — that is what we
    # want: Telugu fonts have no Telugu dandas (U+0C64/U+0C65), so they
    # would print as empty boxes. Devanagari dandas print fine.
    out = out.replace("\u0c64", "।").replace("\u0c65", "॥")
    return out


def _split_for_translation(text: str, limit: int = 4000) -> list[str]:
    parts = re.split(r"(?<=[।.!?])\s+", text)
    chunks, current = [], ""
    for part in parts:
        if len(current) + len(part) + 1 > limit and current:
            chunks.append(current)
            current = part
        else:
            current = f"{current} {part}".strip() if current else part
    if current:
        chunks.append(current)
    return chunks


def _is_echo(translated: str, original: str) -> bool:
    """True if a "translation" is just the source text in Telugu script.

    MyMemory's Sanskrit->Telugu engine often returns the input words
    transliterated instead of translated. That looks like Telugu text
    but adds nothing — and in "both" mode it would just repeat the
    Telugu-lipi line. Compare the translation with the transliteration
    of the source; if they match once spaces and punctuation are
    removed, it is an echo, not a translation.
    """
    try:
        telu = transliterate(original)
    except Exception:
        return False

    def norm(s: str) -> str:
        return "".join(ch for ch in s
                       if ch.strip() and ch not in "।॥.,;:!?()[]{}\"'\u2014-")

    a, b = norm(translated), norm(telu)
    return bool(a) and a == b


def translate(text: str, source: str = SOURCE_LANG) -> str:
    """OCR text -> Telugu translation.

    The free translation services throttle fast repeated calls: Google
    allows about 5 requests per second from one address, and MyMemory's
    free anonymous quota is small (a whole book blows past it in one
    run). So this sends a whole block's sentences in ONE batch request
    instead of one request per sentence, waits a full minute when the
    server says "too many requests", and only then falls back to
    MyMemory — which itself only accepts 500 characters at a time, so
    its text is re-chunked before sending.
    """
    import time

    try:
        from deep_translator import GoogleTranslator, MyMemoryTranslator
    except ImportError:
        raise RuntimeError(
            "deep-translator is not installed. Run:\n"
            "    pip install deep-translator"
        )
    google_src = {"sa": "sa", "hi": "hi"}.get(source, source)
    mymemory_src = {"sa": "sanskrit", "hi": "hindi"}.get(source, source)
    chunks = _split_for_translation(text)
    if not chunks:
        return ""

    def google_batch(items: list[str]) -> list[str]:
        return GoogleTranslator(source=google_src,
                                target="te").translate_batch(items)

    def mymemory_batch(items: list[str]) -> list[str]:
        translator = MyMemoryTranslator(
            source=mymemory_src, target="telugu",
            **({"email": MYMEMORY_EMAIL} if MYMEMORY_EMAIL else {}))
        out = []
        for item in items:
            # MyMemory rejects anything over 500 chars — send it in
            # small pieces, each in its own batch. Sleep between the
            # pieces too: translate_batch sends one request per piece,
            # and back-to-back pieces are what trips the rate limit.
            parts = _split_for_translation(item, limit=450)
            translated_parts = []
            for i, part in enumerate(parts):
                if i:
                    time.sleep(1)
                translated_parts.append(translator.translate(part))
            out.append(" ".join(translated_parts))
            time.sleep(0.5)
        return out

    # One batch request for all sentences. If Google throttles us, wait
    # once (30s) and retry; if it is still throttling, mark Google dead
    # for the REST of this run and use MyMemory from then on — waiting
    # three minutes per block would make a book take forever.
    result = None
    if not getattr(translate, "_google_dead", False):
        for wait in (0, 30):
            if wait:
                print(f"    translation throttled — waiting {wait}s ...",
                      flush=True)
                time.sleep(wait)
            try:
                result = google_batch(chunks)
                break
            except Exception:
                continue
        if result is None:
            translate._google_dead = True
            print("    Google Translate is throttling this run — using "
                  "MyMemory for the remaining blocks.", flush=True)
    if result is None:
        # MyMemory throttles by IP/quota too, and it often recovers
        # after a real wait. Retry with growing waits before giving up
        # on the block, instead of crashing the whole page on the
        # first TooManyRequests.
        last_exc = None
        for wait in (0, 60, 180):
            if wait:
                print(f"    MyMemory throttled — waiting {wait}s "
                      "before retrying this block ...", flush=True)
                time.sleep(wait)
            try:
                result = mymemory_batch(chunks)
                break
            except Exception as exc:
                last_exc = exc
                continue
        if result is None:
            raise RuntimeError(
                "Translation failed on both Google and MyMemory "
                f"({last_exc}). Nothing is lost: the OCR text and every "
                "finished page are saved, so wait a few minutes and run "
                "again with --resume to continue from the next page. "
                "Tip: setting MYMEMORY_EMAIL at the top of this file "
                "raises MyMemory's free daily quota roughly tenfold. "
                "For a whole book, the free services are the bottleneck "
                "— a few pages per day, or an offline model, is the "
                "reliable way."
            ) from last_exc
    time.sleep(1)  # stay under the free endpoint's rate limit
    translated = " ".join(result)
    if _is_echo(translated, text):
        print("    (translation came back as an echo of the original — "
              "kept as untranslated, will retry on the next run)",
              flush=True)
        return ""
    return translated


def split_blocks(text: str) -> list[str]:
    """Split page text into blocks: on blank lines, and after a line that
    ends a verse (…॥n॥), so each shloka translates as its own block
    instead of the whole page going in as one block."""
    blocks, current = [], []
    verse_end = re.compile(r"॥\s*[०-९0-9]+\s*॥\s*$")
    for line in text.splitlines():
        if not line.strip():
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
        if verse_end.search(line):
            blocks.append("\n".join(current))
            current = []
    if current:
        blocks.append("\n".join(current))
    return [b.strip() for b in blocks if b.strip()]


# ---------------------------------------------------------------- Telugu font

def find_telugu_font(font_dir: str = FONT_DIR) -> tuple[str, str]:
    """Find a Telugu Regular+Bold font pair. Returns (regular, bold).

    Two passes, in this order:
    1. Exact file names (NotoSansTelugu-Regular/Bold.ttf, NirmalaUI.ttf)
       in every font folder. This is what wins on Babu's machine: the
       Noto files sit in the per-user fonts folder.
    2. A scan for any .ttf/.otf whose name contains "telugu" or
       "nirmala" — because Windows shows fonts by display name while the
       file on disk is called something else, and a double-click install
       puts the file in the per-user fonts folder
       (%LOCALAPPDATA%\\Microsoft\\Windows\\Fonts), not in
       C:\\Windows\\Fonts, even though the Fonts window shows both
       together.
    A .ttc (font collection) is never picked: the system Nirmala font is
    Nirmala.ttc, a collection, and reportlab cannot use it directly.
    """
    candidates: list[Path] = []
    here = Path(__file__).resolve().parent
    if font_dir:
        candidates.append(Path(font_dir))
    candidates.append(here / "fonts")
    if sys.platform.startswith("win"):
        candidates.append(Path(r"C:\Windows\Fonts"))
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    candidates += [Path("/usr/share/fonts/truetype/noto"),
                   Path("/usr/share/fonts"),
                   Path("/usr/local/share/fonts")]

    def exact(folder: Path) -> tuple[str | None, str | None]:
        reg = bold = None
        for name in ("NotoSansTelugu-Regular.ttf", "NirmalaUI.ttf"):
            if (folder / name).exists():
                reg = str(folder / name)
                break
        for name in ("NotoSansTelugu-Bold.ttf", "NirmalaUI-Bold.ttf"):
            if (folder / name).exists():
                bold = str(folder / name)
                break
        return reg, bold

    # pass 1: exact names, all folders first
    for folder in candidates:
        reg, bold = exact(folder)
        if reg:
            return reg, bold or reg

    # pass 2: scan for any telugu/nirmala .ttf/.otf
    seen_files: list[str] = []
    seen_ttc: list[str] = []
    for folder in candidates:
        if not folder.is_dir():
            continue
        try:
            files = []
            for pat in ("*.ttf", "*.otf", "*.ttc"):
                files.extend(folder.rglob(pat))
            files = sorted(files)
        except OSError:
            files = []
        telu = [p for p in files
                if ("telugu" in p.name.lower()
                    or "nirmala" in p.name.lower())]
        seen_files += [str(p) for p in telu
                       if p.suffix.lower() in (".ttf", ".otf")]
        seen_ttc += [str(p) for p in telu if p.suffix.lower() == ".ttc"]
        reg = bold = None
        for p in telu:
            if p.suffix.lower() == ".ttc":
                continue  # a collection — reportlab cannot use it directly
            low = p.name.lower()
            if ("bold" in low or low.endswith("-bd.ttf")
                    or "-bd." in low):
                bold = bold or str(p)
            elif "regular" in low:
                reg = reg or str(p)
        if reg is None:
            for p in telu:
                if p.suffix.lower() == ".ttc":
                    continue
                low = p.name.lower()
                if ("bold" not in low and not low.endswith("-bd.ttf")
                        and "-bd." not in low):
                    reg = str(p)
                    break
        if reg:
            return reg, bold or reg
    searched = "\n    ".join(str(c) for c in candidates)
    seen = ("\n    ".join(seen_files) if seen_files
            else "(no Telugu/Nirmala font file seen in any of them)")
    ttc_note = ("\nFont collections seen but not usable directly "
                "(reportlab needs a .ttf, not a .ttc):\n    "
                + "\n    ".join(seen_ttc)) if seen_ttc else ""
    raise RuntimeError(
        "No Telugu font found.\n"
        f"Folders searched:\n    {searched}\n"
        f"Telugu/Nirmala font files seen there:\n    {seen}"
        f"{ttc_note}\n"
        "If your fonts live somewhere else, put "
        "NotoSansTelugu-Regular.ttf and NotoSansTelugu-Bold.ttf in a "
        "'fonts' folder next to this script (or set FONT_DIR at the top "
        "of the file)."
    )


def check_font_coverage(font_path: str) -> None:
    """Warn if the font lacks the Devanagari dandas the text needs."""
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        return  # cannot check without fontTools; continue
    cmap = TTFont(font_path).getBestCmap()
    missing = [hex(c) for c in (0x0964, 0x0965) if c not in cmap]
    if missing:
        print(f"WARNING: font {Path(font_path).name} lacks {missing} "
              "(the dandas । ॥) — separators may print as boxes. "
              "Use Noto Sans Telugu instead.")


# ---------------------------------------------------------------- PDF output

def build_pdf(title: str, pages: list[dict], out_path: Path,
              font_regular: str, font_bold: str, mode: str) -> None:
    """Build the Telugu PDF with reportlab. `pages` is a list of
    {'number': int, 'blocks': [{'telu': str, 'trans': str|None}]}."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate,
                                    Paragraph, Spacer, PageBreak)
    from xml.sax.saxutils import escape as xml_escape

    pdfmetrics.registerFont(TTFont("Telugu", font_regular))
    pdfmetrics.registerFont(TTFont("Telugu-Bold", font_bold))
    pdfmetrics.registerFontFamily("Telugu", normal="Telugu",
                                  bold="Telugu-Bold")

    styles = {
        "title": ParagraphStyle("title", fontName="Telugu-Bold",
                                fontSize=22, leading=28, alignment=1,
                                spaceAfter=6 * mm),
        "subtitle": ParagraphStyle("subtitle", fontName="Telugu",
                                   fontSize=12, leading=17, alignment=1,
                                   textColor=colors.HexColor("#444444")),
        "pagehead": ParagraphStyle("pagehead", fontName="Telugu-Bold",
                                   fontSize=13, leading=17,
                                   textColor=colors.HexColor("#1a3a5c"),
                                   spaceBefore=4 * mm, spaceAfter=2 * mm),
        "body": ParagraphStyle("body", fontName="Telugu",
                               fontSize=12, leading=18, alignment=1,
                               spaceAfter=2 * mm),
        "trans": ParagraphStyle("trans", fontName="Telugu",
                                fontSize=12, leading=18, alignment=1,
                                textColor=colors.HexColor("#333333"),
                                spaceAfter=4 * mm),
    }

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Telugu", 9)
        canvas.setFillColor(colors.HexColor("#888888"))
        canvas.drawCentredString(A4[0] / 2, 12 * mm,
                                 f"పేజీ {doc.page}")
        canvas.restoreState()

    doc = BaseDocTemplate(str(out_path), pagesize=A4,
                          leftMargin=18 * mm, rightMargin=18 * mm,
                          topMargin=16 * mm, bottomMargin=16 * mm,
                          title=title, author="OCR + translation")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height,
                  id="main")
    doc.addPageTemplates([PageTemplate(id="all", frames=[frame],
                                       onPage=footer)])
    story = []
    story.append(Spacer(1, 45 * mm))
    story.append(Paragraph(xml_escape(title), styles["title"]))
    story.append(Paragraph(
        "తెలుగు అనువాదం — స్కాన్ చేసిన పుస్తకం నుండి OCR ద్వారా",
        styles["subtitle"]))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(
        "గమనిక: ఇది OCR మరియు యంత్ర అనువాదం ద్వారా తయారైన ముసాయిదా. "
        "అక్షరాలు మరియు పద్య సంఖ్యలు తప్పుగా చదవబడి ఉండవచ్చు — "
        "ముద్రణకు ముందు సరిచూడండి.", styles["subtitle"]))
    story.append(PageBreak())
    for page in pages:
        story.append(Paragraph(f"మూల పేజీ {page['number']}",
                               styles["pagehead"]))
        for block in page["blocks"]:
            if mode in ("transliterate", "both") and block.get("telu"):
                for line in block["telu"].splitlines():
                    if line.strip():
                        story.append(Paragraph(xml_escape(line),
                                               styles["body"]))
            if mode in ("translate", "both") and block.get("trans"):
                text = block["trans"]
                if mode == "both":
                    text = "అనువాదం — " + text
                story.append(Paragraph(xml_escape(text), styles["trans"]))
    doc.build(story)


# ---------------------------------------------------------------- main

def parse_pages(spec: str, total: int) -> list[int]:
    if not spec:
        return list(range(1, total + 1))
    if "-" in spec:
        a, b = spec.split("-", 1)
        return list(range(int(a), min(int(b), total) + 1))
    return [int(spec)]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read a scanned-images PDF and build a Telugu PDF.")
    parser.add_argument("input", nargs="?",
                        help="Scanned PDF (not needed if INPUT_PDF is set)")
    parser.add_argument("-o", "--output", help="Output PDF")
    parser.add_argument("--mode", choices=["transliterate", "translate", "both"],
                        default=None)
    parser.add_argument("--pages", default=None,
                        help="e.g. 1-2 to convert only pages 1-2 first")
    parser.add_argument("--source", choices=["sa", "hi"], default=None,
                        help="language of the book: sa = Sanskrit (default), "
                             "hi = Hindi")
    parser.add_argument("--resume", action="store_true",
                        help="reuse saved OCR text instead of re-reading "
                             "the pages (translation still re-runs)")
    parser.add_argument("--check", action="store_true",
                        help="check setup only (packages, tesseract, font, "
                             "input/output) and exit without converting")
    args = parser.parse_args()

    input_arg = INPUT_PDF or args.input
    if not input_arg:
        sys.exit("No input PDF given. Set INPUT_PDF at the top of this file, "
                 "or run: python scanned_pdf_to_telugu_pdf.py <book.pdf>")
    in_path = Path(input_arg)
    if not in_path.exists():
        sys.exit(f"Input file not found: {in_path}")
    mode = args.mode or MODE
    source = args.source or SOURCE_LANG
    page_spec = args.pages or PAGES

    out_path = Path(OUTPUT_PDF or args.output) if (OUTPUT_PDF or args.output) \
        else in_path.with_stem(in_path.stem + "_telugu")
    if out_path.suffix.lower() != ".pdf":
        out_path = out_path.with_suffix(".pdf")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    ocr_path = in_path.with_name(in_path.stem + "_ocr.txt")
    te_path = in_path.with_name(in_path.stem + "_te.txt")
    # With --resume and a saved OCR checkpoint, no page is re-read, so
    # tesseract is not needed at all for this run.
    need_ocr = not (args.resume and ocr_path.exists())

    # ---- pre-flight: fail in seconds, not after hours of OCR ---------
    # Font and tesseract are checked BEFORE any page is OCR'd, so a
    # missing font/program is found now instead of at the PDF step.
    problems = []
    for pkg in ("pymupdf", "reportlab"):
        try:
            __import__(pkg)
        except ImportError:
            problems.append(f"package '{pkg}' not installed "
                            f"(pip install {pkg})")
    if mode in ("transliterate", "both"):
        if sanscript is None:
            problems.append("package 'indic-transliteration' not installed "
                            "(pip install indic-transliteration)")
    if mode in ("translate", "both"):
        try:
            import deep_translator  # noqa: F401
        except ImportError:
            problems.append("package 'deep-translator' not installed "
                            "(pip install deep-translator)")
    if not need_ocr:
        print(f"Resume: using saved OCR text from {ocr_path.name}; "
              "tesseract not needed for this run.")
    elif shutil.which(TESSERACT_CMD) is None:
        problems.append(f"tesseract not found as '{TESSERACT_CMD}' — install "
                        "it (Windows: UB-Mannheim, tick Sanskrit+Hindi and "
                        "'Add to PATH') or set TESSERACT_CMD")
    else:
        langs = subprocess.run([TESSERACT_CMD, "--list-langs"],
                               capture_output=True, text=True).stdout
        # --list-langs prints one code per line after a header line;
        # accept san/hin anywhere in the list (tesseract may list san+hin
        # components separately).
        for code in ("san", "hin"):
            if not re.search(rf"^{code}$", langs, re.M):
                problems.append(f"tesseract language '{code}' not installed "
                                f"(re-run the installer, tick "
                                f"{'Sanskrit' if code == 'san' else 'Hindi'})")
    try:
        font_regular, font_bold = find_telugu_font()
        check_font_coverage(font_regular)
    except RuntimeError as exc:
        problems.append(str(exc))
        font_regular = font_bold = ""
    if not out_path.parent.is_dir() or not os.access(out_path.parent, os.W_OK):
        problems.append(f"output folder not writable: {out_path.parent}")
    if problems:
        print("Setup check FAILED:")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    print(f"Setup check OK: packages, Telugu font "
          f"({Path(font_regular).name}), input and output all ready.")
    if args.check:
        sys.exit(0)

    import pymupdf
    doc = pymupdf.open(str(in_path))
    total = len(doc)
    wanted = parse_pages(page_spec, total)
    print(f"{in_path.name}: {total} pages, converting {len(wanted)} "
          f"(mode={mode}, source={source})")

    # ---- OCR phase (checkpointed) -------------------------------------
    ocr_pages: dict[int, str] = {}
    if args.resume and ocr_path.exists():
        current = None
        for line in ocr_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("### PAGE "):
                current = int(line.split()[2])
                ocr_pages[current] = ""
            elif current is not None:
                ocr_pages[current] += line + "\n"
        print(f"Resumed: OCR text loaded for {len(ocr_pages)} pages.")
    dropped_total = 0
    for n in wanted:
        if n in ocr_pages:
            continue
        t0 = time.time()
        text = ocr_page(doc[n - 1])
        cleaned, dropped = clean_ocr_text(text)
        dropped_total += dropped
        ocr_pages[n] = cleaned
        print(f"  page {n}: OCR done in {time.time() - t0:.0f}s, "
              f"{len(cleaned)} chars", flush=True)
    with open(ocr_path, "w", encoding="utf-8") as f:
        for n in sorted(ocr_pages):
            f.write(f"### PAGE {n}\n{ocr_pages[n]}\n")
    print(f"OCR text saved to {ocr_path.name} "
          f"({dropped_total} footer lines dropped)")

    # ---- convert phase (checkpointed per page) ------------------------
    # Finished pages are saved to a JSON file the moment each page is
    # done. If translation throttling stops the run halfway, re-running
    # with --resume skips every finished page instead of starting over.
    import json

    te_json_path = in_path.with_name(in_path.stem + "_te.json")
    done_pages: dict[str, list[dict]] = {}
    if args.resume and te_json_path.exists():
        done_pages = json.loads(te_json_path.read_text(encoding="utf-8"))
        if mode in ("translate", "both"):
            # pages with untranslated blocks (echoes, or the run died
            # mid-page) are converted again, not treated as finished.
            done_pages = {k: v for k, v in done_pages.items()
                          if v and all(b.get("trans") for b in v)}
        print(f"Resume: {len(done_pages)} pages already converted — "
              "skipping them.")
    pages: list[dict] = []
    for n in wanted:
        if str(n) in done_pages:
            pages.append({"number": n, "blocks": done_pages[str(n)]})
            print(f"  page {n}: already converted, skipped")
            continue
        raw = ocr_pages.get(n, "")
        blocks = []
        for b in split_blocks(raw):
            item: dict = {}
            if mode in ("transliterate", "both"):
                item["telu"] = transliterate(b)
            if mode in ("translate", "both"):
                trans = translate(b, source=source)
                if trans:
                    item["trans"] = trans
            blocks.append(item)
        pages.append({"number": n, "blocks": blocks})
        done_pages[str(n)] = blocks
        te_json_path.write_text(json.dumps(done_pages, ensure_ascii=False,
                                           indent=1), encoding="utf-8")
        print(f"  page {n}: converted {len(blocks)} blocks (saved)", flush=True)
    with open(te_path, "w", encoding="utf-8") as f:
        for page in pages:
            f.write(f"### PAGE {page['number']}\n")
            for block in page["blocks"]:
                if block.get("telu"):
                    f.write(block["telu"] + "\n\n")
                if block.get("trans"):
                    f.write(block["trans"] + "\n\n")

    # ---- PDF phase -----------------------------------------------------
    font_regular, font_bold = find_telugu_font()
    check_font_coverage(font_regular)
    print(f"Telugu font: {Path(font_regular).name}")
    title = in_path.stem
    build_pdf(title, pages, out_path, font_regular, font_bold, mode)
    print(f"Wrote {out_path}")
    print("Reminder: this is an OCR + machine-translation draft. "
          "Check conjunct letters, verse numbers and names before printing.")


if __name__ == "__main__":
    main()
