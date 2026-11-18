#!/usr/bin/env python3
"""Hindi / Sanskrit books -> Telugu book translator.

Builds on Babu's Core Architecture (2026-11-17): five stages, end to end.
  Step 1: Document Ingestion — read .txt / .docx / digital .pdf directly.
          Scanned PDFs go through OCR (pytesseract, san+hin) with --ocr.
  Step 2: Segmentation & Classification — split on dandas (U+0964/U+0965)
          and verse numbers; classify each block as sloka / hindi-note /
          sanskrit-note so the pipeline knows how to process it.
  Step 3: Transliteration — Devanagari slokas -> Telugu lipi with
          indic-transliteration (exact phonetic mapping, meter kept).
  Step 4: Translation — notes (Hindi/Sanskrit prose) -> Telugu with
          one of three engines (choose with --engine):
            indictrans2 (default) — AI4Bharat IndicTrans2, free and
              local, no English pivot. NOTE: this model is GATED on
              HuggingFace — you must request access first (see the
              error message if it fails). ~2 GB download, slow on CPU.
            openai — gpt-4o-mini, most accurate on hard Sanskrit.
              Needs OPENAI_API_KEY. (Same key you use for
              make_telugu_book.py.)
            google — Google Translate via deep-translator, free and
              no key. May hit Google's rate limit (5 req/sec, 200k/day
              per IP); if it fails with TooManyRequests, wait or use
              another engine.
            none — keep notes untranslated (transliteration only).
          Sanskrit Sandhi splitting is OPTIONAL and OFF by default:
          IndicTrans2 handles Sanskrit prose reasonably without it.
  Step 5: Reconstruction & Export — slokas (Telugu lipi) + translated
          notes recombined in original order, exported to .docx
          (python-docx) and/or .pdf (ReportLab + Noto Sans Telugu).

Practical notes (differ from the first draft of the architecture):
  * PDF uses ReportLab, NOT WeasyPrint — WeasyPrint needs Pango/Cairo
    system libraries that are painful on Windows; ReportLab renders
    Telugu conjuncts fine and is already proven on Babu's machine.
  * Sandhi splitting (sanskrit_parser) is OPTIONAL and OFF by default:
    IndicTrans2 handles Sanskrit prose reasonably without it. Add it
    later only if a specific passage translates badly.
  * Sentence splitting uses a small regex splitter, not
    indic-nlp-library, so there is one less dependency to install.
  * IndicTrans2 on CPU is SLOW (a few seconds per sentence). For a
    book, run it with --checkpoint (default on): every translated
    block is saved, so Ctrl+C loses nothing and re-running resumes.

Install (once):
  pip install indic-transliteration transformers torch python-docx reportlab pymupdf
  For scanned PDFs also: pip install pytesseract pdf2image pillow
  (and install Tesseract itself, plus poppler for pdf2image.)
  IndicTrans2 model (~2 GB) downloads automatically on first use.

Usage:
  python hindi_sanskrit_to_telugu.py --check
  python hindi_sanskrit_to_telugu.py --text "रामो राजमणिः ॥" --show
  python hindi_sanskrit_to_telugu.py --file book.txt --out book_telugu
  python hindi_sanskrit_to_telugu.py --file book.docx --out book_telugu --format docx
  python hindi_sanskrit_to_telugu.py --file book.pdf --out book_telugu --format pdf
  python hindi_sanskrit_to_telugu.py --file scanned.pdf --ocr --out book_telugu
  python hindi_sanskrit_to_telugu.py --file book.txt --engine openai --out book_telugu
      (needs OPENAI_API_KEY; uses gpt-4o-mini for the notes only)
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

WORK_DIR = "translator_work"
DANDA_SINGLE = "\u0964"   # ।
DANDA_DOUBLE = "\u0965"   # ॥
INDICTRANS_MODEL = "ai4bharat/indictrans2-indic-indic-1B"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Step 1: Ingestion
# ---------------------------------------------------------------------------

def read_txt(path):
    return Path(path).read_text(encoding="utf-8", errors="replace")


def read_docx(path):
    from docx import Document
    doc = Document(str(path))
    parts = []
    for p in doc.paragraphs:
        if p.text.strip():
            parts.append(p.text)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    parts.append(cell.text)
    return "\n\n".join(parts)


def read_pdf(path):
    import fitz  # PyMuPDF
    doc = fitz.open(str(path))
    parts = []
    for page in doc:
        t = page.get_text().strip()
        if t:
            parts.append(t)
    return "\n\n".join(parts)


def read_pdf_ocr(path, dpi=300):
    """Scanned PDF -> Devanagari text via tesseract (san+hin)."""
    from pdf2image import convert_from_path
    import pytesseract
    from PIL import Image
    pages = convert_from_path(str(path), dpi=dpi)
    log(f"  OCR: {len(pages)} page(s) at {dpi} dpi")
    out = []
    for i, img in enumerate(pages, 1):
        if img.mode != "L":
            img = img.convert("L")
        text = pytesseract.image_to_string(img, lang="san+hin")
        out.append(text)
        log(f"  ... page {i}/{len(pages)} OCR done ({len(text)} chars)")
    return "\n\n".join(out)


def ingest(path, use_ocr=False):
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".txt":
        return read_txt(p)
    if suffix == ".docx":
        return read_docx(p)
    if suffix == ".pdf":
        if use_ocr:
            return read_pdf_ocr(p)
        text = read_pdf(p)
        if len(text.strip()) < 50:
            log("  WARNING: digital PDF gave almost no text — it may be "
                "scanned. Re-run with --ocr to use tesseract.")
        return text
    raise ValueError(f"Unsupported input file type: {suffix} "
                     "(use .txt, .docx or .pdf)")


# ---------------------------------------------------------------------------
# Step 2: Segmentation & Classification
# ---------------------------------------------------------------------------

def _is_devanagari(text):
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    n_dev = sum(1 for c in letters if "\u0900" <= c <= "\u097f")
    return n_dev >= len(letters) * 0.5


def _looks_like_sloka(text):
    """A sloka is Devanagari and ends with a double danda, or carries
    a verse number. (Do NOT use 'short + single danda' as a signal —
    Hindi prose sentences also end with a single danda and would be
    misclassified as slokas.)"""
    t = text.strip()
    if not _is_devanagari(t):
        return False
    if t.rstrip().endswith(DANDA_DOUBLE):
        return True
    if re.search(r"॥\s*\d+\s*॥\s*$", t):
        return True
    return False


def segment_text(text):
    """Split into blocks and classify each as 'sloka', 'hindi-note' or
    'sanskrit-note'. Slokas split on the double danda; prose splits on
    blank lines. Returns [{"kind","text"}]."""
    # Normalise line endings and collapse 3+ blank lines.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    blocks = []
    # First split on double danda: each sloka ends with ॥ (optionally
    # followed by a verse number and another ॥).
    parts = re.split(r"(॥\s*(?:\d+\s*॥)?)", text)
    buf = ""
    for part in parts:
        if part.strip().startswith("॥") or re.match(r"^\s*॥", part):
            buf += part
            if buf.strip():
                blocks.append(buf.strip())
            buf = ""
        else:
            buf += part
    if buf.strip():
        blocks.append(buf.strip())
    # Now split prose blocks on blank lines, and classify.
    out = []
    for b in blocks:
        if _looks_like_sloka(b):
            out.append({"kind": "sloka", "text": b})
            continue
        for para in re.split(r"\n\s*\n", b):
            para = para.strip()
            if not para:
                continue
            if _is_devanagari(para):
                # Devanagari prose: Sanskrit if it reads like a verse or
                # carries Sanskrit markers, else Hindi. Heuristic: heavy
                # Sanskrit vocabulary (tatsama) leans Sanskrit; the
                # translation engine handles both, so this tag only
                # chooses the source language code.
                if re.search(r"[ऋॠॡॢॣ]|ः\s|ं\s", para) and len(para) < 500:
                    kind = "sanskrit-note"
                else:
                    kind = "hindi-note"
                out.append({"kind": kind, "text": para})
            else:
                # Non-Devanagari prose (already Telugu / English / Latin):
                # keep as-is, tag as a note so it is not transliterated.
                out.append({"kind": "note", "text": para})
    return out


# ---------------------------------------------------------------------------
# Step 3: Transliteration (Devanagari -> Telugu lipi)
# ---------------------------------------------------------------------------

def transliterate_sloka(text):
    """Devanagari -> Telugu lipi. Trust indic-transliteration's output
    as-is: do NOT apply the aggressive Whisper-era normaliser here —
    it converts a real retroflex 'ణ' (as in मणि -> మణి) into an
    anusvara 'ం', corrupting proper Sanskrit."""
    from indic_transliteration import sanscript
    from indic_transliteration.sanscript import transliterate
    return transliterate(text, sanscript.DEVANAGARI, sanscript.TELUGU)


# ---------------------------------------------------------------------------
# Step 4: Translation (notes -> Telugu)
# ---------------------------------------------------------------------------

def split_sentences(text):
    """Small regex sentence splitter (no indic-nlp-library needed).
    Splits on । ॥ . ? ! followed by space/newline, keeping the mark."""
    parts = re.split(r"(?<=[।॥\.?!])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


class IndicTrans2Translator:
    """IndicTrans2 (ai4bharat/indictrans2-indic-indic-1B) via transformers.
    Loaded lazily; model downloads (~2 GB) on first use."""

    def __init__(self, model=INDICTRANS_MODEL):
        self.model_name = model
        self.tokenizer = None
        self.model = None

    def load(self):
        if self.model is not None:
            return
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        log(f"  loading IndicTrans2 ({self.model_name}) — first run "
            "downloads ~2 GB, please wait ...")
        t0 = time.time()
        try:
            self.tokenizer = AutoTokenizer.from_pretrained(
                self.model_name, trust_remote_code=True)
            self.model = AutoModelForSeq2SeqLM.from_pretrained(
                self.model_name, trust_remote_code=True)
        except Exception as exc:
            msg = str(exc)
            if "gated" in msg.lower() or "401" in msg or "restricted" in msg.lower():
                raise RuntimeError(
                    "IndicTrans2 is a GATED model on HuggingFace — it is "
                    "not freely downloadable. To use it:\n"
                    "  1. Create a free account at https://huggingface.co\n"
                    "  2. Open https://huggingface.co/"
                    "ai4bharat/indictrans2-indic-indic-1B and click "
                    "'Request access'.\n"
                    "  3. Once approved, create a token at "
                    "https://huggingface.co/settings/tokens\n"
                    "  4. Run: huggingface-cli login (paste the token), "
                    "or set HF_TOKEN in your environment.\n"
                    "  Then re-run this script.\n"
                    "Alternatively, use --engine openai (needs "
                    "OPENAI_API_KEY) or --engine none (transliteration "
                    "only).")
            raise
        log(f"  model loaded in {time.time() - t0:.0f}s (CPU)")

    def translate(self, text, src_lang, tgt_lang="tel_Telu"):
        """Translate one string. src_lang like 'hin_Deva' / 'san_Deva'."""
        self.load()
        import torch
        sentences = split_sentences(text)
        if not sentences:
            return ""
        batch = self.tokenizer(
            [f"{src_lang}: {s}" for s in sentences],
            return_tensors="pt", padding=True, truncation=True,
            max_length=256)
        # IndicTrans2 uses language-code tokens as BOS.
        bos = self.tokenizer.convert_tokens_to_ids(tgt_lang)
        with torch.no_grad():
            out = self.model.generate(
                **batch, forced_bos_token_id=bos, max_length=256,
                num_beams=5)
        decoded = self.tokenizer.batch_decode(
            out, skip_special_tokens=True)
        return " ".join(d.strip() for d in decoded)


def translate_google(text, src_lang, tgt_lang="te"):
    """Google Translate via deep-translator (free, no key). May hit
    Google's rate limit on shared/datacentre IPs; if it fails with
    TooManyRequests, wait a while or use another engine."""
    from deep_translator import GoogleTranslator
    src = {"hin_Deva": "hi", "san_Deva": "sa"}.get(src_lang, "auto")
    out = []
    for sent in split_sentences(text):
        out.append(GoogleTranslator(source=src, target=tgt_lang)
                   .translate(sent))
    return " ".join(out)


def translate_openai(text, src_lang, tgt_lang="Telugu"):
    """OpenAI gpt-4o-mini for the notes only (needs OPENAI_API_KEY)."""
    from openai import OpenAI
    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Set it first:\n"
            "  Windows PowerShell:  $env:OPENAI_API_KEY=<redacted>\n"
            "  Linux / Mac:         export OPENAI_API_KEY=<redacted>")
    client = OpenAI(api_key=key)
    src_name = {"hin_Deva": "Hindi", "san_Deva": "Sanskrit"}.get(
        src_lang, src_lang)
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system",
             "content": ("You translate Indian-language prose into Telugu. "
                         "Translate accurately and naturally, keeping proper "
                         "names as they are. Reply with the Telugu "
                         "translation only, no explanation.")},
            {"role": "user",
             "content": f"Translate this {src_name} text into Telugu:\n\n{text}"},
        ],
        temperature=0.2)
    return resp.choices[0].message.content.strip()


# ---------------------------------------------------------------------------
# Step 5: Reconstruction & Export
# ---------------------------------------------------------------------------

def find_telugu_font():
    candidates = [Path(os.path.expandvars(
                        r"%LOCALAPPDATA%\Microsoft\Windows\Fonts")),
                  Path("C:/Windows/Fonts"),
                  Path("/usr/share/fonts/truetype/noto"),
                  Path("/usr/share/fonts"),
                  Path.cwd() / "fonts"]
    for folder in candidates:
        if not folder.is_dir():
            continue
        reg = folder / "NotoSansTelugu-Regular.ttf"
        bold = folder / "NotoSansTelugu-Bold.ttf"
        if reg.exists() and bold.exists():
            return str(reg), str(bold)
        for f in folder.rglob("*.ttf"):
            if "telugu" in f.name.lower() and "regular" in f.name.lower():
                reg = f
            if "telugu" in f.name.lower() and "bold" in f.name.lower():
                bold = f
        if reg and bold:
            return str(reg), str(bold)
    raise RuntimeError(
        "No Telugu font found. Install Noto Sans Telugu "
        "(https://fonts.google.com/noto/specimen/Noto+Sans+Telugu).")


def build_docx(out_path, title, items):
    """items: [{"kind","original","telugu"}]. Slokas bold+centred,
    notes indented."""
    from docx import Document
    from docx.shared import Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Noto Sans Telugu"
    style.font.size = Pt(12)
    h = doc.add_heading(title, level=1)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for it in items:
        if it["kind"] == "sloka":
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            r = p.add_run(it["telugu"])
            r.bold = True
            r.font.size = Pt(13)
        else:
            p = doc.add_paragraph(it["telugu"])
            p.paragraph_format.left_indent = Pt(18)
            p.paragraph_format.space_after = Pt(6)
    doc.save(str(out_path))
    log(f"DOCX written: {out_path}")


def build_pdf(out_path, title, items):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                    HRFlowable)
    reg, bold = find_telugu_font()
    pdfmetrics.registerFont(TTFont("Telugu", reg))
    pdfmetrics.registerFont(TTFont("Telugu-Bold", bold))
    DARK = HexColor("#1a1a6e")
    GREY = HexColor("#555555")

    def esc(t):
        return (t.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("\n", "<br/>"))

    title_style = ParagraphStyle("title", fontName="Telugu-Bold", fontSize=19,
                                 leading=27, alignment=TA_CENTER,
                                 textColor=DARK, spaceAfter=4)
    sloka_style = ParagraphStyle("sloka", fontName="Telugu-Bold",
                                 fontSize=14, leading=22,
                                 alignment=TA_CENTER, spaceBefore=10,
                                 spaceAfter=6)
    note_style = ParagraphStyle("note", fontName="Telugu", fontSize=12.5,
                                leading=20, alignment=TA_JUSTIFY,
                                leftIndent=12, spaceAfter=4)
    doc = SimpleDocTemplate(str(out_path), pagesize=A4,
                            leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm,
                            title=title)
    story = [Paragraph(esc(title), title_style),
             HRFlowable(width="100%", thickness=1, color=DARK,
                        spaceAfter=6)]
    for it in items:
        if it["kind"] == "sloka":
            story.append(Paragraph(esc(it["telugu"]), sloka_style))
        else:
            story.append(Paragraph(esc(it["telugu"]), note_style))
    doc.build(story)
    log(f"PDF written: {out_path}")


# ---------------------------------------------------------------------------
# Pipeline driver
# ---------------------------------------------------------------------------

SRC_LANG = {"hindi-note": "hin_Deva", "sanskrit-note": "san_Deva",
            "note": "hin_Deva"}


def run_check():
    problems = []
    for pkg in ("indic_transliteration", "docx", "reportlab"):
        try:
            __import__(pkg)
        except ImportError:
            problems.append(f"Python package missing: {pkg}")
    try:
        import fitz  # noqa: F401
    except ImportError:
        problems.append("PyMuPDF missing (pip install pymupdf) — needed "
                        "for .pdf input")
    try:
        import transformers  # noqa: F401
        import torch  # noqa: F401
    except ImportError:
        problems.append("transformers/torch missing (pip install "
                        "transformers torch) — needed for IndicTrans2")
    try:
        find_telugu_font()
    except RuntimeError as exc:
        problems.append(str(exc))
    if problems:
        print("Setup check FAILED:")
        for p in problems:
            print("  -", p)
        return 1
    print("Setup check OK: indic-transliteration, python-docx, reportlab, "
          "PyMuPDF, transformers/torch, Telugu font all ready.")
    print("IndicTrans2 model downloads (~2 GB) on first translation run.")
    return 0


def translate_items(items, engine, work, checkpoint=True):
    """Translate/Transliterate every block. Slokas -> Telugu lipi only.
    Notes -> Telugu translation. Returns items with 'telugu' filled.
    Checkpointed so a long book resumes after Ctrl+C."""
    ckpt = Path(work) / "checkpoint.json" if work else None
    done = {}
    if checkpoint and ckpt and ckpt.exists():
        try:
            saved = json.loads(ckpt.read_text(encoding="utf-8"))
            done = {str(i): v for i, v in saved.items()}
            log(f"  resuming from checkpoint ({len(done)} blocks done)")
        except (OSError, ValueError):
            done = {}
    translator = None
    if any(it["kind"] in SRC_LANG for it in items) and engine == "indictrans2":
        translator = IndicTrans2Translator()
    for idx, it in enumerate(items):
        key = str(idx)
        if key in done:
            it["telugu"] = done[key]
            continue
        if it["kind"] == "sloka":
            it["telugu"] = transliterate_sloka(it["text"])
        elif it["kind"] in SRC_LANG:
            src = SRC_LANG[it["kind"]]
            if engine == "openai":
                it["telugu"] = translate_openai(it["text"], src)
            elif engine == "google":
                it["telugu"] = translate_google(it["text"], src)
            elif engine == "none":
                it["telugu"] = it["text"]
            else:
                it["telugu"] = translator.translate(it["text"], src)
        else:  # plain note: keep as-is
            it["telugu"] = it["text"]
        if checkpoint and ckpt:
            done[key] = it["telugu"]
            ckpt.parent.mkdir(parents=True, exist_ok=True)
            ckpt.write_text(json.dumps(done, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        log(f"  ... block {idx + 1}/{len(items)} [{it['kind']}] done")
    if checkpoint and ckpt:
        ckpt.unlink(missing_ok=True)
    return items


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--text", help="text to translate (paste it here)")
    ap.add_argument("--file", help="input file (.txt, .docx, .pdf)")
    ap.add_argument("--ocr", action="store_true",
                    help="use tesseract OCR for a scanned .pdf")
    ap.add_argument("--out", help="output base name (no extension)")
    ap.add_argument("--format", choices=["docx", "pdf", "both", "txt"],
                    default="both",
                    help="output format (default both docx+pdf)")
    ap.add_argument("--engine",
                    choices=["indictrans2", "openai", "google", "none"],
                    default="indictrans2",
                    help="translation engine for notes (default "
                         "indictrans2, free/local but GATED on "
                         "HuggingFace; 'openai' needs OPENAI_API_KEY; "
                         "'google' is free/no-key but may rate-limit; "
                         "'none' keeps notes untranslated)")
    ap.add_argument("--show", action="store_true",
                    help="print the Telugu result to the console")
    ap.add_argument("--check", action="store_true", help="setup check only")
    args = ap.parse_args()

    if args.check:
        return run_check()
    if args.text:
        raw = args.text
        title = "Translation"
    elif args.file:
        raw = ingest(args.file, use_ocr=args.ocr)
        title = Path(args.file).stem
    else:
        print("Give --text \"...\" or --file book.txt/.docx/.pdf")
        return 2
    log(f"  input: {len(raw)} chars")
    items = segment_text(raw)
    n_sloka = sum(1 for it in items if it["kind"] == "sloka")
    n_note = len(items) - n_sloka
    log(f"  segmented into {len(items)} blocks: "
        f"{n_sloka} sloka(s), {n_note} note(s)")
    if not items:
        print("No text found to translate.")
        return 1
    work = Path(WORK_DIR)
    work.mkdir(parents=True, exist_ok=True)
    items = translate_items(items, args.engine, work)
    # Save plain-text transcript too.
    (work / "transcript.txt").write_text(
        "\n\n".join(it["telugu"] for it in items), encoding="utf-8")
    (work / "segments.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"  transcript saved: {work / 'transcript.txt'}")
    if args.show or not args.out:
        print("\n===== Telugu output =====\n")
        for it in items:
            tag = "SLOKA" if it["kind"] == "sloka" else "NOTE"
            print(f"[{tag}] {it['telugu']}\n")
    if args.out:
        base = args.out
        if args.format in ("docx", "both"):
            build_docx(f"{base}.docx", title, items)
        if args.format in ("pdf", "both"):
            build_pdf(f"{base}.pdf", title, items)
        if args.format == "txt":
            Path(f"{base}.txt").write_text(
                "\n\n".join(it["telugu"] for it in items), encoding="utf-8")
            log(f"TXT written: {base}.txt")
    log("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
