#!/usr/bin/env python3
"""run_book.py — THE single entry point for making a Telugu book.

Give it a source book and a config; it produces the finished PDF + Word:

    python3 run_book.py <archive.org id | URL | local file> --config book_config.json

    # or, when a translated Markdown already exists (no AI key needed):
    python3 run_book.py --config book_config.json --translated-md translated.md

Pipeline:
  1. FETCH     archive.org: download the item's djvu txt + djvu xml via the
               metadata API and extract per-page text. Local input may be a
               djvu .txt, a djvu .xml, or a scan .pdf (pdftotext per page).
  2. SPLIT     the source into part files by scan-page ranges
               (config "part_pages", default 50) under <work-dir>/parts/,
               each part headed by a coverage marker
               <!-- PART k: scan pages A-B -->.
  3. TRANSLATE each part with an AI model, following TRANSLATION_FORMAT.md
               (the rules are embedded as TRANSLATION_PROMPT below). Any
               OpenAI-compatible chat-completions endpoint works. The API key
               comes from the BOOK_AI_API_KEY environment variable; base URL
               and model from config (ai_base_url / ai_model) or the
               BOOK_AI_BASE_URL / BOOK_AI_MODEL environment variables.
               Already-translated parts are skipped, so a run can resume.
               Without a key this step stops with a clear bilingual message.
  4. MERGE + VERIFY  concatenate the parts in order and check: coverage
               markers tile the whole book exactly once (when present),
               [అస్పష్టం] count, word count, and a duplicate-phrase
               fabrication sanity check.
  5. BUILD     make_book.build_pdf + make_docx.build_docx — the verified
               two-pass clickable-index PDF and the Word file.

Note: for very large books the API translation is best-effort (a part is
sent in chunks of config "ai_chunk_chars"); the same TRANSLATION_FORMAT.md
also drives the agent/manual workflow, whose output can be fed back in
with --translated-md.
"""
import argparse, json, os, re, subprocess, sys, time
import urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_book
import make_docx

NO_KEY_MESSAGE = """\
========================================================================
అనువాదానికి AI API కీ అవసరం. BOOK_AI_API_KEY అనే ఎన్విరాన్‌మెంట్
వేరియబుల్‌ను సెట్ చేసి మళ్లీ నడపండి — లేదా ఇప్పటికే అనువదించిన
Markdown ఫైలు ఉంటే --translated-md <file> అనే ఎంపికతో నేరుగా
పుస్తక నిర్మాణానికి వెళ్లవచ్చు.

Translation needs an AI API key: set the BOOK_AI_API_KEY environment
variable and re-run, or pass --translated-md <file> with an
already-translated Markdown to go straight to the build.
========================================================================"""

# The translation format rules (distilled from TRANSLATION_FORMAT.md) sent
# to the model as the system prompt.
TRANSLATION_PROMPT = """\
You are translating a classical Indian book (Sanskrit / Hindi / Tamil /
English) into Telugu, for a printed Telugu edition. Follow these rules
exactly:

1. MIRROR METHOD: keep the source's sections, order and arrangement
   unchanged. Translate everything in the input — prefaces, notes, lists —
   nothing dropped, nothing added except the format elements below.
2. VERSE (shlokas/mantras): give the shloka transliterated as-is into
   Telugu script, then "ప్రతిపదార్థం:" (word-by-word meanings in Telugu),
   then "తాత్పర్యం:" (the purport in Telugu carrying the WHOLE meaning of
   the shloka — every idea and detail), and, if the user message says
   భావం is required, then "భావం:" (the essence, in Telugu).
3. PROSE: translate fully and richly into Telugu — the author's argument,
   background and implications, not a bare literal rendering.
4. When a chapter ends inside your input, add its summary at the end under
   the exact heading "## అధ్యాయ సారాంశం".
5. Where the source text is unreadable or garbled, write [అస్పష్టం].
   NEVER guess a wording, a name, or a quantity.
6. Proper names in Telugu script, with the original in brackets at first
   occurrence.
7. Begin your output with the exact marker line given in the user message
   (an HTML comment), then output ONLY the Telugu translation in Markdown:
   ## for section headings, ### for subsections. No explanations, no
   preamble, no code fences."""


# ------------------------------------------------------------------ fetch

def archive_identifier(source):
    """Return the archive.org identifier if `source` names one, else None."""
    if source.startswith('http'):
        m = re.search(r'archive\.org/(?:details|download)/([^/\s]+)', source)
        return m.group(1) if m else None
    if re.fullmatch(r'[A-Za-z0-9._-]+', source) and not os.path.exists(source):
        return source
    return None


def download(url, dst):
    req = urllib.request.Request(url, headers={'User-Agent': 'telugu-book-maker/1.0'})
    with urllib.request.urlopen(req) as r, open(dst, 'wb') as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)


def fetch_archive(identifier, srcdir):
    """Download the djvu txt + xml for an archive.org item (cached).
    Returns (txt_path_or_None, xml_path_or_None)."""
    os.makedirs(srcdir, exist_ok=True)
    meta_url = f'https://archive.org/metadata/{identifier}'
    req = urllib.request.Request(meta_url, headers={'User-Agent': 'telugu-book-maker/1.0'})
    with urllib.request.urlopen(req) as r:
        meta = json.load(r)
    files = [f['name'] for f in meta.get('files', [])]
    txt_name = next((n for n in files if n.endswith('_djvu.txt')), None)
    xml_name = next((n for n in files if n.endswith('_djvu.xml')), None)
    if not txt_name and not xml_name:
        raise SystemExit(f'FETCH FAILED: no djvu text files found for "{identifier}"')

    def get(name):
        if not name:
            return None
        dst = os.path.join(srcdir, name)
        if not os.path.exists(dst):
            print('downloading', name)
            download(f'https://archive.org/download/{identifier}/'
                     + urllib.parse.quote(name), dst)
        return dst

    return get(txt_name), get(xml_name)


def pages_from_xml(xml_path):
    """Per-page text from a djvu XML file: one string per <OBJECT> (page),
    words joined line by line."""
    pages = []
    root = ET.parse(xml_path).getroot()
    for obj in root.iter():
        if obj.tag.split('}')[-1] != 'OBJECT':
            continue
        lines = []
        for line in obj.iter():
            if line.tag.split('}')[-1] != 'LINE':
                continue
            words = [w.text or '' for w in line.iter()
                     if w.tag.split('}')[-1] == 'WORD']
            if words:
                lines.append(' '.join(words))
        pages.append('\n'.join(lines))
    return pages


def pages_from_txt(txt_path):
    """Per-page text from a djvu txt: split on form feeds; if the file is
    one continuous stream (common), group lines into pseudo-pages of 45."""
    with open(txt_path, encoding='utf-8', errors='replace') as f:
        raw = f.read()
    if '\f' in raw:
        pages = [p.strip() for p in raw.split('\f')]
        return [p for p in pages if p or True][:-1] if pages and not pages[-1] else pages
    lines = raw.split('\n')
    return ['\n'.join(lines[i:i + 45]) for i in range(0, len(lines), 45)]


def pages_from_pdf(pdf_path):
    out = subprocess.run(['pdftotext', '-layout', pdf_path, '-'],
                         capture_output=True, text=True, check=True).stdout
    pages = out.split('\f')
    if pages and not pages[-1].strip():
        pages.pop()
    return pages


def extract_pages(source, workdir):
    """Return the list of per-page source texts for any supported input."""
    srcdir = os.path.join(workdir, 'source')
    ident = archive_identifier(source)
    if ident:
        txt, xml = fetch_archive(ident, srcdir)
        if xml:
            pages = pages_from_xml(xml)
            if pages:
                return pages
        return pages_from_txt(txt)
    if not os.path.exists(source):
        raise SystemExit(f'INPUT ERROR: source not found: {source}')
    low = source.lower()
    if low.endswith('.xml'):
        return pages_from_xml(source)
    if low.endswith('.pdf'):
        return pages_from_pdf(source)
    return pages_from_txt(source)


# ------------------------------------------------------------------ split

def split_parts(pages, part_pages, partsdir):
    """Write part files by page ranges; each starts with a coverage marker.
    Returns [(part_no, first_page, last_page, path)]."""
    os.makedirs(partsdir, exist_ok=True)
    parts = []
    for start in range(0, len(pages), part_pages):
        chunk = pages[start:start + part_pages]
        k = len(parts) + 1
        a, b = start + 1, start + len(chunk)
        path = os.path.join(partsdir, f'part_{k:02d}.txt')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(f'<!-- PART {k}: scan pages {a}-{b} -->\n')
            for i, text in enumerate(chunk):
                f.write(f'\n<!-- scan page {a + i} -->\n{text}\n')
        parts.append((k, a, b, path))
    return parts


# ------------------------------------------------------------------ translate

def ai_settings(cfg):
    base = (os.environ.get('BOOK_AI_BASE_URL') or cfg.get('ai_base_url')
            or 'https://api.openai.com/v1').rstrip('/')
    model = os.environ.get('BOOK_AI_MODEL') or cfg.get('ai_model') or 'gpt-4o'
    key = os.environ.get('BOOK_AI_API_KEY')
    return base, model, key


def translate_text(text, base, model, key, bhavam=False):
    """Translate ONE chunk of source text into Telugu Markdown by calling an
    OpenAI-compatible chat-completions endpoint. This is the single,
    documented place where the AI translation happens; it is only called
    when BOOK_AI_API_KEY is set."""
    system = TRANSLATION_PROMPT
    if bhavam:
        system += '\n8. For this book, భావం IS required after every తాత్పర్యం.'
    body = json.dumps({
        'model': model,
        'temperature': 0.3,
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': text},
        ],
    }).encode('utf-8')
    req = urllib.request.Request(
        base + '/chat/completions', data=body, method='POST',
        headers={'Authorization': f'Bearer {key}',
                 'Content-Type': 'application/json'})
    last_err = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                data = json.load(r)
            return data['choices'][0]['message']['content']
        except Exception as e:  # retry once on transient failures
            last_err = e
            time.sleep(5)
    raise SystemExit(f'TRANSLATION FAILED: the AI endpoint did not respond: {last_err}')


def chunk_text(text, max_chars):
    """Split a part into chunks of at most max_chars on line boundaries."""
    chunks, cur = [], ''
    for line in text.split('\n'):
        if cur and len(cur) + len(line) + 1 > max_chars:
            chunks.append(cur)
            cur = ''
        cur += line + '\n'
    if cur.strip():
        chunks.append(cur)
    return chunks


def translate_part(part_path, out_path, cfg, base, model, key):
    """Translate one part file (resumable: skipped if out_path exists)."""
    if os.path.exists(out_path):
        print('  already translated, skipping:', os.path.basename(out_path))
        return
    with open(part_path, encoding='utf-8') as f:
        text = f.read()
    marker = text.split('\n', 1)[0]
    max_chars = int(cfg.get('ai_chunk_chars', 12000))
    chunks = chunk_text(text, max_chars)
    print(f'  translating {os.path.basename(part_path)} in {len(chunks)} chunk(s)...')
    out = []
    for i, ch in enumerate(chunks):
        note = (f'\n\n[Chunk {i + 1} of {len(chunks)} of this part. Translate '
                'seamlessly; the marker line appears only in chunk 1.]') if i else ''
        result = translate_text(ch + note, base, model, key,
                                bhavam=bool(cfg.get('include_bhavam')))
        if i == 0 and not result.lstrip().startswith(marker):
            result = marker + '\n\n' + result
        out.append(result)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n\n'.join(out))
    print('  ->', os.path.basename(out_path))


# ------------------------------------------------------------------ merge + verify

PART_MARKER = re.compile(r'<!--\s*PART\s+(\d+):\s*scan pages\s+(\d+)-(\d+)\s*-->')


def verify_merged(text, total_pages=None):
    """The merge checks from TRANSLATION_FORMAT.md §5. Prints a report;
    raises SystemExit on a hard failure (broken coverage)."""
    print('--- merge verification ---')
    words = text.split()
    print(f'word count: {len(words)}')
    print(f'[అస్పష్టం] marks: {text.count("[అస్పష్టం]")}')
    markers = [(int(k), int(a), int(b)) for k, a, b in PART_MARKER.findall(text)]
    if markers:
        markers.sort()
        expect = 1
        for k, a, b in markers:
            if a != expect:
                raise SystemExit(f'VERIFY FAILED: coverage gap/overlap — part {k} '
                                 f'starts at scan page {a}, expected {expect}')
            expect = b + 1
        if total_pages is not None and expect != total_pages + 1:
            raise SystemExit(f'VERIFY FAILED: coverage ends at scan page {expect - 1}, '
                             f'source has {total_pages}')
        print(f'coverage: {len(markers)} parts tile scan pages '
              f'1-{expect - 1} exactly once — OK')
    else:
        print('coverage: no part markers in the merged file — check skipped '
              '(verify page coverage manually, per TRANSLATION_FORMAT.md)')
    # fabrication sanity check: a word 5-gram repeated implausibly often is
    # the fingerprint of pattern-generated text (see TRANSLATION_FORMAT.md).
    grams = Counter(tuple(words[i:i + 5]) for i in range(len(words) - 4))
    if grams:
        phrase, count = grams.most_common(1)[0]
        print(f'most repeated 5-word phrase: {count}x — {" ".join(phrase)}')
        if count > 25:
            print('WARNING: one phrase repeats suspiciously often. Spot-check '
                  'that passage against the source pages before building — '
                  'pattern-generated text must never reach the book.')
    print('summaries (## అధ్యాయ సారాంశం):', text.count('## ' + 'అధ్యాయ సారాంశం'))
    print('--- verification done ---')


# ------------------------------------------------------------------ main

def main(argv=None):
    ap = argparse.ArgumentParser(
        description='One command: source book -> finished Telugu PDF + Word.')
    ap.add_argument('source', nargs='?',
                    help='archive.org identifier/URL, or a local djvu .txt/.xml or scan .pdf')
    ap.add_argument('--config', required=True, help='book_config.json for the book')
    ap.add_argument('--translated-md',
                    help='already-translated Markdown: skip fetch/split/translate')
    ap.add_argument('--work-dir', help='runner scratch folder '
                                       '(default: <config folder>/run_work)')
    ap.add_argument('--output-dir', help='override the config output_dir')
    ap.add_argument('--part-pages', type=int,
                    help='scan pages per translation part (default: config or 50)')
    args = ap.parse_args(argv)

    cfg = make_book.load_config(args.config)
    workdir = args.work_dir or os.path.join(cfg['_root'], 'run_work')
    os.makedirs(workdir, exist_ok=True)

    if args.translated_md:
        merged_path = os.path.abspath(args.translated_md)
        with open(merged_path, encoding='utf-8') as f:
            merged = f.read()
        verify_merged(merged)
    else:
        if not args.source:
            ap.error('a source is required unless --translated-md is given')
        base, model, key = ai_settings(cfg)
        if not key:
            print(NO_KEY_MESSAGE, file=sys.stderr)
            return 2
        print('fetching / extracting source...')
        pages = extract_pages(args.source, workdir)
        print(f'source pages: {len(pages)}')
        part_pages = args.part_pages or int(cfg.get('part_pages', 50))
        parts = split_parts(pages, part_pages, os.path.join(workdir, 'parts'))
        print(f'split into {len(parts)} part(s) of up to {part_pages} pages')
        tdir = os.path.join(workdir, 'translated')
        os.makedirs(tdir, exist_ok=True)
        for k, a, b, path in parts:
            translate_part(path, os.path.join(tdir, f'part_{k:02d}.md'),
                           cfg, base, model, key)
        merged = ''
        for k, a, b, path in parts:
            with open(os.path.join(tdir, f'part_{k:02d}.md'), encoding='utf-8') as f:
                merged += f.read().rstrip() + '\n\n'
        merged_path = os.path.join(workdir, 'translated.md')
        with open(merged_path, 'w', encoding='utf-8') as f:
            f.write(merged)
        verify_merged(merged, total_pages=len(pages))

    # build: the merged translation becomes the book's source_md
    cfg['source_md'] = merged_path
    if args.output_dir:
        cfg['output_dir'] = os.path.abspath(args.output_dir)
    pdf = make_book.build_pdf(cfg)
    docx = make_docx.build_docx(cfg)
    print('\nDONE. Finished book:')
    print('  PDF :', pdf)
    print('  Word:', docx)
    return 0


if __name__ == '__main__':
    sys.exit(main())
