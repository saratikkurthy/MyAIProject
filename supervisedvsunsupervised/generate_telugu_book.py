# -*- coding: utf-8 -*-
"""
make_telugu_book.py -- Turn a YouTube video, a scanned PDF, or a text file
into a nice Telugu PDF book, with AI doing the hard work.

WHAT IT DOES (the same pipeline that made the Mrityunjaya Stotram book):
  1. INPUT
       - YouTube video URL : downloads the audio (yt-dlp) and transcribes
         it to text with Whisper AI (faster-whisper, runs on your machine,
         no key needed). Works for Telugu / Sanskrit chanting and speech.
       - Scanned image PDF : reads each page with OCR (tesseract, Sanskrit
         + Hindi). Text PDFs are read directly, no OCR needed.
       - .txt / .docx      : read directly.
  2. SLOKAS
       The text is split into slokas (verse blocks).
  3. TELUGU LIPI
       Every sloka is written in Telugu script (transliteration, offline,
       exact) and normalised to standard Telugu stotram spelling
       (మృత్యుంజయ not మృత్యుఞ్జయ, చంద్ర not చన్ద్ర, స్తోత్రం not స్తోత్రమ్).
  4. TRANSLATION + SUMMARY (OpenAI API)
       Each sloka is translated into Telugu and given a 1-2 line meaning
       (భావం). At the end, one overall summary of the whole book is made.
  5. PDF BOOK
       A clean A4 Telugu PDF: title page, source link, slokas with
       timestamps (YouTube) or page numbers (PDF), translation, meaning,
       and the overall summary.

YOU NEED:
  - Python packages: openai, faster-whisper (only for YouTube),
    indic-transliteration, python-docx, pymupdf, reportlab, Pillow
    Install:  pip install openai faster-whisper indic-transliteration
              python-docx pymupdf reportlab Pillow
  - tesseract with Sanskrit + Hindi (only for scanned PDFs).
    Windows: UB-Mannheim installer, tick Sanskrit + Hindi, add to PATH.
  - A Telugu font for the PDF (Noto Sans Telugu is searched automatically).
  - An OpenAI API key for translation (https://platform.openai.com/api-keys).
    It is a PAID service - gpt-4o-mini costs only a few paise per page,
    but set a spending limit in your OpenAI account. The key is read from
    the OPENAI_API_KEY environment variable, NEVER written in this file.

HOW TO RUN (edit the SETTINGS below, or pass arguments):
  python make_telugu_book.py --youtube "https://www.youtube.com/watch?v=XXXX"
  python make_telugu_book.py --pdf "book.pdf" --source sa
  python make_telugu_book.py --text "slokas.txt" --source hi
  python make_telugu_book.py --check          (checks everything is ready)
  python make_telugu_book.py --dry-run ...    (builds the book with
                                               transliteration only, no
                                               API call, no money spent)
  python make_telugu_book.py --resume ...     (continue a stopped run)

SETTINGS: fill these in (or use the command-line flags, which win).
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------- settings
INPUT_URL = ""        # YouTube video URL (for --youtube)
INPUT_PATH = ""       # .pdf / .txt / .docx file (for --pdf / --text / --docx)
OUTPUT_PATH = ""      # output PDF; default = <input name>_telugu_book.pdf
SOURCE_LANG = "sa"    # "sa" = Sanskrit, "hi" = Hindi
MODEL = "gpt-4o-mini"  # OpenAI model for translation (cheap + good)
WHISPER_MODEL = "small"  # Whisper model for YouTube audio (tiny/base/small/medium)
BATCH_SIZE = 5        # slokas sent to OpenAI in one call
DPI = 300             # render resolution for scanned PDF pages
TESSERACT_CMD = "tesseract"
FONT_DIR = ""         # folder with NotoSansTelugu-Regular.ttf (auto-found if blank)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")  # NEVER hard-code your key

LANG_NAME = {"sa": "Sanskrit", "hi": "Hindi", "te": "Telugu"}

# ---------------------------------------------------------------- helpers


def log(msg):
    print(msg, flush=True)


def _read_text_file(path):
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-8", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp1252", errors="replace")


def read_txt(path):
    return _read_text_file(path).splitlines()


def read_docx(path):
    from docx import Document
    doc = Document(str(path))
    lines = []
    for p in doc.paragraphs:
        lines.append(p.text)
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                lines.append(cell.text)
    return lines


def _crop_to_page(pil_img):
    """Crop a rendered page to the bright page area (photo on dark background).
    Returns the original image unchanged when no crop is safe."""
    try:
        from PIL import Image
    except ImportError:
        return pil_img
    thumb = pil_img.convert("L").resize((120, 160))
    w, h = thumb.size
    px = thumb.load()
    bright_rows = []
    for y in range(h):
        bright = sum(1 for x in range(w) if px[x, y] > 100)
        if bright > w * 0.30:
            bright_rows.append(y)
    if not bright_rows:
        return pil_img
    y0, y1 = bright_rows[0], bright_rows[-1]
    bright_cols = []
    for x in range(w):
        bright = sum(1 for y in range(y0, y1 + 1) if px[x, y] > 100)
        if bright > (y1 - y0 + 1) * 0.30:
            bright_cols.append(x)
    if not bright_cols:
        return pil_img
    x0, x1 = bright_cols[0], bright_cols[-1]
    sx, sy = pil_img.width / w, pil_img.height / h
    box = (int(x0 * sx), int(y0 * sy), int(x1 * sx), int(y1 * sy))
    bw, bh = box[2] - box[0], box[3] - box[1]
    if bw * bh > 0.85 * pil_img.width * pil_img.height:
        return pil_img
    m = 0.02
    box = (max(0, int(box[0] - bw * m)), max(0, int(box[1] - bh * m)),
           min(pil_img.width, int(box[2] + bw * m)),
           min(pil_img.height, int(box[3] + bh * m)))
    return pil_img.crop(box)


def ocr_page_image(pil_img, lang="san+hin"):
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        pil_img.save(tmp.name)
        tmp_path = tmp.name
    try:
        out = subprocess.run(
            [TESSERACT_CMD, tmp_path, "stdout", "-l", lang, "--psm", "6"],
            capture_output=True, text=True, timeout=300)
        return out.stdout
    finally:
        os.unlink(tmp_path)


def read_pdf(path):
    """Return a list of (page_no, text). Scanned pages are OCR'd."""
    import fitz
    doc = fitz.open(str(path))
    pages = []
    for i, page in enumerate(doc, start=1):
        text = page.get_text().strip()
        if len(text) >= 100:
            pages.append((i, text))
            continue
        # scanned page: render and OCR
        pix = page.get_pixmap(dpi=DPI)
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        img = _crop_to_page(img)
        text = ocr_page_image(img, "san+hin" if SOURCE_LANG == "sa" else "hin+san")
        pages.append((i, text))
        log(f"  page {i}: OCR read {len(text)} chars")
    return pages


# ---------------------------------------------------------------- slokas

VERSE_END = re.compile(r"॥\s*(?:[०-९0-9]+\s*॥)?\s*$")
FOOTER_PATTERNS = [re.compile(p, re.I) for p in
                   (r"eGangotri", r"Dharmartha Trust", r"Digitized by")]


def clean_ocr_text(text):
    lines = []
    for line in text.splitlines():
        if any(p.search(line) for p in FOOTER_PATTERNS):
            continue
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


def split_slokas(lines):
    """Split lines into sloka blocks (blank line, or after a ॥n॥ line)."""
    blocks = []
    cur = []
    for line in lines:
        line = line.rstrip()
        if re.match(r"^\[page \d+\]$", line.strip()):
            continue  # page marker, not part of any sloka
        if not line.strip():
            if cur:
                blocks.append("\n".join(cur).strip())
                cur = []
            continue
        cur.append(line)
        if VERSE_END.search(line):
            blocks.append("\n".join(cur).strip())
            cur = []
    if cur:
        blocks.append("\n".join(cur).strip())
    return [b for b in blocks if b and len(b) >= 2]


# ---------------------------------------------------------------- Telugu lipi

def transliterate(text):
    from indic_transliteration import sanscript
    from indic_transliteration.sanscript import transliterate
    return transliterate(text, sanscript.DEVANAGARI, sanscript.TELUGU)


def normalize_telugu(s):
    """Literal transliteration -> standard Telugu stotram spelling.
    (మృత్యుఞ్జయ -> మృత్యుంజయ, చన్ద్ర -> చంద్ర, స్తోత్రమ్ -> స్తోత్రం)"""
    pairs = [
        ("ఙ్క", "ంక"), ("ఙ్ఖ", "ంఖ"), ("ఙ్గ", "ంగ"), ("ఙ్ఘ", "ంఘ"),
        ("ఞ్చ", "ంచ"), ("ఞ్ఛ", "ంఛ"), ("ఞ్జ", "ంజ"), ("ఞ్ఝ", "ంఝ"),
        ("ణ్ట", "ంట"), ("ణ్ఠ", "ంఠ"), ("ణ్డ", "ండ"), ("ణ్ఢ", "ంఢ"),
        ("న్త", "ంత"), ("న్థ", "ంథ"), ("న్ద", "ంద"), ("న్ధ", "ంధ"),
        ("న్ప", "ంప"), ("న్ఫ", "ంఫ"), ("న్బ", "ంబ"), ("న్భ", "ంభ"),
        ("న్శ", "ంశ"), ("న్ష", "ంష"), ("న్స", "ంస"), ("న్హ", "ంహ"),
        ("న్జ", "ంజ"), ("న్చ", "ంచ"),
        ("మ్ప", "ంప"), ("మ్ఫ", "ంఫ"), ("మ్బ", "ంబ"), ("మ్భ", "ంభ"),
        ("మ్శ", "ంశ"), ("మ్ష", "ంష"), ("మ్స", "ంస"), ("మ్హ", "ంహ"),
        ("మ్జ", "ంజ"), ("మ్చ", "ంచ"),
    ]
    for a, b in pairs:
        s = s.replace(a, b)
    s = re.sub(r"మ్(?=\s|$)", "ం", s)
    return s


def to_telugu_lipi(text):
    return normalize_telugu(transliterate(text))


# ---------------------------------------------------------------- YouTube + Whisper AI

def download_youtube_audio(url, out_dir):
    """Download a YouTube video's audio. Returns (audio_path, info dict)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / "audio"
    # android client avoids the sign-in bot check; format 18 has audio
    cmd = ["yt-dlp", "--no-warnings", "--no-check-certificates",
           "--extractor-args", "youtube:player_client=android",
           "-f", "18/best", "--output", str(base) + ".%(ext)s",
           "--print", "%(title)s", "--print", "%(duration)s",
           "--print", "%(uploader)s", "--print", "%(id)s", url]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        raise RuntimeError("yt-dlp failed:\n" + (r.stderr or r.stdout)[-800:])
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    info = {"title": lines[0] if lines else "",
            "duration": lines[1] if len(lines) > 1 else "",
            "uploader": lines[2] if len(lines) > 2 else "",
            "id": lines[3] if len(lines) > 3 else ""}
    media = list(out_dir.glob("audio.*"))
    if not media:
        raise RuntimeError("yt-dlp finished but no audio file was saved.")
    media = media[0]
    wav = out_dir / "audio.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(media),
                    "-vn", "-ac", "1", "-ar", "16000", str(wav)],
                   check=True, timeout=600)
    return wav, info


def transcribe_audio(wav_path, model_name="small", lang="te"):
    """Transcribe audio with Whisper AI. Returns segments with timestamps.
    Uses an initial prompt (the refrain) so chanted verses are not skipped,
    then re-checks any long silent stretch in short chunks."""
    from faster_whisper import WhisperModel
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    prompt = ("रुद्रं पशुपतिं स्थाणुं नीलकण्ठमुमापतिम्। "
              "नमामि शिरसा देवं किं नो मृत्युः करिष्यति।")
    segments, info = model.transcribe(
        str(wav_path), language=lang, beam_size=5, vad_filter=False,
        initial_prompt=prompt)
    segs = [{"start": round(s.start, 1), "end": round(s.end, 1),
             "text": s.text.strip()} for s in segments if s.text.strip()]
    # re-check long gaps in 15-second chunks (Whisper skips chanted parts)
    total = info.duration
    covered = sorted((s["start"], s["end"]) for s in segs)
    gaps = []
    prev_end = 0.0
    for a, b in covered:
        if a - prev_end > 25:
            gaps.append((prev_end, a))
        prev_end = max(prev_end, b)
    if total - prev_end > 25:
        gaps.append((prev_end, total))
    for g0, g1 in gaps:
        t = g0
        while t + 15 <= g1:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_path = tmp.name
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(t),
                            "-t", "15", "-i", str(wav_path), tmp_path],
                           check=True, timeout=120)
            try:
                c_segs, _ = model.transcribe(
                    tmp_path, language=lang, beam_size=5, vad_filter=False,
                    initial_prompt=prompt)
                for s in c_segs:
                    if s.text.strip():
                        segs.append({"start": round(s.start + t, 1),
                                     "end": round(s.end + t, 1),
                                     "text": s.text.strip()})
            finally:
                os.unlink(tmp_path)
            t += 15
    segs.sort(key=lambda s: s["start"])
    return segs, total


# ---------------------------------------------------------------- OpenAI translation AI

def _openai_client():
    from openai import OpenAI
    key = OPENAI_API_KEY or os.getenv("OPENAI_API_KEY", "")
    if not key:
        raise RuntimeError(
            "No OpenAI API key. Set it first:\n"
            "  Windows PowerShell:  $env:OPENAI_API_KEY = 'sk-...'\n"
            "  Linux / Mac:         export OPENAI_API_KEY='sk-...'\n"
            "Get a key at https://platform.openai.com/api-keys")
    return OpenAI(api_key=key)


def translate_batch(client, blocks, source_lang, needs_cleanup=False):
    """Translate a batch of slokas. Returns a list of dicts in the same
    order as blocks. Each dict has translation + summary, and (when
    needs_cleanup is True, i.e. the text came from Whisper) a "corrected"
    field with the cleaned-up original Sanskrit/Hindi verse."""
    lang = LANG_NAME.get(source_lang, "Sanskrit")
    numbered = "\n\n".join(f"[{i+1}]\n{b}" for i, b in enumerate(blocks))
    if needs_cleanup:
        system = (
            f"The numbered items below are a Whisper AI transcript of a {lang} "
            "devotional recitation. The machine misheard some words (wrong "
            "letters, mixed scripts, even stray foreign words). For each item: "
            "FIRST reconstruct the correct original verse - fix the misheard "
            "words using your knowledge of the stotram and the context, keep "
            "the verse order and meaning, and if a line is too garbled to "
            "recover, write it as heard and mark it [unclear]. "
            "THEN translate the corrected verse into clear, natural, "
            "devotional Telugu, and write a 1-2 line meaning in simple Telugu. "
            "Reply with ONLY a JSON object: "
            '{"items": [{"corrected": "...", "translation": "...", '
            '"summary": "..."}, ...]} with exactly one item per numbered '
            "sloka, in the same order.")
    else:
        system = (
            f"You are an expert translator of {lang} devotional literature into Telugu. "
            "Translate each numbered sloka into clear, natural, devotional Telugu. "
            "Keep the meaning faithful; do not add or remove ideas. "
            "For each sloka also write a 1-2 line meaning (భావం) in simple Telugu. "
            "Reply with ONLY a JSON object: "
            '{"items": [{"translation": "...", "summary": "..."}, ...]} '
            "with exactly one item per numbered sloka, in the same order.")
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": numbered}]
    last_err = None
    for attempt in range(4):
        try:
            resp = client.chat.completions.create(
                model=MODEL, messages=messages, temperature=0.2,
                response_format={"type": "json_object"})
            data = json.loads(resp.choices[0].message.content)
            items = data.get("items", [])
            if len(items) != len(blocks):
                raise ValueError(
                    f"OpenAI returned {len(items)} items for {len(blocks)} slokas")
            usage = getattr(resp, "usage", None)
            if usage:
                log(f"    tokens: in={usage.prompt_tokens} "
                    f"out={usage.completion_tokens}")
            return [{"corrected": str(it.get("corrected", "")).strip(),
                     "translation": str(it.get("translation", "")).strip(),
                     "summary": str(it.get("summary", "")).strip()}
                    for it in items]
        except Exception as exc:
            last_err = exc
            wait = 15 * (attempt + 1)
            log(f"    OpenAI call failed ({exc}); retrying in {wait}s...")
            time.sleep(wait)
    raise RuntimeError(f"OpenAI translation failed after retries: {last_err}")


def overall_summary(client, per_sloka_summaries, title):
    """One overall summary of the whole book, in Telugu."""
    joined = "\n".join(f"- {s}" for s in per_sloka_summaries if s)
    messages = [
        {"role": "system", "content": (
            "You summarise devotional books in Telugu. Write a short, warm "
            "overall summary (6-10 lines) in simple Telugu: what this text "
            "is, its main message, and what the reader gains from it.")},
        {"role": "user", "content": f"Title: {title}\n\nSloka meanings:\n{joined}"}]
    resp = client.chat.completions.create(
        model=MODEL, messages=messages, temperature=0.3)
    return resp.choices[0].message.content.strip()


# ---------------------------------------------------------------- PDF book

def find_telugu_font():
    candidates = []
    if FONT_DIR:
        candidates.append(Path(FONT_DIR))
    candidates += [Path("/usr/share/fonts/truetype/noto"),
                   Path("/usr/share/fonts"),
                   Path("C:/Windows/Fonts"),
                   Path(os.path.expandvars(
                       r"%LOCALAPPDATA%\Microsoft\Windows\Fonts")),
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
    raise RuntimeError("No Telugu font found. Install Noto Sans Telugu.")


def build_pdf(out_path, title, title_en, source_line, blocks, overall):
    """blocks: list of dicts with keys: heading, original, telugu_lipi,
    translation, summary. overall: overall summary text (Telugu)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                    HRFlowable)

    reg, bold = find_telugu_font()
    pdfmetrics.registerFont(TTFont("Telugu", reg))
    pdfmetrics.registerFont(TTFont("Telugu-Bold", bold))
    import glob as _glob
    _dev = _glob.glob("/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf")
    if _dev:
        pdfmetrics.registerFont(TTFont("Devanagari", _dev[0]))
    DARK = HexColor("#1a1a6e")
    GREY = HexColor("#555555")
    BLUE = HexColor("#0b5394")

    def esc(t):
        return (t.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("\n", "<br/>"))

    title_style = ParagraphStyle("title", fontName="Telugu-Bold", fontSize=20,
                                 leading=28, alignment=TA_CENTER,
                                 textColor=DARK, spaceAfter=4)
    sub_style = ParagraphStyle("sub", fontName="Helvetica", fontSize=11,
                               leading=16, alignment=TA_CENTER,
                               textColor=GREY, spaceAfter=2)
    link_style = ParagraphStyle("link", fontName="Helvetica", fontSize=10,
                                leading=15, alignment=TA_CENTER,
                                textColor=BLUE, spaceAfter=6)
    head_style = ParagraphStyle("head", fontName="Telugu-Bold", fontSize=13,
                                leading=18, textColor=DARK, spaceBefore=14,
                                spaceAfter=4)
    body_style = ParagraphStyle("body", fontName="Telugu", fontSize=13,
                                leading=21, spaceAfter=2)
    dev_style = ParagraphStyle("dev", fontName="Devanagari", fontSize=13,
                               leading=21, spaceAfter=2)
    trans_style = ParagraphStyle("trans", fontName="Telugu", fontSize=12,
                                 leading=19, spaceAfter=2, textColor=GREY)
    note_style = ParagraphStyle("note", fontName="Telugu", fontSize=10.5,
                                leading=16, textColor=GREY, spaceBefore=10)

    doc = SimpleDocTemplate(str(out_path), pagesize=A4,
                            leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm,
                            title=title)
    story = [Paragraph(esc(title), title_style)]
    if title_en:
        story.append(Paragraph(esc(title_en), sub_style))
    if source_line:
        story.append(Paragraph(esc(source_line), link_style))
    story.append(HRFlowable(width="100%", thickness=1, color=DARK,
                            spaceAfter=6))
    for b in blocks:
        story.append(Paragraph(esc(b["heading"]), head_style))
        if b.get("original"):
            for line in b["original"].splitlines():
                story.append(Paragraph(esc(line), dev_style))
        if b.get("telugu_lipi"):
            story.append(Paragraph("<b>తెలుగు లిపి</b>", trans_style))
            for line in b["telugu_lipi"].splitlines():
                story.append(Paragraph(esc(line), body_style))
        if b.get("translation"):
            story.append(Paragraph("<b>అనువాదం</b>", trans_style))
            for line in b["translation"].splitlines():
                story.append(Paragraph(esc(line), body_style))
        if b.get("summary"):
            story.append(Paragraph(f"<b>భావం:</b> {esc(b['summary'])}",
                                   trans_style))
    if overall:
        story.append(HRFlowable(width="100%", thickness=1, color=DARK,
                                spaceBefore=14, spaceAfter=6))
        story.append(Paragraph("సారాంశం", head_style))
        for line in overall.splitlines():
            story.append(Paragraph(esc(line), body_style))
    story.append(Paragraph(
        "గమనిక: ఈ పుస్తకం యంత్రం ద్వారా తయారైంది. అర్థంలో సందేహం "
        "ఉంటే మూలాన్ని చూడండి.",
        note_style))
    doc.build(story)
    log(f"PDF written: {out_path}")


# ---------------------------------------------------------------- checks

def run_check():
    problems = []
    for pkg in ("openai", "indic_transliteration", "docx", "fitz",
                "reportlab", "PIL"):
        try:
            __import__(pkg)
        except ImportError:
            problems.append(f"Python package missing: {pkg}")
    try:
        __import__("faster_whisper")
    except ImportError:
        problems.append("faster-whisper missing (needed only for YouTube)")
    def _which(cmd, *a):
        try:
            return subprocess.run([cmd, *a], capture_output=True).returncode == 0
        except FileNotFoundError:
            return False

    if not _which("yt-dlp", "--version"):
        problems.append("yt-dlp not found (needed only for YouTube)")
    if not _which("ffmpeg", "-version"):
        problems.append("ffmpeg not found (needed for YouTube + scanned PDF)")
    t = None
    try:
        t = subprocess.run([TESSERACT_CMD, "--version"], capture_output=True)
    except FileNotFoundError:
        t = None
    if t is None or t.returncode:
        problems.append("tesseract not found (needed only for scanned PDFs)")
    else:
        langs = subprocess.run([TESSERACT_CMD, "--list-langs"],
                               capture_output=True, text=True).stdout
        for code in ("san", "hin"):
            if code not in langs:
                problems.append(f"tesseract language '{code}' not installed")
    try:
        find_telugu_font()
    except RuntimeError as exc:
        problems.append(str(exc))
    if not (OPENAI_API_KEY or os.getenv("OPENAI_API_KEY")):
        problems.append("OPENAI_API_KEY is not set (needed for translation)")
    if problems:
        print("Setup check FAILED:")
        for p in problems:
            print("  -", p)
        return 1
    print("Setup check OK: packages, yt-dlp, ffmpeg, tesseract, Telugu font, "
          "OpenAI key all ready.")
    return 0


# ---------------------------------------------------------------- main

def fmt_time(seconds):
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def main():
    global MODEL
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--youtube", help="YouTube video URL")
    ap.add_argument("--pdf", help="input PDF (text or scanned image)")
    ap.add_argument("--text", help="input .txt file")
    ap.add_argument("--docx", help="input .docx file")
    ap.add_argument("--source", choices=["sa", "hi"], default=SOURCE_LANG,
                    help="source language: sa=Sanskrit, hi=Hindi")
    ap.add_argument("--model", default=MODEL, help="OpenAI model")
    ap.add_argument("--output", help="output PDF path")
    ap.add_argument("--title", help="book title (Telugu)")
    ap.add_argument("--dry-run", action="store_true",
                    help="transliteration only, no OpenAI call")
    ap.add_argument("--resume", action="store_true",
                    help="reuse the saved translation checkpoint")
    ap.add_argument("--check", action="store_true", help="setup check only")
    ap.add_argument("--pages", help="PDF pages to use, e.g. 1-5 (default all)")
    args = ap.parse_args()

    if args.check:
        return run_check()
    MODEL = args.model
    source_lang = args.source

    # ---- gather the source text + metadata
    title = args.title or ""
    title_en = ""
    source_line = ""
    segments = None  # YouTube: list of {start, end, text}
    raw_lines = []
    work_dir = Path.cwd() / "telugu_book_work"
    work_dir.mkdir(exist_ok=True)

    if args.youtube:
        url = args.youtube
        log(f"Downloading audio: {url}")
        wav, info = download_youtube_audio(url, work_dir)
        title = title or info.get("title", "Telugu Book")
        title_en = info.get("title", "")
        dur = info.get("duration", "")
        mins = fmt_time(float(dur)) if dur.replace(".", "").isdigit() else dur
        source_line = f"Video: {url}  |  Channel: {info.get('uploader', '')}  |  Duration: {mins}"
        log("Transcribing with Whisper AI (this takes a few minutes)...")
        segments, total = transcribe_audio(wav, WHISPER_MODEL, "te")
        log(f"  transcript: {len(segments)} segments, {fmt_time(total)}")
        raw_lines = [s["text"] for s in segments]
    elif args.pdf:
        path = Path(args.pdf)
        if not path.exists():
            log(f"Input PDF not found: {path}")
            return 2
        title = title or path.stem
        source_line = f"Source: {path.name}"
        pages = read_pdf(path)
        if args.pages:
            a, b = args.pages.split("-")
            pages = [p for p in pages if int(a) <= p[0] <= int(b)]
        for pno, text in pages:
            raw_lines.append(f"[page {pno}]")
            raw_lines.extend(clean_ocr_text(text).splitlines())
        log(f"  read {len(pages)} pages")
    elif args.text:
        path = Path(args.text)
        title = title or path.stem
        source_line = f"Source: {path.name}"
        raw_lines = read_txt(path)
    elif args.docx:
        path = Path(args.docx)
        title = title or path.stem
        source_line = f"Source: {path.name}"
        raw_lines = read_docx(path)
    elif INPUT_URL:
        args.youtube = INPUT_URL
        return main()
    elif INPUT_PATH:
        p = Path(INPUT_PATH)
        ext = p.suffix.lower()
        if ext == ".pdf":
            args.pdf = INPUT_PATH
        elif ext == ".docx":
            args.docx = INPUT_PATH
        else:
            args.text = INPUT_PATH
        return main()
    else:
        ap.print_help()
        log("\nGive an input: --youtube URL, --pdf FILE, --text FILE or --docx FILE.")
        return 2

    # ---- split into slokas
    if segments is not None:
        # group transcript segments into verse blocks (a block ends when the
        # refrain line appears, or after a long pause)
        blocks_text = []
        cur = []
        for s in segments:
            cur.append(s["text"])
            if "करिष्यति" in s["text"] or "karishyati" in s["text"].lower():
                blocks_text.append("\n".join(cur))
                cur = []
        if cur:
            blocks_text.append("\n".join(cur))
        slokas = [b for b in blocks_text if len(b.strip()) >= 2]
        seg_times = []
        # approximate time per block from the segments
        idx = 0
        for b in slokas:
            n = b.count("\n") + 1
            seg_times.append((segments[idx]["start"],
                              segments[min(idx + n - 1, len(segments) - 1)]["end"]))
            idx += n
    else:
        slokas = split_slokas(raw_lines)
        seg_times = [None] * len(slokas)
    log(f"  slokas found: {len(slokas)}")
    if not slokas:
        log("No slokas found. Check the input file / video.")
        return 2

    # ---- transliterate to Telugu lipi (offline, exact)
    # (for YouTube this is recomputed from the corrected verse later)
    telugu_lipi = [to_telugu_lipi(b) for b in slokas]

    # ---- translate with OpenAI (or dry-run)
    ckpt = work_dir / "translation.json"
    translations = None
    if args.resume and ckpt.exists():
        saved = json.loads(ckpt.read_text(encoding="utf-8"))
        if len(saved.get("items", [])) == len(slokas):
            translations = saved["items"]
            log("Resume: using saved translations.")
    if translations is None:
        if args.dry_run:
            log("Dry run: skipping OpenAI, translation left blank.")
            translations = [{"corrected": "", "translation": "", "summary": ""}
                            for _ in slokas]
        else:
            client = _openai_client()
            translations = []
            for i in range(0, len(slokas), BATCH_SIZE):
                batch = slokas[i:i + BATCH_SIZE]
                log(f"  translating slokas {i+1}-{i+len(batch)} of {len(slokas)}...")
                translations.extend(translate_batch(
                    client, batch, source_lang,
                    needs_cleanup=(segments is not None)))
                ckpt.write_text(json.dumps({"items": translations},
                                           ensure_ascii=False, indent=1),
                                encoding="utf-8")

    # ---- overall summary
    overall = ""
    if not args.dry_run:
        try:
            client = _openai_client()
            overall = overall_summary(
                client, [t["summary"] for t in translations], title)
        except Exception as exc:
            log(f"  overall summary failed ({exc}); continuing without it.")

    # ---- build the PDF book
    blocks = []
    for i, (orig, lipi, tr) in enumerate(zip(slokas, telugu_lipi, translations)):
        if seg_times[i]:
            head = f"శ్లోకం {i+1}  ·  {fmt_time(seg_times[i][0])} – {fmt_time(seg_times[i][1])}"
        else:
            head = f"శ్లోకం {i+1}"
        if tr.get("corrected"):
            # Whisper transcript was cleaned by OpenAI: use the clean verse
            orig = tr["corrected"]
            lipi = to_telugu_lipi(orig)
        blocks.append({"heading": head, "original": orig,
                       "telugu_lipi": lipi,
                       "translation": tr["translation"],
                       "summary": tr["summary"]})
    out_path = Path(args.output) if args.output else Path(
        (args.pdf or args.text or args.docx or "youtube") and
        (Path(args.pdf or args.text or args.docx or "book").stem
         + "_telugu_book.pdf"))
    if args.youtube:
        out_path = Path(args.output) if args.output else Path(
            "youtube_telugu_book.pdf")
    build_pdf(out_path, title, title_en, source_line, blocks, overall)
    log(f"Done. Book: {out_path}  ({len(blocks)} slokas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
