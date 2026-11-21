#!/usr/bin/env python3
"""make_book.py — generic Telugu book PDF builder.

Builds a finished, print-ready Telugu book PDF from a translated Markdown
file, driven entirely by a JSON config (no book-specific values in code).

What it does (the proven Sarva Karma / Hindu America pipeline, generalized):
  1. Reads the translated Markdown + book_config.json.
  2. Prepares pictures: copies them into the build folder, resizing the
     image FILES themselves to <= image_max_width_px (LibreOffice renders
     images at native pixel size and ignores CSS widths).
  3. Builds styled HTML: all-Telugu cover page, maroon #8B1A1A headings,
     saffron #C8861A rules, cream quote boxes, and every 'అధ్యాయ సారాంశం'
     section wrapped as a tinted summary box (single-cell table with
     INLINE styles — LibreOffice ignores table CSS classes).
  4. Two-pass CLICKABLE index: pass 1 converts without page numbers and
     locates each TOC section's start page (PDF outline + pdftotext
     verification); pass 2 rebuilds with page numbers in Telugu digits
     and re-verifies that pagination did not shift.
  5. Verifies that every index link annotation in the PDF resolves to the
     expected section start page — fails loudly if not.
  6. Stamps a running header + Telugu-digit footer page numbers on every
     page except the cover (reportlab + pypdf), then re-verifies the
     links survived stamping.

Usage:
    python3 make_book.py [path/to/book_config.json]   (default: book_config.json)

Config paths are resolved relative to the config file's directory.
See book_config.example.json for every key.
"""
import glob, io, json, os, re, subprocess, sys

TE_DIGITS = str.maketrans('0123456789', '౦౧౨౩౪౫౬౭౮౯')

CSS = """
@page { size: A4; margin: 18mm 16mm 18mm 16mm; }
body { font-family: 'Noto Serif Telugu','Noto Sans Telugu',serif; font-size: 11.5pt; line-height: 1.75; color: #1a1a1a; }
.cover { page-break-after: always; text-align: center; }
.cover .kicker { font-size: 11pt; color: #7A4A12; letter-spacing: 2pt; margin-top: 8pt; }
.cover h1 { font-size: 30pt; color: #8B1A1A; margin: 8pt 0 4pt 0; line-height: 1.25; }
.cover .rule { width: 90mm; border-top: 2px solid #C8861A; margin: 8pt auto; }
.cover .subtitle { font-size: 14pt; color: #333; line-height: 1.6; margin: 6pt 20mm; }
.cover img { width: 92mm; margin: 12pt auto 6pt auto; border: 1px solid #C8861A; padding: 3pt; background: #fff; }
.cover .author { font-size: 13pt; color: #8B1A1A; margin-top: 8pt; }
.cover .meta { font-size: 10pt; color: #555; margin-top: 6pt; line-height: 1.6; }
h2 { font-size: 16pt; color: #8B1A1A; border-bottom: 2px solid #C8861A; padding-bottom: 4pt; page-break-before: always; page-break-after: avoid; }
h3 { font-size: 13pt; color: #7A4A12; page-break-after: avoid; margin-top: 14pt; }
blockquote { background: #FFF8E7; border-left: 4px solid #C8861A; margin: 10pt 0; padding: 8pt 12pt; color: #333; font-size: 11pt; }
table.summarybox { margin-top: 14pt; width: 100%; border-collapse: collapse; }
table.summarybox td { background: #F4F8EC; border: 1px solid #7A7A3A; padding: 10pt 12pt; }
table.summarybox h2 { border: none; page-break-before: auto; font-size: 13pt; margin: 0 0 6pt 0; padding-bottom: 0; }
.idx h2 { page-break-before: auto; }
.idx ul { list-style: none; padding-left: 0; }
.idx li { margin-bottom: 5pt; font-size: 12pt; }
.idx a { color: #8B1A1A; text-decoration: none; }
.idx .pg { color: #555; }
.photo { text-align: center; margin: 12pt 0; page-break-inside: avoid; }
.photo img { border: 1px solid #C8861A; padding: 3pt; background: #fff; }
.cap { font-size: 9.5pt; color: #7A4A12; text-align: center; margin: 4pt 0 10pt 0; font-style: italic; }
"""

DEFAULTS = {
    'subtitle': '',
    'cover_kicker': '',
    'cover_author': '',
    'cover_meta': [],
    'index_title': 'విషయ సూచిక',
    'summary_heading': 'అధ్యాయ సారాంశం',
    'toc': 'auto',                 # 'auto' or a list of {"heading": "...", "display": "..."}
    'images': [],                   # [{"match_type": "line|substr|heading", "match": "...",
                                    #   "file": "...", "caption": "..."}]
    'image_max_width_px': 640,      # body pictures: resize the FILE to at most this width
    'cover_image_max_width_px': 430,
    'drop_first_h1': True,          # the cover carries the title; drop the MD's own H1
    'work_dir': 'build',
    'soffice_timeout': 1800,
}

REQUIRED = ('title', 'header', 'source_md', 'cover_image', 'output_dir', 'output_pdf')


def load_config(path):
    with open(path, encoding='utf-8') as f:
        cfg = json.load(f)
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in cfg.items() if not k.startswith('_')})
    for key in REQUIRED:
        if key not in merged:
            raise SystemExit(f'CONFIG ERROR: missing required key "{key}" in {path}')
    merged['_root'] = os.path.dirname(os.path.abspath(path))
    merged['_config_path'] = os.path.abspath(path)
    return merged


def resolve(cfg, p):
    return p if os.path.isabs(p) else os.path.join(cfg['_root'], p)


def heading_text(line):
    return line.lstrip('#').strip()


def read_md(cfg):
    with open(resolve(cfg, cfg['source_md']), encoding='utf-8') as f:
        return f.read()


# ---------------------------------------------------------------- TOC

def resolve_toc(cfg, md):
    """Return [{'anchor', 'display', 'heading'}] for the clickable index.

    Config 'toc' is either 'auto' (every level-2 heading except the summary
    heading, in document order) or an explicit list of
    {"heading": "<exact MD heading line>", "display": "<optional text>"}.
    """
    toc_cfg = cfg['toc']
    entries = []
    if toc_cfg == 'auto':
        for line in md.split('\n'):
            if line.startswith('## ') and heading_text(line) != cfg['summary_heading']:
                entries.append({'heading': line, 'display': heading_text(line)})
    else:
        for e in toc_cfg:
            entries.append({'heading': e['heading'],
                            'display': e.get('display') or heading_text(e['heading'])})
    if not entries:
        raise SystemExit('CONFIG ERROR: the table of contents is empty '
                         '(no "toc" entries and no ## headings found)')
    for i, e in enumerate(entries):
        e['anchor'] = f'sec{i + 1}'
    return entries


# ---------------------------------------------------------------- images

def prepare_images(cfg):
    """Copy cover + body images into <work_dir>/images, resizing the files
    themselves. Returns {config file path: 'images/<name>'} plus '__cover__'.
    """
    from PIL import Image
    imgdir = os.path.join(resolve(cfg, cfg['work_dir']), 'images')
    os.makedirs(imgdir, exist_ok=True)
    mapping = {}

    def prep(src_rel, maxw):
        if src_rel in mapping:
            return mapping[src_rel]
        src = resolve(cfg, src_rel)
        if not os.path.exists(src):
            raise SystemExit(f'IMAGE ERROR: not found: {src}')
        name = os.path.basename(src)
        dst = os.path.join(imgdir, name)
        with Image.open(src) as im:
            fmt = im.format
            if im.width > maxw:
                h = round(im.height * maxw / im.width)
                im = im.resize((maxw, h), Image.LANCZOS)
            if fmt in ('PNG', 'JPEG'):
                im.save(dst)
            else:  # e.g. WEBP -> PNG so LibreOffice can read it
                name = os.path.splitext(name)[0] + '.png'
                dst = os.path.join(imgdir, name)
                im.save(dst)
        mapping[src_rel] = 'images/' + name
        return mapping[src_rel]

    mapping['__cover__'] = prep(cfg['cover_image'], cfg['cover_image_max_width_px'])
    for e in cfg['images']:
        prep(e['file'], cfg['image_max_width_px'])
    return mapping


# ---------------------------------------------------------------- HTML

def cover_html(cfg, imgmap):
    parts = ['<div class="cover">']
    if cfg.get('cover_kicker'):
        parts.append(f'<p class="kicker">{cfg["cover_kicker"]}</p>')
    parts.append(f'<h1>{cfg["title"]}</h1>')
    parts.append('<div class="rule"></div>')
    if cfg.get('subtitle'):
        parts.append(f'<p class="subtitle">{cfg["subtitle"]}</p>')
    parts.append(f'<img src="{imgmap["__cover__"]}">')
    if cfg.get('cover_author'):
        parts.append(f'<p class="author">{cfg["cover_author"]}</p>')
    if cfg.get('cover_meta'):
        parts.append('<p class="meta">' + '<br>'.join(cfg['cover_meta']) + '</p>')
    parts.append('</div>\n')
    return '\n'.join(parts)


def index_html(cfg, toc, pages=None):
    items = []
    for e in toc:
        pg = ''
        if pages and e['display'] in pages:
            pg = f' <span class="pg">— పుట {str(pages[e["display"]]).translate(TE_DIGITS)}</span>'
        items.append(f'<li><a href="#{e["anchor"]}">{e["display"]}</a>{pg}</li>')
    return (f'<div class="idx">\n<h2>{cfg["index_title"]}</h2>\n<ul>\n' +
            '\n'.join(items) + '\n</ul>\n</div>\n')


def _line_matches(line, match_type, match):
    if match_type == 'line':
        return line == match
    if match_type == 'substr':
        return match in line
    if match_type == 'heading':
        if line.startswith('#'):
            return heading_text(line) == match
        m = re.match(r'^<h2 id="[^"]+">(.*)</h2>$', line)
        return bool(m) and m.group(1) == match
    raise SystemExit(f'CONFIG ERROR: unknown image match_type "{match_type}"')


def preprocess(cfg, md, toc, imgmap, idx_html):
    lines = md.split('\n')
    # drop the book's own H1 title line (the cover carries the title)
    if cfg['drop_first_h1']:
        for i, l in enumerate(lines[:5]):
            if l.strip():
                if l.startswith('# '):
                    del lines[i]
                break
    # --- anchors on TOC target headings
    for e in toc:
        hits = [i for i, l in enumerate(lines) if l == e['heading']]
        if len(hits) != 1:
            raise SystemExit(f'TOC ERROR: heading not found exactly once '
                             f'({len(hits)} hits): {e["heading"]}')
        lines[hits[0]] = f'<h2 id="{e["anchor"]}">{heading_text(e["heading"])}</h2>'
    # --- image insertions (right after the matched line)
    for e in cfg['images']:
        mtype = e.get('match_type', 'line')
        hits = [i for i, l in enumerate(lines) if _line_matches(l, mtype, e['match'])]
        if len(hits) != 1:
            raise SystemExit(f'IMAGE ERROR: match not unique ({len(hits)} hits) '
                             f'for image {e["file"]}: {e["match"]}')
        i = hits[0]
        div = (f'\n<div class="photo"><img src="{imgmap[e["file"]]}">\n'
               f'<p class="cap">{e["caption"]}</p></div>\n')
        lines[i] = lines[i] + '\n' + div
    md = '\n'.join(lines)
    # --- wrap each summary section in a tinted single-cell table (inline
    # styles; LibreOffice ignores table CSS classes)
    import markdown as _md
    summary = re.escape(cfg['summary_heading'])

    def wrap_summary(mm):
        body = mm.group(1).strip()
        paras = ''.join(_md.markdown(p, extensions=['extra'])
                        for p in body.split('\n\n') if p.strip())
        return ('<table class="summarybox" style="background:#F4F8EC; border:1px solid #7A7A3A; margin-top:14pt; width:100%; border-collapse:collapse;"><tr>'
                '<td style="background:#F4F8EC; border:1px solid #7A7A3A; padding:10pt 12pt;">'
                f'<h2>{cfg["summary_heading"]}</h2>' + paras + '</td></tr></table>\n')

    md = re.sub(r'(?ms)^## ' + summary + r'\n\n(.*?)(?=^#|^<h2 id=|\Z)', wrap_summary, md)
    # --- cover + index at the front
    return cover_html(cfg, imgmap) + idx_html + '\n' + md


def md_to_html(md):
    import markdown
    body = markdown.markdown(md, extensions=['extra', 'md_in_html'])
    return ('<!DOCTYPE html><html><head><meta charset="utf-8"><style>' + CSS +
            '</style></head><body>\n' + body + '\n</body></html>')


# ---------------------------------------------------------------- conversion

def soffice(infile, outdir, fmt, timeout=1800):
    cmd = ['soffice', '--headless', '--convert-to', fmt, '--outdir', outdir, infile]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        print(r.stdout, r.stderr)
        raise SystemExit(f'BUILD FAILED: LibreOffice {fmt} conversion failed')


def norm(s):
    return re.sub(r'[\s—\-–,.()]+', '', s)


def outline_pages(pdf):
    """(title, page) pairs from the PDF outline, in document order."""
    from pypdf import PdfReader
    r = PdfReader(pdf)
    res = []

    def walk(items):
        for it in items:
            if isinstance(it, list):
                walk(it)
            else:
                try:
                    res.append((it.title.strip(), r.get_destination_page_number(it) + 1))
                except Exception:
                    pass
    walk(r.outline)
    return res


def pdf_pages_text(pdf):
    txt = pdf + '.txt'
    subprocess.run(['pdftotext', '-layout', pdf, txt], check=True)
    with open(txt, encoding='utf-8', errors='replace') as f:
        return f.read().split('\f')


def find_start_pages(cfg, toc, pdf):
    out = outline_pages(pdf)
    if not out:
        raise SystemExit('BUILD FAILED: PDF outline is empty — cannot locate sections')
    idx_hits = [p for t, p in out if t == cfg['index_title']]
    if not idx_hits:
        raise SystemExit(f'BUILD FAILED: index heading "{cfg["index_title"]}" '
                         'not found in the PDF outline')
    idx_page = idx_hits[0]
    pages_norm = [norm(p) for p in pdf_pages_text(pdf)]
    found = {}
    for e in toc:
        htext = heading_text(e['heading'])
        cands = [p for t, p in out if t == htext and p > idx_page]
        if not cands:
            raise SystemExit(f'BUILD FAILED: no outline entry after the index for: {htext}')
        page = cands[0]
        if norm(htext) not in pages_norm[page - 1]:
            raise SystemExit(f'BUILD FAILED: pdftotext does not confirm the heading '
                             f'on outline page {page}: {htext}')
        found[e['display']] = page
    return found


def verify_links(toc, pdf, expected):
    """Check the index link annotations resolve to the expected pages,
    matching entries in reading order (top-to-bottom)."""
    from pypdf import PdfReader
    r = PdfReader(pdf)
    page_ids = [p.indirect_reference.idnum for p in r.pages]
    links = []
    for pi, page in enumerate(r.pages):
        annots = page.get('/Annots')
        if not annots:
            continue
        for a in annots:
            o = a.get_object()
            if o.get('/Subtype') != '/Link':
                continue
            dest = o.get('/Dest')
            if dest is None:
                continue
            dpage = page_ids.index(dest[0].idnum) + 1
            rect = o['/Rect']
            links.append((pi + 1, float(rect[3]), dpage))
    links.sort(key=lambda x: (x[0], -x[1]))
    dests = [d for _, _, d in links]
    exp = [expected[e['display']] for e in toc]
    print(f'link annotations found: {len(dests)}; expected: {len(exp)}')
    if dests != exp:
        print('link dests:', dests)
        print('expected  :', exp)
    return dests == exp


def stamp(cfg, src, dst):
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    W, H = A4
    fonts = glob.glob('/usr/share/fonts/truetype/noto/NotoSansTelugu-Regular.ttf')
    if fonts:
        pdfmetrics.registerFont(TTFont('Telugu', fonts[0]))
        font = 'Telugu'
    else:
        font = 'Helvetica'
    reader = PdfReader(src)
    n = len(reader.pages)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for i in range(1, n + 1):
        if i > 1:
            c.setFont(font, 8.5)
            c.drawCentredString(W / 2, H - 30, cfg['header'])
            c.setFont(font, 10)
            c.drawCentredString(W / 2, 28, '— ' + str(i).translate(TE_DIGITS) + ' —')
        c.showPage()
    c.save()
    buf.seek(0)
    overlay = PdfReader(buf)
    writer = PdfWriter(clone_from=src)
    for i in range(1, len(writer.pages)):
        writer.pages[i].merge_page(overlay.pages[i])
        writer.pages[i].compress_content_streams()
    writer.compress_identical_objects()
    with open(dst, 'wb') as f:
        writer.write(f)
    print('stamped', dst, 'pages:', n)


# ---------------------------------------------------------------- main build

def build_pdf(cfg):
    """Run the full two-pass PDF build. Returns the final PDF path.
    Raises SystemExit with a clear message on any verification failure."""
    build = resolve(cfg, cfg['work_dir'])
    os.makedirs(build, exist_ok=True)
    outdir = resolve(cfg, cfg['output_dir'])
    os.makedirs(outdir, exist_ok=True)
    md = read_md(cfg)
    toc = resolve_toc(cfg, md)
    imgmap = prepare_images(cfg)
    html_path = os.path.join(build, 'book.html')
    pdf1 = os.path.join(build, 'book.pdf')

    # pass 1: index without page numbers
    html1 = md_to_html(preprocess(cfg, md, toc, imgmap, index_html(cfg, toc)))
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html1)
    print('pass 1 HTML written; converting to PDF...')
    sys.stdout.flush()
    soffice(html_path, build, 'pdf', cfg['soffice_timeout'])
    pages = find_start_pages(cfg, toc, pdf1)
    print('start pages:', json.dumps(pages, ensure_ascii=False))

    # pass 2: index with real page numbers
    html2 = md_to_html(preprocess(cfg, md, toc, imgmap, index_html(cfg, toc, pages)))
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html2)
    print('pass 2 HTML written; converting to PDF...')
    sys.stdout.flush()
    soffice(html_path, build, 'pdf', cfg['soffice_timeout'])
    pages2 = find_start_pages(cfg, toc, pdf1)
    if pages2 != pages:
        print('pass 2 start pages:', json.dumps(pages2, ensure_ascii=False))
        raise SystemExit('BUILD FAILED: pagination shifted between passes — aborting')
    print('index page numbers verified against the rebuilt PDF')
    if not verify_links(toc, pdf1, pages):
        raise SystemExit('BUILD FAILED: CLICKABLE INDEX — link destinations do not match')
    print('clickable index verified on the unstamped PDF')

    final = os.path.join(outdir, cfg['output_pdf'])
    stamp(cfg, pdf1, final)
    if not verify_links(toc, final, pages):
        raise SystemExit('BUILD FAILED: CLICKABLE INDEX LOST in stamping — aborting')
    print('clickable index verified on the final stamped PDF')
    print('PDF done ->', final)
    return final


def main(argv):
    cfg = load_config(argv[1] if len(argv) > 1 else 'book_config.json')
    build_pdf(cfg)


if __name__ == '__main__':
    main(sys.argv)
