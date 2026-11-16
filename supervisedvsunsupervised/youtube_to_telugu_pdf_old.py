#!/usr/bin/env python3
"""YouTube video -> Telugu text -> PDF.

Give it a YouTube video link. It downloads the audio, transcribes it
to Telugu text with Whisper AI (runs on your own machine, no API key,
no money), and builds a clean PDF with the Telugu text and a timestamp
for every verse / line.

Pipeline (the same one that worked on the @urbhaktii Mrityunjaya video):
  1. yt-dlp downloads the audio (android client, so YouTube's bot check
     does not block it), ffmpeg converts it to 16 kHz mono WAV.
  2. faster-whisper ("small" model) transcribes it in Telugu.
     VAD is OFF on purpose: with VAD on, Whisper only heard the first
     minute and skipped the rest. With VAD off it hears the whole video.
  3. Gap re-check: Whisper sometimes skips chanted parts and leaves a
     long silent stretch. Any stretch longer than 25 seconds with no
     words is re-transcribed in 15-second chunks, using the video title
     as a hint. This is what recovered the verses the first pass missed.
  4. Every segment is converted to Telugu script. Whisper mixes three
     scripts for a Sanskrit recitation -- Telugu, Devanagari, and Latin
     transliteration ("nama mishrasa devam...") -- so each segment is
     transliterated to Telugu, and the spelling is normalised to
     standard spelling (e.g. literal "మృత్యుఞ్జయ" -> "మృత్యుంజయ").
     True garbage (mixed scripts, foreign words) is dropped.
  5. A PDF is built: video title, link, channel, duration, then every
     verse with its timestamp. The raw transcript is also saved as
     .json and .txt next to the PDF, so you can check it.

  Honest limit: this script only changes the SCRIPT, not the letters.
  If Whisper misheard a letter, the wrong letter stays wrong. For a
  spoken-Telugu video the text is usually clean; for a Sanskrit
  recitation some letters will be wrong. The accurate version is
  make_telugu_book.py, which uses OpenAI to correct the transcript
  into the proper verses before building the PDF.

  Speed: the "small" model takes about as long as the video itself
  (roughly real-time) on a decent machine, and much longer on a slow
  one -- a 2-hour video took over 11 hours on one slow Windows box
  and was still in the first pass. For a long video, use a faster
  model: --model tiny (fastest, ~10x quicker, less accurate) or
  --model base (middle ground). The run prints live progress every
  5 minutes of audio, and it saves a checkpoint
  (youtube_telugu_work/transcript_partial.json) as it goes, so
  stopping it with Ctrl+C does not lose the work -- re-running the
  same command resumes from the checkpoint instead of starting over.
  Delete transcript_partial.json to force a fresh run.

Install (once):
  pip install yt-dlp faster-whisper reportlab indic-transliteration imageio-ffmpeg
  (imageio-ffmpeg gives the script its own ffmpeg, so you do not have
  to install ffmpeg or edit PATH. If you already have ffmpeg on PATH,
  that one is used instead.)
  NOTE: the script reads the WAV itself (stdlib `wave` + numpy) and
  passes the samples straight to Whisper, so it never uses the `av`
  library at all. That matters because faster-whisper 1.2.1 breaks
  with av 14+ ("metadata_errors" error) and av 13.1.0 has no wheel
  for Python 3.14, so it cannot be installed on a 3.14 machine
  without a C compiler. With this script the av version does not
  matter -- any version (or none) works.
  Noto Sans Telugu font must be installed (Windows: install the font,
  it is then found automatically).

Usage:
  python youtube_to_telugu_pdf.py --url "https://www.youtube.com/watch?v=Re6Jg2IR4-M"
  python youtube_to_telugu_pdf.py --url <link> --output my_book.pdf
  python youtube_to_telugu_pdf.py --check        (setup check only)

Or put the link in VIDEO_URL below and just run the script.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------- settings
# Put your values here, or use the --url / --output flags instead.
VIDEO_URL = ""
OUTPUT_PDF = ""            # empty = auto name from the video title
LANGUAGE = "te"            # transcription language: "te" = Telugu
MODEL_SIZE = "small"       # Whisper model: tiny / base / small / medium
KEEP_AUDIO = False         # keep the downloaded audio (.wav) after the run
WORK_DIR = "youtube_telugu_work"

# Whisper skips chanted parts sometimes. Any stretch with no words
# longer than this (seconds) is re-checked in short chunks.
GAP_SECONDS = 25
CHUNK_SECONDS = 15
# A pause longer than this (seconds) between two spoken parts starts a
# new verse block in the PDF.
VERSE_GAP = 3.0


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def fmt_time(seconds):
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _clean_proxy_env():
    """Drop IPv6 bracket entries from NO_PROXY (they crash the model
    downloader on some machines). Harmless if NO_PROXY is not set."""
    for key in ("NO_PROXY", "no_proxy"):
        val = os.environ.get(key, "")
        if "[" in val:
            kept = [p for p in val.split(",") if "[" not in p]
            os.environ[key] = ",".join(kept)


def find_ffmpeg():
    """Find ffmpeg. First the system one (on PATH), then the static
    binary bundled with the imageio-ffmpeg package (pip-installable,
    no manual setup). Raises a clear error if neither exists."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    raise RuntimeError(
        "ffmpeg not found. Easiest fix (one command):\n"
        "  pip install imageio-ffmpeg\n"
        "That gives the script its own ffmpeg and nothing else needs "
        "installing. Or install ffmpeg from https://www.gyan.dev/"
        "ffmpeg/builds/ and add it to PATH.")


def _ffmpeg():
    """Resolve ffmpeg once per run (cached on the function)."""
    if not hasattr(_ffmpeg, "exe"):
        _ffmpeg.exe = find_ffmpeg()
    return _ffmpeg.exe


# ---------------------------------------------------------------- fonts

def find_telugu_font():
    """Find Noto Sans Telugu (regular + bold) on Windows or Linux."""
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


# ---------------------------------------------------------------- Telugu spelling

def normalize_telugu(s):
    """Literal transliteration spelling -> standard Telugu spelling.
    E.g. మృత్యుఞ్జయ -> మృత్యుంజయ, చన్ద్ర -> చంద్ర, స్తోత్రమ్ -> స్తోత్రం."""
    pairs = [("ఙ్క", "ంక"), ("ఞ్జ", "ంజ"), ("ఞ్చ", "ంచ"),
             ("ణ్ట", "ంట"), ("ణ్డ", "ండ"), ("న్త", "ంత"),
             ("న్ద", "ంద"), ("న్ధ", "ంధ"), ("మ్ప", "ంప"),
             ("మ్భ", "ంభ"), ("మ్బ", "ంబ"), ("ఞ్", "ం"),
             ("ఙ్", "ం"), ("ణ్", "ం"), ("న్", "ం"), ("మ్", "ం"),
             ("ఞ", "ం"), ("ఙ", "ం"), ("ణ", "ం")]
    for a, b in pairs:
        s = s.replace(a, b)
    # word-final "మ్" -> "ం" is already covered above, but keep the
    # common "స్తోత్రమ్" style ending tidy.
    s = re.sub(r"మ్(\s|$)", r"ం\1", s)
    return s


_ENGLISH_WORDS = {
    "the", "and", "you", "subscribe", "channel", "video", "like",
    "share", "comment", "please", "thanks", "thank", "hello", "welcome",
    "music", "song", "now", "today", "good", "morning", "please",
}


def to_telugu(text):
    """Convert one Whisper segment into Telugu script, whatever script
    Whisper wrote it in. Whisper mixes three scripts for a Sanskrit
    recitation: Telugu, Devanagari, and Latin transliteration
    ("nama mishrasa devam..."). Each is transliterated to Telugu.
    Returns "" when the segment is unrecoverable garbage (mixed scripts,
    foreign words, Chinese) -- those are dropped, not printed.
    Note: this only changes the SCRIPT, not the letters. If Whisper
    misheard a letter, the wrong letter stays wrong. The OpenAI cleanup
    step (in make_telugu_book.py) is what fixes misheard letters."""
    from indic_transliteration import sanscript
    from indic_transliteration.sanscript import transliterate
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return ""
    n_dev = sum(1 for c in letters if "\u0900" <= c <= "\u097f")
    n_tel = sum(1 for c in letters if "\u0c00" <= c <= "\u0c7f")
    n_lat = sum(1 for c in letters if c.isascii() and c.isalpha())
    if n_tel >= len(letters) * 0.5:
        out = text  # already Telugu
    elif n_dev >= len(letters) * 0.5:
        out = transliterate(text, sanscript.DEVANAGARI, sanscript.TELUGU)
    elif n_lat >= len(letters) * 0.5:
        words = set(text.lower().split())
        if words & _ENGLISH_WORDS:
            return ""  # English hallucination, not a verse
        out = transliterate(text.lower(), sanscript.ITRANS, sanscript.TELUGU)
    else:
        return ""  # mixed scripts = garbage
    # after conversion it must be mostly Telugu letters
    out_letters = [c for c in out if c.isalpha()]
    if not out_letters:
        return ""
    n_tel2 = sum(1 for c in out_letters if "\u0c00" <= c <= "\u0c7f")
    if n_tel2 < len(out_letters) * 0.7:
        return ""
    return normalize_telugu(out)


def _is_garbage(text):
    """True when a Whisper segment is mostly stray characters (Chinese,
    Latin, symbols) instead of Telugu/Devanagari. Such segments are
    hallucinations and are dropped, not printed in the PDF."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return True
    telugu = sum(1 for c in letters
                 if "\u0c00" <= c <= "\u0c7f" or "\u0900" <= c <= "\u097f")
    return telugu < len(letters) * 0.5


# ---------------------------------------------------------------- download + audio

def _run(cmd, timeout):
    """Run a command and return the CompletedProcess. Shows the last
    lines of output when it fails, so the real error is visible."""
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r


def get_video_info(url):
    """Fetch the video's title/duration/uploader/id WITHOUT downloading.
    Uses --print with --skip-download (the safe pair). Do NOT use
    --dump-single-json here: with the SABR-only experiment it trips
    over format selection ("Requested format is not available")."""
    _clean_proxy_env()
    cmd = ["yt-dlp", "--no-check-certificates",
           "--extractor-args", "youtube:player_client=android",
           "--no-playlist", "--skip-download",
           "--print", "%(title)s",
           "--print", "%(duration)s",
           "--print", "%(uploader)s",
           "--print", "%(id)s",
           url]
    r = _run(cmd, 300)
    if r.returncode != 0:
        raise RuntimeError("yt-dlp could not read the video:\n"
                           + (r.stderr or r.stdout)[-800:])
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    # drop any warning lines so the four fields line up
    lines = [l for l in lines if not l.startswith("WARNING:")]
    if len(lines) < 4:
        raise RuntimeError("yt-dlp returned unreadable video info:\n"
                           + r.stdout[-400:])
    return {"title": lines[0],
            "duration": lines[1],
            "uploader": lines[2],
            "id": lines[3]}


def download_audio(url, out_dir):
    """Download the video's audio with yt-dlp and convert it to a
    16 kHz mono WAV. Returns (wav_path, info dict with title/duration/
    uploader/id).
    NOTE: the download call must NOT use --print. With --print, yt-dlp
    prints the metadata and exits WITHOUT downloading (it returned 0
    and saved nothing), which is the bug this version fixes."""
    _clean_proxy_env()
    out_dir.mkdir(parents=True, exist_ok=True)
    info = get_video_info(url)
    log(f"  video: {info.get('title') or info.get('id')}")
    # resume: if the audio was already downloaded and converted, reuse
    # it instead of downloading the whole video again (matters on a
    # long video when a later step fails and you re-run).
    wav = out_dir / "audio.wav"
    if wav.exists() and wav.stat().st_size > 100000:
        log(f"  reusing existing audio: {wav} "
            f"({fmt_time(float(info['duration'] or 0))}) -- delete it "
            f"to force a fresh download")
        return wav, info
    cmd = ["yt-dlp", "--no-check-certificates",
           "--extractor-args", "youtube:player_client=android",
           "--no-playlist",
           "-f", "18/bestaudio",
           "-o", str(out_dir / "audio.%(ext)s"),
           url]
    log("Downloading audio with yt-dlp ...")
    r = _run(cmd, 1800)
    if r.returncode != 0:
        raise RuntimeError("yt-dlp failed:\n" + (r.stderr or r.stdout)[-800:])
    media = list(out_dir.glob("audio.*"))
    if not media:
        tail = (r.stderr or r.stdout or "")[-800:]
        raise RuntimeError(
            "yt-dlp finished but no audio file was saved.\n"
            f"  work folder: {out_dir}\n"
            f"  files there now: {[p.name for p in out_dir.iterdir()]}\n"
            f"  yt-dlp output (last lines):\n{tail}")
    media = media[0]
    subprocess.run([_ffmpeg(), "-y", "-v", "error", "-i", str(media),
                    "-vn", "-ac", "1", "-ar", "16000", str(wav)],
                   check=True, timeout=600)
    if media != wav:
        media.unlink(missing_ok=True)
    log(f"  audio ready: {wav} ({fmt_time(float(info['duration'] or 0))})")
    return wav, info


# ---------------------------------------------------------------- transcription

def _load_wav_as_array(wav_path):
    """Read a WAV file into a float32 numpy array (samples in [-1, 1]).

    Why this exists: faster-whisper's own audio decoder (faster_whisper/
    audio.py) calls av.open(..., metadata_errors="ignore"), which av 14+
    removed, so faster-whisper 1.2.1 crashes with av 19 ("open() got an
    unexpected keyword argument 'metadata_errors'"). av 13.1.0 would fix
    it, but it has no prebuilt wheel for Python 3.14 -- pip tries to
    compile it from source and fails without a C compiler ("Microsoft
    Visual C++ 14.0 or greater is required"). So instead of fighting the
    av version, this script never uses av: it reads the WAV itself with
    Python's built-in `wave` module and hands Whisper the samples.
    model.transcribe() accepts a numpy array directly and then never
    calls its av decoder. Our WAVs are always 16 kHz mono (ffmpeg
    converts them that way in download_audio), which is exactly the
    sample rate Whisper expects, so no resampling is needed here.
    """
    import wave
    import numpy as np
    with wave.open(str(wav_path), "rb") as w:
        n_channels = w.getnchannels()
        sampwidth = w.getsampwidth()
        n_frames = w.getnframes()
        raw = w.readframes(n_frames)
    if sampwidth == 2:
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sampwidth == 4:
        audio = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    elif sampwidth == 1:
        audio = ((np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0)
                 / 128.0)
    else:
        raise ValueError(f"unsupported WAV sample width: {sampwidth} bytes")
    if n_channels > 1:
        audio = audio.reshape(-1, n_channels).mean(axis=1)
    return audio.astype(np.float32)


def _wav_duration(wav_path):
    """Duration of a WAV file in seconds, from its header only."""
    import wave
    with wave.open(str(wav_path), "rb") as w:
        return w.getnframes() / w.getframerate()


def _save_partial(work, video_id, segs):
    """Save the segments transcribed so far, so an interrupted run can
    resume instead of starting over. A Ctrl+C used to lose everything,
    because the transcript was only written at the very end."""
    if not work:
        return
    try:
        (Path(work) / "transcript_partial.json").write_text(
            json.dumps({"video_id": video_id, "segments": segs},
                       ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass  # a checkpoint is a nicety, never let it break the run


def transcribe_audio(wav_path, model_name="small", lang="te", prompt="",
                     work=None, video_id="", resume_segs=None):
    """Transcribe the WAV with Whisper. Returns (segments, total_seconds).
    Each segment is {"start", "end", "text"}.
    The WAV is loaded with _load_wav_as_array() and the samples are
    passed to model.transcribe() -- never the file path, so faster-
    whisper's av decoder is never used (see _load_wav_as_array).
    Full pass first (VAD off, NO prompt, so nothing is skipped), then
    every long silent stretch is re-checked in 15-second chunks with
    the video title as the hint (the prompt helps on short chunks).
    The first pass prints live progress (every 5 minutes of audio) and
    saves a checkpoint as it goes, so you can see it is alive and a
    Ctrl+C does not lose hours of work. If resume_segs (from a
    previous interrupted run of the same video) is given, only the
    audio after the last saved segment is transcribed."""
    from faster_whisper import WhisperModel
    _clean_proxy_env()
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    log(f"Transcribing with Whisper '{model_name}' (language={lang}) ...")
    total = _wav_duration(wav_path)
    segs = list(resume_segs) if resume_segs else []
    resume_from = max((s["end"] for s in segs), default=0.0)
    if resume_from > 0:
        log(f"  resuming from {fmt_time(resume_from)} "
            f"({len(segs)} segments already done)")
    # Full pass: NO initial prompt. A prompt on the whole file makes
    # Whisper skip most of the audio (12 segments instead of 46 on the
    # Mrityunjaya test). The prompt is only used on the short chunks
    # below, where it helps.
    # The samples go in as a numpy array, never the file path -- that is
    # what keeps faster-whisper's av decoder (and its version clash)
    # out of the picture entirely.
    if resume_from > 0:
        # only the part after the resume point still needs transcribing
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".wav",
                                        delete=False) as tmp:
            tail_path = tmp.name
        subprocess.run([_ffmpeg(), "-y", "-v", "error",
                        "-ss", str(resume_from), "-i", str(wav_path),
                        tail_path], check=True, timeout=600)
        try:
            audio = _load_wav_as_array(tail_path)
        finally:
            os.unlink(tail_path)
        offset = resume_from
    else:
        audio = _load_wav_as_array(wav_path)
        offset = 0.0
    segments, info = model.transcribe(
        audio, language=lang, beam_size=5, vad_filter=False)
    # consume the generator segment by segment, so the run shows live
    # progress and saves a checkpoint as it goes.
    next_log = (int(resume_from // 300) + 1) * 300.0
    for s in segments:
        if not s.text.strip():
            continue
        segs.append({"start": round(s.start + offset, 1),
                     "end": round(s.end + offset, 1),
                     "text": s.text.strip()})
        if s.end + offset >= next_log:
            log(f"  ... {fmt_time(s.end + offset)} of {fmt_time(total)} "
                f"transcribed ({len(segs)} segments)")
            _save_partial(work, video_id, segs)
            next_log += 300.0
    log(f"  first pass: {len(segs)} segments, "
        f"{fmt_time(total)} of audio")
    _save_partial(work, video_id, segs)
    # find the long stretches with no words and re-check them
    covered = sorted((s["start"], s["end"]) for s in segs)
    gaps = []
    prev_end = 0.0
    for a, b in covered:
        if a - prev_end > GAP_SECONDS:
            gaps.append((prev_end, a))
        prev_end = max(prev_end, b)
    if total - prev_end > GAP_SECONDS:
        gaps.append((prev_end, total))
    # the hint for the short chunks: the video title PLUS the verse that
    # repeats most often (the refrain). The refrain is a far stronger
    # hint than the title alone -- it is what recovered the missed
    # verses in the Mrityunjaya test.
    chunk_prompt = prompt or ""
    if segs:
        from collections import Counter
        most_common = Counter(s["text"] for s in segs).most_common(1)
        if most_common and len(most_common[0][0]) > 10:
            chunk_prompt = (most_common[0][0] + " " + chunk_prompt).strip()
    for g0, g1 in gaps:
        log(f"  re-checking silent stretch {fmt_time(g0)}-{fmt_time(g1)} "
            f"in {CHUNK_SECONDS}s chunks ...")
        t = g0
        while t + CHUNK_SECONDS <= g1:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".wav",
                                            delete=False) as tmp:
                tmp_path = tmp.name
            subprocess.run([_ffmpeg(), "-y", "-v", "error",
                            "-ss", str(t), "-t", str(CHUNK_SECONDS),
                            "-i", str(wav_path), tmp_path],
                           check=True, timeout=120)
            try:
                c_segs, _ = model.transcribe(
                    _load_wav_as_array(tmp_path), language=lang, beam_size=5,
                    vad_filter=False, initial_prompt=chunk_prompt or None)
                for s in c_segs:
                    if s.text.strip():
                        segs.append({"start": round(s.start + t, 1),
                                     "end": round(s.end + t, 1),
                                     "text": s.text.strip()})
            finally:
                os.unlink(tmp_path)
            t += CHUNK_SECONDS
        _save_partial(work, video_id, segs)
    segs.sort(key=lambda s: s["start"])
    # convert every segment to Telugu script (Devanagari and Latin
    # transliteration are transliterated; true garbage is dropped)
    kept = []
    for s in segs:
        text = to_telugu(s["text"])
        if not text:
            continue
        s["text"] = text
        kept.append(s)
    # coverage check: how much of the audio actually has words. If the
    # tail is far short of the total, the run missed the ending -- the
    # gap re-check above should have caught it, so a shortfall here
    # means something went wrong and the run is worth re-doing.
    covered_s = 0.0
    cur_end = 0.0
    for a, b in sorted((s["start"], s["end"]) for s in kept):
        if a > cur_end:
            covered_s += max(0.0, b - a)
            cur_end = b
        else:
            covered_s += max(0.0, b - cur_end)
            cur_end = max(cur_end, b)
    log(f"  after gap re-check + cleanup: {len(kept)} segments, "
        f"covering {fmt_time(covered_s)} of {fmt_time(total)}")
    return kept, total


def group_into_blocks(segs):
    """Group segments into verse blocks: a new block starts when the
    pause since the previous segment is longer than VERSE_GAP seconds
    (a pause between verses) or when the text ends with a double danda.
    Returns [{"start", "end", "text"}]."""
    blocks = []
    cur = None
    for s in segs:
        if (cur is None
                or s["start"] - cur["end"] > VERSE_GAP
                or cur["text"].rstrip().endswith("॥")):
            if cur is not None:
                blocks.append(cur)
            cur = {"start": s["start"], "end": s["end"], "text": s["text"]}
        else:
            cur["text"] += " " + s["text"]
            cur["end"] = s["end"]
    if cur is not None:
        blocks.append(cur)
    # tidy up: collapse extra spaces, keep the danda separators
    for b in blocks:
        b["text"] = re.sub(r"\s+", " ", b["text"]).strip()
    return blocks


# ---------------------------------------------------------------- PDF

def build_pdf(out_path, title, info, blocks):
    """Build the A4 Telugu PDF: title, video link, channel, duration,
    then one block per verse with its timestamp."""
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
    DARK = HexColor("#1a1a6e")
    GREY = HexColor("#555555")
    BLUE = HexColor("#0b5394")

    def esc(t):
        return (t.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("\n", "<br/>"))

    title_style = ParagraphStyle("title", fontName="Telugu-Bold", fontSize=19,
                                 leading=27, alignment=TA_CENTER,
                                 textColor=DARK, spaceAfter=4)
    sub_style = ParagraphStyle("sub", fontName="Helvetica", fontSize=11,
                               leading=16, alignment=TA_CENTER,
                               textColor=GREY, spaceAfter=2)
    link_style = ParagraphStyle("link", fontName="Helvetica", fontSize=10,
                                leading=15, alignment=TA_CENTER,
                                textColor=BLUE, spaceAfter=6)
    head_style = ParagraphStyle("head", fontName="Telugu-Bold", fontSize=12,
                                leading=18, textColor=DARK, spaceBefore=12,
                                spaceAfter=3)
    body_style = ParagraphStyle("body", fontName="Telugu", fontSize=13,
                                leading=21, spaceAfter=2)
    note_style = ParagraphStyle("note", fontName="Telugu", fontSize=10.5,
                                leading=16, textColor=GREY, spaceBefore=12)

    doc = SimpleDocTemplate(str(out_path), pagesize=A4,
                            leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm,
                            title=title)
    story = [Paragraph(esc(title), title_style)]
    if info.get("uploader"):
        story.append(Paragraph(esc(info["uploader"]), sub_style))
    if info.get("duration"):
        story.append(Paragraph(
            esc(f"Duration: {fmt_time(float(info['duration']))}"),
            sub_style))
    if info.get("id"):
        story.append(Paragraph(
            esc(f"https://www.youtube.com/watch?v={info['id']}"),
            link_style))
    story.append(HRFlowable(width="100%", thickness=1, color=DARK,
                            spaceAfter=6))
    for b in blocks:
        story.append(Paragraph(
            esc(f"{fmt_time(b['start'])} – {fmt_time(b['end'])}"),
            head_style))
        story.append(Paragraph(esc(b["text"]), body_style))
    story.append(Paragraph(
        "గమనిక: ఈ పాఠ్యం Whisper AI ద్వారా ఆడియో నుంచి తయారైంది. "
        "అర్థంలో సందేహం ఉంటే వీడియోను చూడండి.",
        note_style))
    doc.build(story)
    log(f"PDF written: {out_path}")


# ---------------------------------------------------------------- check

def run_check():
    problems = []
    for pkg in ("faster_whisper", "reportlab", "PIL",
                "indic_transliteration"):
        try:
            __import__(pkg)
        except ImportError:
            problems.append(f"Python package missing: {pkg}")

    def _which(cmd, *a):
        try:
            return subprocess.run([cmd, *a], capture_output=True).returncode == 0
        except FileNotFoundError:
            return False

    if not _which("yt-dlp", "--version"):
        problems.append("yt-dlp not found (pip install yt-dlp)")
    # NOTE: av is deliberately NOT checked here. This script reads the
    # WAV itself and passes samples to Whisper, so it never uses av --
    # any av version (including 19.x, which faster-whisper 1.2.1
    # itself rejects) works fine. An earlier --check wrongly flagged
    # av 19 and told Babu to install av==13.1.0, which has no wheel
    # for Python 3.14 and cannot build without a C compiler.
    try:
        _ffmpeg()
    except RuntimeError as exc:
        problems.append(str(exc))
    try:
        find_telugu_font()
    except RuntimeError as exc:
        problems.append(str(exc))
    if problems:
        print("Setup check FAILED:")
        for p in problems:
            print("  -", p)
        return 1
    print("Setup check OK: yt-dlp, ffmpeg, faster-whisper, Telugu font "
          "all ready.")
    return 0


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--url", help="YouTube video URL")
    ap.add_argument("--output", help="output PDF path")
    ap.add_argument("--model", default=MODEL_SIZE,
                    help="Whisper model (tiny/base/small/medium)")
    ap.add_argument("--language", default=LANGUAGE,
                    help="transcription language (default te)")
    ap.add_argument("--keep-audio", action="store_true",
                    help="keep the downloaded audio.wav")
    ap.add_argument("--check", action="store_true", help="setup check only")
    args = ap.parse_args()

    if args.check:
        return run_check()

    url = args.url or VIDEO_URL
    if not url:
        print("Give a YouTube link: --url <link>, or set VIDEO_URL "
              "at the top of the script.")
        return 2

    work = Path(WORK_DIR)
    work.mkdir(parents=True, exist_ok=True)

    wav, info = download_audio(url, work)
    title = info.get("title") or "YouTube video"
    # resume: if a previous run of THIS video was interrupted, its
    # partial transcript is reused instead of starting over. (The
    # partial file is only trusted when its video id matches this
    # video, so running a different video never resumes stale work.
    # Delete transcript_partial.json to force a fresh run.)
    partial = work / "transcript_partial.json"
    resume_segs = []
    if partial.exists():
        try:
            saved = json.loads(partial.read_text(encoding="utf-8"))
            if (saved.get("video_id") == info.get("id")
                    and saved.get("segments")):
                resume_segs = saved["segments"]
        except (OSError, ValueError, KeyError):
            resume_segs = []
    segs, total = transcribe_audio(wav, args.model, args.language,
                                   prompt=title, work=work,
                                   video_id=info.get("id") or "",
                                   resume_segs=resume_segs)
    blocks = group_into_blocks(segs)
    log(f"  grouped into {len(blocks)} verse blocks")

    # save the raw transcript next to the PDF, so it can be checked
    (work / "transcript.json").write_text(
        json.dumps({"video": info, "segments": segs, "blocks": blocks},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    (work / "transcript.txt").write_text(
        "\n\n".join(f"[{fmt_time(b['start'])} – {fmt_time(b['end'])}]\n"
                    f"{b['text']}" for b in blocks),
        encoding="utf-8")
    log(f"  transcript saved: {work / 'transcript.json'}")
    # the run finished, so the partial checkpoint is no longer needed
    (work / "transcript_partial.json").unlink(missing_ok=True)

    out = args.output or OUTPUT_PDF
    if not out:
        safe = re.sub(r'[\\/:*?"<>|]+', "_", title).strip() or "youtube_telugu"
        out = f"{safe}.pdf"
    build_pdf(out, title, info, blocks)

    if not (args.keep_audio or KEEP_AUDIO):
        wav.unlink(missing_ok=True)
    log(f"Done. Book: {out}  ({len(blocks)} verses, "
        f"{fmt_time(total)} of audio)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
