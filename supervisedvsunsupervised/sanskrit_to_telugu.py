#!/usr/bin/env python3
"""
hindi_to_telugu.py — Convert a Hindi book (Devanagari) into a Telugu book.

Two things people usually mean by "convert", so this script does both:

  * transliterate (default): the same Hindi words, written in Telugu script.
    Exact and offline — nothing is reworded, no meaning is guessed.
  * translate:           the Hindi meaning, translated into Telugu.
    Needs internet (uses Google Translate through the `deep-translator`
    package). A translation is always a draft — check proper names and
    technical terms before printing.
  * both:                Telugu script first, then the Telugu translation,
    one after the other — the same layout as a facing-text book.

Input : .txt, .docx, or .pdf (Sanskrit/Hindi text in Devanagari)
Output: .txt or .docx (Telugu text, set in Noto Sans Telugu)

PDFs: works when the PDF has real embedded text (a book made from a Word
file, or a digitally typeset PDF). The text is read page by page with
PyMuPDF, then converted exactly like a .txt file.

If the PDF is a SCAN (pages are photos of a printed book), there is no
text to read — the only text on a page is usually the digitizer's footer
stamp, repeated on every page. A page with almost no text is therefore
read with OCR instead: the page is rendered as an image and tesseract
reads the Devanagari (language `san+hin`). This needs the `tesseract`
program plus PyMuPDF. On Windows, install Tesseract from
https://github.com/UB-Mannheim/tesseract/wiki and tick the Sanskrit and
Hindi language data during setup; on Linux: `apt-get install tesseract-ocr
tesseract-ocr-san tesseract-ocr-hin`. Use --ocr off to skip OCR, or
--ocr on to OCR every page even when it has text.

Setup (once):
    python3 -m venv ~/hindi-telugu-venv
    ~/hindi-telugu-venv/bin/pip install indic-transliteration python-docx pymupdf
    # only needed for --mode translate / both:
    ~/hindi-telugu-venv/bin/pip install deep-translator

Usage:
    ~/hindi-telugu-venv/bin/python hindi_to_telugu.py book_hindi.txt
    ~/hindi-telugu-venv/bin/python hindi_to_telugu.py book_hindi.docx --mode both
    ~/hindi-telugu-venv/bin/python hindi_to_telugu.py sanskrit_book.pdf --mode transliterate
    ~/hindi-telugu-venv/bin/python hindi_to_telugu.py book_hindi.txt -o book_telugu.docx --mode translate

Or, if you prefer to put the path in the file instead of the command line,
fill in INPUT_PATH below (put it in quotes, and put an r in front of the
quote on Windows so the backslashes are read literally) and just run:
    ~/hindi-telugu-venv/bin/python hindi_to_telugu.py

Why the dandas stay Devanagari:
    Telugu has its own danda characters (U+0C64/U+0C65), but almost no
    Telugu font contains them — in a PDF they print as empty boxes.
    Printed Telugu books therefore use the Devanagari danda (U+0964/U+0965),
    which every Telugu font does contain. This script keeps "।" and "॥"
    as they are, and converts the digits: १२३ -> ౧౨౩.
"""

# ---------------------------------------------------------------- settings
# Put your book's path here if you would rather not type it on the command
# line. Leave it as "" to use the command-line argument instead.
# Windows example:  INPUT_PATH = r"C:\Users\ishar\MyAIProject\book.pdf"
# Linux example:    INPUT_PATH = "/home/babu/books/book.pdf"
#INPUT_PATH = ""
#MODE = "transliterate"   # "transliterate", "translate", or "both"
#OUTPUT_PATH = ""         # leave "" for the automatic <name>_telugu name
INPUT_PATH = r"C:\Users\ishar\Downloads\Telegram Desktop\వాంఛాకల్పలతాదేవనాగరం.pdf"
MODE = "transliterate"
OUTPUT_PATH = r"F:\ConvertedPDFs\వాంఛాకల్పలతాదేవనాగరంTelugu.docx"
import argparse
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------- transliteration

DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "౦౧౨౩౪౫౬౭౮౯")

try:
    from indic_transliteration import sanscript
except ImportError:
    sanscript = None


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
    # want (see the note at the top). Nothing to fix here; assert the Telugu
    # dandas never sneak in, because no common Telugu font can print them.
    out = out.replace("\u0c64", "।").replace("\u0c65", "॥")
    return out


# ---------------------------------------------------------------- translation

def _split_for_translation(text: str, limit: int = 4000) -> list[str]:
    """Split text into chunks under `limit` chars, at sentence boundaries."""
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


def translate(text: str) -> str:
    """Hindi -> Telugu translation (needs internet).

    Tries Google Translate first; if that endpoint throttles the request
    (common from shared/cloud machines), falls back to MyMemory, which is
    free without a key but has a smaller daily quota. A translation is
    always a draft — check proper names and technical terms before printing.
    """
    import time

    try:
        from deep_translator import GoogleTranslator, MyMemoryTranslator
    except ImportError:
        raise RuntimeError(
            "deep-translator is not installed. Run:\n"
            "    pip install deep-translator"
        )

    def google(chunk: str) -> str:
        return GoogleTranslator(source="hi", target="te").translate(chunk)

    def mymemory(chunk: str) -> str:
        return MyMemoryTranslator(source="hindi", target="telugu").translate(chunk)

    translated = []
    for chunk in _split_for_translation(text):
        result = None
        for attempt in range(3):
            try:
                result = google(chunk)
                break
            except Exception:
                time.sleep(2 * (attempt + 1))
        if result is None:  # Google throttled us — try the fallback
            try:
                result = mymemory(chunk)
            except Exception as exc:
                raise RuntimeError(
                    "Translation failed on both Google and MyMemory "
                    f"({exc}). Wait a few minutes and run again."
                ) from exc
        translated.append(result)
        time.sleep(0.5)  # stay under the free endpoint's rate limit
    return " ".join(translated)


# ---------------------------------------------------------------- I/O

def read_text_file(path: Path) -> str:
    """Read a .txt file, whatever encoding Windows saved it in.

    Notepad and Word often save Hindi text as UTF-16 or Windows ANSI
    instead of UTF-8; reading those as UTF-8 crashes with
    UnicodeDecodeError. Detect the encoding instead of assuming it.
    """
    data = path.read_bytes()
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        print("Detected encoding: UTF-16")
        return data.decode("utf-16")
    if data.startswith(b"\xef\xbb\xbf"):
        print("Detected encoding: UTF-8 with BOM")
        return data.decode("utf-8-sig")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    # A .docx or .pdf wrongly named .txt is binary, not text — say so plainly.
    if data[:2] == b"PK":
        raise ValueError(
            f"{path.name} looks like a Word (.docx) file, not a .txt file. "
            "Rename it to .docx (or save it as plain text) and run again."
        )
    if data[:4] == b"%PDF":
        raise ValueError(
            f"{path.name} looks like a PDF file, not a .txt file. "
            "Rename it to .pdf and run again."
        )
    print("WARNING: not UTF-8 — reading as Windows ANSI (cp1252); "
          "some characters may be replaced.")
    return data.decode("cp1252", errors="replace")


def ocr_page(page, dpi: int = 300, lang: str = "san+hin") -> str:
    """Render one PDF page as an image and read its Devanagari with OCR."""
    import shutil
    import subprocess

    if shutil.which("tesseract") is None:
        raise RuntimeError(
            "OCR needs the 'tesseract' program, which is not installed. "
            "Windows: install it from "
            "https://github.com/UB-Mannheim/tesseract/wiki (tick Sanskrit "
            "and Hindi during setup). Linux: apt-get install tesseract-ocr "
            "tesseract-ocr-san tesseract-ocr-hin."
        )
    png = page.get_pixmap(dpi=dpi).tobytes("png")
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", "6", "-l", lang],
        input=png, capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "tesseract failed: " + result.stderr.decode("utf-8", "replace")[:200]
        )
    return result.stdout.decode("utf-8", "replace")


def read_pdf_blocks(path: Path, ocr: str = "auto") -> list[dict]:
    """Read a PDF page by page into text blocks (one block per line).

    Tries PyMuPDF first, then the `pdftotext` command. A page with almost
    no extractable text is a scan: with --ocr auto (default) such pages
    are read with OCR instead; with --ocr on, every page is OCR'd.
    """
    lines: list[str] = []
    empty_pages = 0
    ocr_pages = 0
    total_pages = 0
    try:
        import pymupdf  # PyMuPDF >= 1.24
    except ImportError:
        try:
            import fitz as pymupdf  # older PyMuPDF
        except ImportError:
            pymupdf = None
    if pymupdf is not None:
        doc = pymupdf.open(str(path))
        total_pages = len(doc)
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text")
            use_ocr = ocr == "on" or (ocr == "auto" and len(text.strip()) < 100)
            if use_ocr:
                try:
                    text = ocr_page(page)
                    ocr_pages += 1
                except RuntimeError as exc:
                    print(f"Page {i}: {exc}")
                    # fall through with whatever text the page had
            if not text.strip():
                empty_pages += 1
            lines.extend(text.splitlines())
            lines.append("")  # blank line between pages
    else:
        import subprocess

        result = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "Could not read this PDF: install PyMuPDF "
                "(pip install pymupdf) or poppler (pdftotext)."
            )
        lines = result.stdout.splitlines()
        total_pages = 0  # unknown without a PDF reader
    if ocr_pages:
        print(f"OCR was used on {ocr_pages} of {total_pages} pages "
              "(those pages are scans; check the recognitions — "
              "conjunct letters and verse numbers can be misread).")
    if empty_pages:
        print(f"WARNING: {empty_pages} of {total_pages} pages still had no "
              "text even after OCR — those pages may be blank or images "
              "without writing.")
    return [{"text": line, "style": "Normal", "runs": None} for line in lines]


def read_input(path, ocr: str = "auto") -> tuple[list[dict], str]:
    """Return (blocks, kind) where each block is {'text', 'style', 'runs'}.

    Accepts a Path or a plain string. The file type is decided by its
    SUFFIX (.pdf / .docx / .txt) — never by comparing the whole path
    to ".pdf". If the suffix is missing or unfamiliar, the file's first
    bytes decide instead (PDF files start with %PDF, Word files with PK).
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in (".pdf", ".docx", ".txt"):
        with open(path, "rb") as f:
            magic = f.read(4)
        if magic == b"%PDF":
            suffix = ".pdf"
        elif magic[:2] == b"PK":
            suffix = ".docx"
        else:
            suffix = ".txt"
    if suffix == ".pdf":
        return read_pdf_blocks(path, ocr=ocr), "txt"
    if suffix == ".docx":
        from docx import Document

        doc = Document(str(path))
        blocks = []
        for para in doc.paragraphs:
            runs = [
                {"text": r.text, "bold": r.bold, "italic": r.italic}
                for r in para.runs
            ]
            if not runs:  # empty paragraph (blank line)
                runs = [{"text": "", "bold": False, "italic": False}]
            blocks.append({"text": para.text, "style": para.style.name, "runs": runs})
        return blocks, "docx"
    # plain text: one block per line, blank lines kept as empty blocks
    lines = read_text_file(path).splitlines()
    return [{"text": line, "style": "Normal", "runs": None} for line in lines], "txt"


def convert_runs(runs: list[dict], convert) -> list[dict]:
    return [
        {"text": convert(r["text"]), "bold": r["bold"], "italic": r["italic"]}
        for r in runs
    ]


def build_blocks(blocks: list[dict], mode: str) -> list[dict]:
    """Apply the chosen mode to every block; 'both' doubles each block."""
    out = []
    for block in blocks:
        text = block["text"]
        if not text.strip():
            out.append(block)
            continue
        if mode in ("transliterate", "both"):
            tele_text = transliterate(text)
            if block["runs"] is not None:
                new_block = dict(block)
                new_block["runs"] = convert_runs(block["runs"], transliterate)
                new_block["text"] = tele_text
            else:
                new_block = dict(block); new_block["text"] = tele_text
            out.append(new_block)
        if mode in ("translate", "both"):
            translated = translate(text)
            if mode == "both":
                trans_block = {"text": "అనువాదం — " + translated,
                               "style": block["style"], "runs": None}
                # for docx, keep it as plain text (no bold/italic carried over)
                if block["runs"] is not None:
                    trans_block["runs"] = [
                        {"text": "అనువాదం — " + translated,
                         "bold": False, "italic": False}
                    ]
                out.append(trans_block)
            else:
                if block["runs"] is not None:
                    new_block = dict(block)
                    new_block["runs"] = [
                        {"text": translated, "bold": False, "italic": False}
                    ]
                    new_block["text"] = translated
                else:
                    new_block = dict(block); new_block["text"] = translated
                out.append(new_block)
    return out


def write_output(blocks: list[dict], kind: str, path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "txt":
        path.write_text(
            "\n".join(b["text"] for b in blocks), encoding="utf-8"
        )
        return
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = "Noto Sans Telugu"
    normal.font.size = Pt(12)
    for block in blocks:
        para = doc.add_paragraph(style=block["style"])
        for run in block["runs"] or [{"text": block["text"],
                                      "bold": False, "italic": False}]:
            r = para.add_run(run["text"])
            r.bold = run["bold"]
            r.italic = run["italic"]
            r.font.name = "Noto Sans Telugu"
    doc.save(str(path))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a Hindi book (Devanagari) into a Telugu book."
    )
    parser.add_argument("input", nargs="?",
                        help="Hindi book: .txt, .docx or .pdf "
                             "(not needed if INPUT_PATH is set at the top)")
    parser.add_argument("-o", "--output",
                        help="Output file (default: <name>_telugu.<ext>)")
    parser.add_argument("--mode", choices=["transliterate", "translate", "both"],
                        default=None,
                        help="transliterate = same words in Telugu script; "
                             "translate = meaning in Telugu; both = script + "
                             "translation (default: the MODE setting at the top)")
    parser.add_argument("--ocr", choices=["auto", "on", "off"], default="auto",
                        help="PDF scans: auto = OCR only pages with almost no "
                             "text (default); on = OCR every page; off = never OCR")
    args = parser.parse_args()

    # The path can come from the settings at the top of this file
    # (INPUT_PATH / MODE / OUTPUT_PATH) or from the command line.
    input_arg = INPUT_PATH or args.input
    if not input_arg:
        sys.exit("No input file given. Either set INPUT_PATH at the top of "
                 "this file, or run: python hindi_to_telugu.py <book.pdf>")
    in_path = Path(input_arg)
    mode = args.mode or MODE
    if in_path.is_dir():
        sys.exit(f"{in_path} is a folder, not a file. Give the full path "
                 "to the book file itself, e.g. "
                 r"C:\Users\ishar\Downloads\book.pdf")
    if not in_path.exists():
        sys.exit(f"Input file not found: {in_path}")

    out_arg = OUTPUT_PATH or args.output
    out_path = Path(out_arg) if out_arg else in_path.with_stem(
        in_path.stem + "_telugu"
    )
    kind = "docx" if out_path.suffix.lower() == ".docx" else "txt"

    blocks, _ = read_input(in_path, ocr=args.ocr)
    print(f"Read {len(blocks)} paragraphs/lines from {in_path.name}")
    converted = build_blocks(blocks, mode)
    write_output(converted, kind, out_path)
    print(f"Wrote {out_path}  (mode={mode})")
    print("Note: dandas are kept as Devanagari । ॥ — that is what prints "
          "correctly in Telugu fonts (see the note at the top of the script).")


if __name__ == "__main__":
    main()
