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
  * translate (default):    the meaning in Telugu.
  * transliterate:          the same words, written in Telugu script.
  * both:                   Telugu script first, then "అనువాదం —" + the
                            Telugu translation under it.

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
    python scanned_pdf_to_telugu_pdf.py book.pdf
    python scanned_pdf_to_telugu_pdf.py book.pdf --pages 1-2 --mode translate
    python scanned_pdf_to_telugu_pdf.py book.pdf --pages 1-97 -o book_telugu.pdf
    python scanned_pdf_to_telugu_pdf.py book.pdf --resume
"""

import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------- settings
# Fill these in if you would rather not type them on the command line.
# Windows example:  INPUT_PDF = r"C:\Users\ishar\Downloads\book.pdf"
#INPUT_PDF = ""
#OUTPUT_PDF = ""          # leave "" for <name>_telugu.pdf
INPUT_PDF = r"C:\Users\ishar\Downloads\Telegram Desktop\వాంఛాకల్పలతాదేవనాగరం.pdf"
OUTPUT_PDF = r"F:\ConvertedPDFs\వాంఛాకల్పలతాదేవనాగరంTelugu.docx"
MODE = "translate"       # "translate", "transliterate", or "both"
SOURCE_LANG = "sa"       # OCR text language for translation: "sa" = Sanskrit,
                         # "hi" = Hindi. (Only used for translate/both.)
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


def translate(text: str, source: str = SOURCE_LANG) -> str:
    """OCR text -> Telugu translation (Google first, MyMemory fallback).

    Google takes chunks of up to ~4000 chars, but MyMemory only accepts
    500 chars at a time — so the fallback re-chunks before sending.
    """
    try:
        from deep_translator import GoogleTranslator, MyMemoryTranslator
    except ImportError:
        raise RuntimeError(
            "deep-translator is not installed. Run:\n"
            "    pip install deep-translator"
        )
    google_src = {"sa": "sa", "hi": "hi"}.get(source, source)
    mymemory_src = {"sa": "sanskrit", "hi": "hindi"}.get(source, source)

    def google(chunk: str) -> str:
        return GoogleTranslator(source=google_src, target="te").translate(chunk)

    def mymemory(chunk: str) -> str:
        # MyMemory rejects anything over 500 chars — send it in small pieces.
        translator = MyMemoryTranslator(source=mymemory_src, target="telugu")
        parts = _split_for_translation(chunk, limit=450)
        return " ".join(translator.translate(p) for p in parts)

    translated = []
    for chunk in _split_for_translation(text):
        result = None
        for attempt in range(3):
            try:
                result = google(chunk)
                break
            except Exception:
                time.sleep(2 * (attempt + 1))
        if result is None:
            try:
                result = mymemory(chunk)
            except Exception as exc:
                raise RuntimeError(
                    "Translation failed on both Google and MyMemory "
                    f"({exc}). Wait a few minutes and run again."
                ) from exc
        translated.append(result)
        time.sleep(0.5)
    return " ".join(translated)


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
    """Find a Telugu Regular+Bold .ttf pair. Returns (regular, bold)."""
    candidates: list[Path] = []
    here = Path(__file__).resolve().parent
    if font_dir:
        candidates.append(Path(font_dir))
    candidates.append(here / "fonts")
    if sys.platform.startswith("win"):
        candidates.append(Path(r"C:\Windows\Fonts"))
    candidates += [Path("/usr/share/fonts/truetype/noto"),
                   Path("/usr/share/fonts"),
                   Path("/usr/local/share/fonts")]
    for folder in candidates:
        reg, bold = None, None
        for name in ("NotoSansTelugu-Regular.ttf", "NirmalaUI.ttf"):
            p = folder / name
            if p.exists():
                reg = str(p)
                break
        for name in ("NotoSansTelugu-Bold.ttf", "NirmalaUI-Bold.ttf"):
            p = folder / name
            if p.exists():
                bold = str(p)
                break
        if reg:
            return reg, bold or reg
    raise RuntimeError(
        "No Telugu font found. Download NotoSansTelugu-Regular.ttf and "
        "NotoSansTelugu-Bold.ttf from Google Fonts and put them in a "
        "'fonts' folder next to this script (or set FONT_DIR). "
        "On Windows, Nirmala UI (C:\\Windows\\Fonts\\NirmalaUI.ttf) "
        "also works if it is present."
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
    story.append(Paragraph(title, styles["title"]))
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
                        story.append(Paragraph(line, styles["body"]))
            if mode in ("translate", "both") and block.get("trans"):
                text = block["trans"]
                if mode == "both":
                    text = "అనువాదం — " + text
                story.append(Paragraph(text, styles["trans"]))
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
              f"{len(cleaned)} chars")
    with open(ocr_path, "w", encoding="utf-8") as f:
        for n in sorted(ocr_pages):
            f.write(f"### PAGE {n}\n{ocr_pages[n]}\n")
    print(f"OCR text saved to {ocr_path.name} "
          f"({dropped_total} footer lines dropped)")

    # ---- convert phase (checkpointed) ---------------------------------
    # translated text is stored per page as: blocks joined by \n\n, and
    # in "both" mode the transliterated blocks are stored too.
    pages: list[dict] = []
    if args.resume and te_path.exists():
        print("Resume: reusing OCR text; translating blocks again.")
    for n in wanted:
        raw = ocr_pages.get(n, "")
        blocks = []
        for b in split_blocks(raw):
            item: dict = {}
            if mode in ("transliterate", "both"):
                item["telu"] = transliterate(b)
            if mode in ("translate", "both"):
                item["trans"] = translate(b, source=source)
            blocks.append(item)
        pages.append({"number": n, "blocks": blocks})
        print(f"  page {n}: converted {len(blocks)} blocks")
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
