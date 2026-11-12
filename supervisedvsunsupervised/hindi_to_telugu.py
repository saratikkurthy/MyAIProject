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

Input : .txt or .docx (Hindi text in Devanagari)
Output: .txt or .docx (Telugu text, set in Noto Sans Telugu)

Setup (once):
    python3 -m venv ~/hindi-telugu-venv
    ~/hindi-telugu-venv/bin/pip install indic-transliteration python-docx
    # only needed for --mode translate / both:
    ~/hindi-telugu-venv/bin/pip install deep-translator

Usage:
    ~/hindi-telugu-venv/bin/python hindi_to_telugu.py book_hindi.txt
    ~/hindi-telugu-venv/bin/python hindi_to_telugu.py book_hindi.docx --mode both
    ~/hindi-telugu-venv/bin/python hindi_to_telugu.py book_hindi.txt -o book_telugu.docx --mode translate

Why the dandas stay Devanagari:
    Telugu has its own danda characters (U+0C64/U+0C65), but almost no
    Telugu font contains them — in a PDF they print as empty boxes.
    Printed Telugu books therefore use the Devanagari danda (U+0964/U+0965),
    which every Telugu font does contain. This script keeps "।" and "॥"
    as they are, and converts the digits: १२३ -> ౧౨౩.
"""

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

def read_input(path: Path) -> tuple[list[dict], str]:
    """Return (blocks, kind) where each block is {'text', 'style', 'runs'}."""
    if path.suffix.lower() == ".docx":
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
    lines = path.read_text(encoding="utf-8").splitlines()
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


def write_output(blocks: list[dict], kind: str, path: Path) -> None:
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
    parser.add_argument("input", help="Hindi book: .txt or .docx")
    parser.add_argument("-o", "--output", help="Output file (default: <name>_telugu.<ext>)")
    parser.add_argument("--mode", choices=["transliterate", "translate", "both"],
                        default="transliterate",
                        help="transliterate = same words in Telugu script (default); "
                             "translate = meaning in Telugu; both = script + translation")
    args = parser.parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        sys.exit(f"Input file not found: {in_path}")
    if in_path.suffix.lower() not in (".txt", ".docx"):
        sys.exit("Input must be a .txt or .docx file.")

    out_path = Path(args.output) if args.output else in_path.with_stem(
        in_path.stem + "_telugu"
    )
    kind = "docx" if out_path.suffix.lower() == ".docx" else "txt"

    blocks, _ = read_input(in_path)
    print(f"Read {len(blocks)} paragraphs/lines from {in_path.name}")
    converted = build_blocks(blocks, args.mode)
    write_output(converted, kind, out_path)
    print(f"Wrote {out_path}  (mode={args.mode})")
    print("Note: dandas are kept as Devanagari । ॥ — that is what prints "
          "correctly in Telugu fonts (see the note at the top of the script).")


if __name__ == "__main__":
    main()
