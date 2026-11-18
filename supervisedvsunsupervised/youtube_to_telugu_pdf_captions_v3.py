#!/usr/bin/env python3
"""YouTube video -> Telugu text -> PDF, via YouTube's FREE auto-captions.

Why this file exists: the local (faster-whisper) script
youtube_to_telugu_pdf.py cannot transcribe Babu's actual videos.
Tested 2026-11-16 on the Advaita Darshanam discourse
(https://www.youtube.com/watch?v=ipZtUnGIvpY, 1h43m, spoken Telugu):
local Whisper small found NOTHING coherent in three 60-second samples
(language detected as Telugu, prob 0.96, but 0 segments), and forced
runs hallucinated Malayalam script / fancy-English / Chinese. The
same failure happened on the Sri Vallabha Ganapathi Kalyanam video
(only 1.35% of the audio produced anything, all garbage). A bigger
local model will NOT fix this -- these hall/phone recordings defeat
local faster-whisper.

The FREE accurate route is YouTube's own auto-captions: Google's
speech recognition runs on YouTube's servers and is far more robust
than local Whisper on noisy recordings. NOTE 2026-11-16: whether a
Telugu ("te") track exists for this video is now OPEN, not settled.
An early yt-dlp listing said "te" was listed, but a player check the
same day showed only "Hindi (auto-generated)" -- no Telugu track,
Telugu only as an auto-translate option -- and a signed-out check of
YouTube's timedtext track-list endpoint at 13:37 CST returned an
empty list (no tracks at all, not even the Hindi one the player
showed, so that endpoint alone proves nothing). This script
downloads whatever caption track exists for the requested language
(free, no API key, no money) and turns it into a clean Telugu PDF.
If "te" fails, try --lang hi --method timedtext: if that Hindi
track is actually Telugu speech mislabelled by YouTube, its first
lines will read as Telugu.

Pipeline:
  1. yt-dlp downloads the Telugu auto-captions (VTT) -- free.
  2. The VTT is parsed and cleaned: YouTube auto-captions repeat the
     same line across several cues as the text updates, so duplicate
     consecutive lines are dropped; HTML tags are stripped.
  3. Cues are grouped into blocks by pause > 3 s, and a PDF is built
     the same way as the local script (A4, Noto Sans Telugu, per-block
     timestamps). A coverage line is printed: captioned seconds vs
     video duration, so you can see at a glance whether the captions
     cover the video.

  NOTE on YouTube rate limits: downloading captions hit an HTTP 429
  ("Too Many Requests") on 2026-11-16 after many rapid yt-dlp
  calls, and it was STILL 429 about 2 hours later -- so the block is
  persistent (likely ~a day), not a few minutes. v3 adds a fallback
  for exactly that case: if yt-dlp is rate-limited, the script now
  fetches the SAME auto-captions straight from YouTube's timedtext
  endpoint with only the Python standard library (no yt-dlp, a
  different request pattern). Use --method timedtext to go straight
  to the fallback, or leave the default (auto) to try yt-dlp first
  and fall back automatically. The fallback is NOT yet verified
  against a live video (2026-11-16) -- run --check, then try it on
  the Advaita video and read the coverage line it prints.

Install (once):
  pip install yt-dlp reportlab
  (Noto Sans Telugu font must be installed, as before.)
  No API key, no account, no money.

Usage:
  python youtube_to_telugu_pdf_captions_v3.py --check
  python youtube_to_telugu_pdf_captions_v3.py --url "https://www.youtube.com/watch?v=ipZtUnGIvpY" --method timedtext
  python youtube_to_telugu_pdf_captions_v3.py --url "<link>" --output "MyBook.pdf"
  python youtube_to_telugu_pdf_captions_v3.py --url "<link>" --lang te
  python youtube_to_telugu_pdf_captions_v3.py --url "https://www.youtube.com/watch?v=ipZtUnGIvpY" --lang hi --method timedtext
"""

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

VIDEO_URL = ""
OUTPUT_PDF = ""
LANGUAGE = "te"
WORK_DIR = "captions_telugu_work"
BLOCK_GAP = 3.0


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def fmt_time(seconds):
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _clean_proxy_env():
    for key in ("NO_PROXY", "no_proxy"):
        val = os.environ.get(key, "")
        if "[" in val:
            os.environ[key] = ",".join(p for p in val.split(",") if "[" not in p)


def _run(cmd, timeout):
    # yt-dlp always writes UTF-8. Decode as UTF-8 explicitly: on Windows
    # the default (cp1252) mangles non-ASCII titles and can even drop
    # the title line, which broke get_video_info for Telugu titles.
    return subprocess.run(cmd, capture_output=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


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


def get_video_info(url):
    """Fetch the video's title/duration/uploader/id WITHOUT downloading.
    Uses yt-dlp -j (JSON): the title is JSON-escaped (pure ASCII), so a
    Telugu title survives ANY console encoding -- the old --print version
    lost the title on Windows (cp1252), which crashed the script.
    Falls back to --print if -j ever fails."""
    _clean_proxy_env()
    cmd = ["yt-dlp", "--no-check-certificates",
           "--extractor-args", "youtube:player_client=android",
           "--no-playlist", "--skip-download", "-j", url]
    r = _run(cmd, 300)
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                return {"title": d.get("title") or d.get("id") or "",
                        "duration": str(d.get("duration") or ""),
                        "uploader": d.get("uploader") or "",
                        "id": d.get("id") or ""}
    # Fallback: --print (kept in case -j fails on some video).
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
    lines = [l for l in r.stdout.splitlines() if l.strip()
             and not l.startswith("WARNING:")]
    if len(lines) < 4:
        # Title may be empty on Windows (Telugu title lost in --print);
        # use the video id as the title rather than crashing.
        if len(lines) == 3:
            return {"title": lines[2], "duration": lines[0],
                    "uploader": lines[1], "id": lines[2]}
        raise RuntimeError("yt-dlp returned unreadable video info:\n"
                           + r.stdout[-400:])
    return {"title": lines[0], "duration": lines[1],
            "uploader": lines[2], "id": lines[3]}


def _vtt_ts_to_seconds(ts):
    # "00:01:23.456" or "01:23.456" -> seconds (float)
    parts = ts.strip().split(":")
    try:
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        m, s = parts
        return int(m) * 60 + float(s)
    except ValueError:
        return 0.0


def parse_vtt(path):
    """Parse a WebVTT caption file into [{"start","end","text"}].
    YouTube auto-captions repeat the same line across several cues as
    the on-screen text updates, so exact-duplicate consecutive lines
    are dropped (keeping the earliest start). HTML tags are stripped."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    cues = []
    # Split on blank lines; each block is: [optional id] timestamp --> timestamp [settings] / text lines
    blocks = re.split(r"\n\s*\n", text)
    for block in blocks:
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        ts_idx = None
        for i, l in enumerate(lines):
            if "-->" in l:
                ts_idx = i
                break
        if ts_idx is None:
            continue
        m = re.match(r"([\d:.]+)\s*-->\s*([\d:.]+)", lines[ts_idx])
        if not m:
            continue
        start = _vtt_ts_to_seconds(m.group(1))
        end = _vtt_ts_to_seconds(m.group(2))
        body = " ".join(lines[ts_idx + 1:])
        body = re.sub(r"<[^>]+>", "", body)          # strip <c>, <b>, etc.
        body = re.sub(r"&amp;", "&", body)
        body = re.sub(r"&lt;", "<", body)
        body = re.sub(r"&gt;", ">", body)
        body = re.sub(r"&nbsp;", " ", body)
        body = re.sub(r"\s+", " ", body).strip()
        if body:
            cues.append({"start": round(start, 1), "end": round(end, 1),
                         "text": body})
    # Dedupe: drop a cue whose text is identical to the previous kept cue,
    # or whose text is a prefix/duplicate of the running line. YouTube
    # auto-captions re-emit the same words as they are refined.
    deduped = []
    for c in cues:
        if deduped and c["text"] == deduped[-1]["text"]:
            continue
        # If this cue's text starts with the previous cue's text and only
        # adds a few words, it is the same rolling line: keep the longer one.
        if (deduped and c["text"].startswith(deduped[-1]["text"])
                and c["start"] - deduped[-1]["start"] < 5.0):
            deduped[-1] = c
            continue
        deduped.append(c)
    return deduped


def download_captions(url, lang, out_dir):
    """Download YouTube auto-captions (VTT) for `lang`. Returns the VTT path."""
    _clean_proxy_env()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(out_dir / "caps.%(ext)s")
    cmd = ["yt-dlp", "--no-check-certificates",
           "--extractor-args", "youtube:player_client=android",
           "--no-playlist", "--skip-download",
           "--write-auto-subs", "--sub-langs", lang,
           "--sub-format", "vtt",
           "-o", out_tmpl, url]
    log(f"Downloading {lang} auto-captions with yt-dlp ...")
    r = _run(cmd, 600)
    vtts = sorted(out_dir.glob(f"caps.{lang}.vtt"))
    if not vtts:
        # yt-dlp names it caps.<lang>.vtt; fall back to any vtt present
        vtts = sorted(out_dir.glob("caps.*.vtt"))
    if not vtts:
        err = (r.stderr or r.stdout or "")
        if "429" in err or "Too Many Requests" in err:
            raise RuntimeError(
                "YouTube rate-limited the caption download (HTTP 429). "
                "This is temporary -- wait a few minutes and run the "
                "same command again. Do not run it repeatedly in a row.")
        if "no subtitles" in err.lower() or "subtitle" in err.lower():
            raise RuntimeError(
                f"No auto-captions found for language '{lang}' on this "
                f"video. Run: yt-dlp --list-subs \"{url}\" to see which "
                "languages exist, then re-run with --lang <code>.")
        raise RuntimeError("yt-dlp could not download captions:\n"
                           + err[-800:])
    vtt = vtts[0]
    if vtt.stat().st_size < 100:
        raise RuntimeError(f"Caption file {vtt} is nearly empty "
                           f"({vtt.stat().st_size} bytes) -- the video "
                           "may have no real captions in this language.")
    log(f"  captions saved: {vtt} ({vtt.stat().st_size} bytes)")
    return vtt


def _extract_player_response(page_html):
    """Pull ytInitialPlayerResponse (a JSON object) out of a watch page."""
    marker = "ytInitialPlayerResponse = "
    idx = page_html.find(marker)
    if idx < 0:
        return None
    start = page_html.find("{", idx + len(marker))
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(page_html)):
        ch = page_html[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(page_html[start:i + 1])
                    except ValueError:
                        return None
    return None


def _pick_caption_track(player, lang):
    """Pick the caption track for `lang`; prefer a manual track over
    an auto-generated (kind=asr) one when both exist."""
    try:
        tracks = player["captions"]["playerCaptionsTracklistRenderer"]["captionTracks"]
    except (KeyError, TypeError):
        return None
    cands = [t for t in tracks if t.get("languageCode") == lang]
    if not cands:
        return None
    manual = [t for t in cands if t.get("kind") != "asr"]
    return (manual or cands)[0]


def _json3_to_vtt(data):
    """Convert YouTube's json3 caption format to WebVTT text.
    Returns "" when there are no usable events."""
    try:
        doc = json.loads(data)
    except ValueError:
        return ""
    out = ["WEBVTT", ""]
    for ev in doc.get("events", []):
        segs = ev.get("segs") or []
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if not text:
            continue
        start = ev.get("tStartMs", 0) / 1000.0
        dur = ev.get("dDurationMs", 0) / 1000.0
        end = start + dur
        def ts(s):
            h, m = int(s // 3600), int((s % 3600) // 60)
            return f"{h:02d}:{m:02d}:{s % 60:06.3f}"
        out.append(f"{ts(start)} --> {ts(end)}")
        out.append(text)
        out.append("")
    return "\n".join(out) if len(out) > 2 else ""


def _video_id_from_url(url):
    m = re.search(r"[?&]v=([A-Za-z0-9_-]{11})", url)
    if m:
        return m.group(1)
    m = re.search(r"youtu\.be/([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else ""


def download_captions_timedtext(url, lang, out_dir):
    """Fallback when yt-dlp is rate-limited (HTTP 429): fetch the same
    auto-captions straight from YouTube's timedtext endpoint, using
    only the standard library. Returns (vtt_path, track_dict).
    This is a DIFFERENT request pattern from yt-dlp's subtitle
    download, so a block on yt-dlp does not necessarily block this."""
    _clean_proxy_env()
    out_dir.mkdir(parents=True, exist_ok=True)
    vid = _video_id_from_url(url)
    if not vid:
        raise RuntimeError("Timedtext fallback: could not find the "
                           "video id in the URL.")
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/126.0 Safari/537.36"),
        "Accept-Language": "en-US,en;q=0.9",
    }
    watch = f"https://www.youtube.com/watch?v={vid}&hl=en"
    try:
        req = urllib.request.Request(watch, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp:
            page = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise RuntimeError("Timedtext fallback: could not open the "
                           f"video page: {exc}")
    player = _extract_player_response(page)
    if not player:
        raise RuntimeError("Timedtext fallback: no caption list found "
                           "in the video page (YouTube may have changed "
                           "its page format).")
    track = _pick_caption_track(player, lang)
    if not track:
        raise RuntimeError("Timedtext fallback: no "
                           f"'{lang}' caption track on this video.")
    base = track.get("baseUrl")
    if not base:
        raise RuntimeError("Timedtext fallback: the caption track has "
                           "no download URL.")
    base = html.unescape(base)
    name = (track.get("name") or {}).get("simpleText", "")
    log(f"  timedtext track: {name or lang} "
        f"(kind={track.get('kind', 'manual')})")
    for fmt in ("vtt", "json3"):
        turl = base + ("&fmt=vtt" if fmt == "vtt" else "&fmt=json3")
        try:
            req = urllib.request.Request(turl, headers=headers)
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read().decode("utf-8", errors="replace")
        except Exception as exc:
            log(f"  timedtext {fmt} fetch failed: {exc}")
            continue
        vtt_text = None
        if fmt == "vtt" and data.lstrip().startswith("WEBVTT"):
            vtt_text = data
        elif fmt == "json3":
            vtt_text = _json3_to_vtt(data)
        if vtt_text:
            p = out_dir / f"caps.{lang}.vtt"
            p.write_text(vtt_text, encoding="utf-8")
            log(f"  captions saved via timedtext ({fmt}): {p} "
                f"({p.stat().st_size} bytes)")
            return p, track
    raise RuntimeError("Timedtext fallback: YouTube returned no usable "
                       "caption data in vtt or json3 format.")


def download_captions_any(url, lang, out_dir, method="auto"):
    """Download captions, trying yt-dlp first (unless --method
    timedtext) and the direct timedtext endpoint as a fallback.
    Returns (vtt_path, source) where source is 'yt-dlp' or 'timedtext'."""
    if method in ("auto", "ytdlp"):
        try:
            return download_captions(url, lang, out_dir), "yt-dlp"
        except RuntimeError as exc:
            if method == "ytdlp":
                raise
            log(f"  yt-dlp failed ({exc}); trying the direct "
                "timedtext fallback ...")
    vtt, _track = download_captions_timedtext(url, lang, out_dir)
    return vtt, "timedtext"


def group_into_blocks(cues):
    blocks, cur = [], None
    for c in cues:
        if cur is None or c["start"] - cur["end"] > BLOCK_GAP:
            if cur is not None:
                blocks.append(cur)
            cur = {"start": c["start"], "end": c["end"], "text": c["text"]}
        else:
            # YouTube auto-captions re-emit a rolling line, so the new
            # cue often repeats the tail of the previous text. Drop the
            # longest word-level overlap before appending.
            prev = cur["text"]
            add = c["text"]
            prev_words = prev.split()
            add_words = add.split()
            overlap = 0
            for k in range(min(len(prev_words), len(add_words)), 0, -1):
                if prev_words[-k:] == add_words[:k]:
                    overlap = k
                    break
            if overlap:
                add = " ".join(add_words[overlap:])
            if add:
                cur["text"] += " " + add
            cur["end"] = c["end"]
    if cur is not None:
        blocks.append(cur)
    for b in blocks:
        b["text"] = re.sub(r"\s+", " ", b["text"]).strip()
    return [b for b in blocks if b["text"]]


def build_pdf(out_path, title, info, blocks):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_CENTER
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
            esc(f"Duration: {fmt_time(float(info['duration']))}"), sub_style))
    if info.get("id"):
        story.append(Paragraph(
            esc(f"https://www.youtube.com/watch?v={info['id']}"), link_style))
    story.append(HRFlowable(width="100%", thickness=1, color=DARK,
                            spaceAfter=6))
    for b in blocks:
        story.append(Paragraph(
            esc(f"{fmt_time(b['start'])} – {fmt_time(b['end'])}"), head_style))
        story.append(Paragraph(esc(b["text"]), body_style))
    story.append(Paragraph(
        "గమనిక: ఈ పాఠ్యం YouTube ఆటో-క్యాప్షన్ల నుంచి తయారైంది. "
        "అర్థంలో సందేహం ఉంటే వీడియోను చూడండి.",
        note_style))
    doc.build(story)
    log(f"PDF written: {out_path}")


def run_check():
    problems = []
    for pkg in ("reportlab",):
        try:
            __import__(pkg)
        except ImportError:
            problems.append(f"Python package missing: {pkg}")
    if not shutil.which("yt-dlp"):
        # Not fatal in v3: the timedtext fallback needs no yt-dlp.
        print("  note: yt-dlp not found -- the --method timedtext route "
              "still works without it (pip install yt-dlp for the "
              "default auto route).")
    try:
        find_telugu_font()
    except RuntimeError as exc:
        problems.append(str(exc))
    if problems:
        print("Setup check FAILED:")
        for p in problems:
            print("  -", p)
        return 1
    print("Setup check OK: yt-dlp, reportlab, Telugu font all ready. "
          "No API key needed -- captions are free.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--url", help="YouTube video URL")
    ap.add_argument("--output", help="output PDF path")
    ap.add_argument("--lang", default=LANGUAGE,
                    help="caption language code (default te for Telugu)")
    ap.add_argument("--method", default="auto",
                    choices=("auto", "ytdlp", "timedtext"),
                    help="caption download route: auto (yt-dlp, then the "
                    "direct timedtext fallback), ytdlp only, or timedtext "
                    "only (use this if yt-dlp keeps getting HTTP 429)")
    ap.add_argument("--vtt", help="use an already-downloaded .vtt file "
                    "instead of downloading (for testing)")
    ap.add_argument("--check", action="store_true", help="setup check only")
    args = ap.parse_args()

    if args.check:
        return run_check()
    url = args.url or VIDEO_URL
    if not url and not args.vtt:
        print("Give a YouTube link: --url <link>, or set VIDEO_URL "
              "at the top of the script.")
        return 2
    work = Path(WORK_DIR)
    work.mkdir(parents=True, exist_ok=True)
    if args.vtt:
        vtt = Path(args.vtt)
        info = {"title": vtt.stem, "duration": "", "uploader": "", "id": ""}
    else:
        # get_video_info itself uses yt-dlp, so it can also hit the 429.
        # If it fails and we have a fallback route, keep going with
        # minimal info (video id from the URL) instead of dying here.
        try:
            info = get_video_info(url)
        except RuntimeError as exc:
            if args.method == "ytdlp":
                raise
            log(f"  yt-dlp could not read video info ({exc}); "
                "continuing with the timedtext route using the video id.")
            vid = _video_id_from_url(url)
            info = {"title": vid, "duration": "", "uploader": "", "id": vid}
        log(f"  video: {info.get('title') or info.get('id')} "
            f"({fmt_time(float(info['duration'] or 0))})")
        vtt, source = download_captions_any(url, args.lang, work,
                                            method=args.method)
        log(f"  captions via: {source}")
    cues = parse_vtt(vtt)
    log(f"  parsed {len(cues)} caption cues")
    if not cues:
        print("No caption text found in the VTT file -- the video may "
              "have no real captions in this language.")
        return 1
    blocks = group_into_blocks(cues)
    log(f"  grouped into {len(blocks)} blocks")
    total = float(info.get("duration") or 0)
    if total > 0:
        covered = 0.0
        cur_end = 0.0
        for a, b in sorted((c["start"], c["end"]) for c in cues):
            covered += max(0.0, b - max(a, cur_end))
            cur_end = max(cur_end, b)
        log(f"  coverage: {fmt_time(covered)} of {fmt_time(total)} "
            f"({100 * covered / total:.1f}% of the video has captions)")
        if covered < total * 0.5:
            log("  WARNING: less than half the video has captions -- "
                "read the PDF; the captions may be sparse.")
    (work / "transcript.json").write_text(
        json.dumps({"video": info, "cues": cues, "blocks": blocks},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    (work / "transcript.txt").write_text(
        "\n\n".join(f"[{fmt_time(b['start'])} – {fmt_time(b['end'])}]\n"
                    f"{b['text']}" for b in blocks),
        encoding="utf-8")
    log(f"  transcript saved: {work / 'transcript.json'}")
    out = args.output or OUTPUT_PDF
    if not out:
        title = info.get("title") or "youtube_telugu"
        safe = re.sub(r'[\\/:*?"<>|]+', "_", title).strip() or "youtube_telugu"
        out = f"{safe}.pdf"
    build_pdf(out, info.get("title") or "YouTube video", info, blocks)
    log(f"Done. Book: {out}  ({len(blocks)} blocks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
