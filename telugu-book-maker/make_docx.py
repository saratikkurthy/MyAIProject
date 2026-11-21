#!/usr/bin/env python3
"""make_docx.py — generic Telugu book Word (.docx) builder.

Uses the same config and the same preprocessing as make_book.py, except the
index carries no page numbers (Word repaginates on its own; the entries
still hyperlink to their sections).

LibreOffice converts HTML->docx leaving images as LINKED drawings, so all
drawing paragraphs are stripped and the images are re-embedded with
python-docx at the right places: the cover image right after the title
paragraph, and each body picture right before its caption paragraph.
Finally every body-level Heading 2 gets pageBreakBefore (LibreOffice drops
the CSS page break; headings inside table cells — the summary boxes — are
not in doc.paragraphs and stay untouched), and the embedded media count in
the finished file is verified.

Usage:
    python3 make_docx.py [path/to/book_config.json]   (default: book_config.json)
"""
import os, subprocess, sys, zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_book import (load_config, resolve, read_md, resolve_toc,
                       prepare_images, preprocess, index_html, md_to_html)

W_NS = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def build_docx(cfg):
    """Run the full docx build. Returns the final docx path.
    Raises SystemExit with a clear message on any failure."""
    from docx import Document
    from docx.shared import Inches
    from PIL import Image

    build = resolve(cfg, cfg['work_dir'])
    os.makedirs(build, exist_ok=True)
    outdir = resolve(cfg, cfg['output_dir'])
    os.makedirs(outdir, exist_ok=True)
    md = read_md(cfg)
    toc = resolve_toc(cfg, md)
    imgmap = prepare_images(cfg)

    html = md_to_html(preprocess(cfg, md, toc, imgmap, index_html(cfg, toc)))
    src = os.path.join(build, 'book_docx.html')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(html)
    print('docx HTML written; converting...')
    sys.stdout.flush()
    # NOTE: the filter name must be ONE argument, without quote characters.
    r = subprocess.run(['soffice', '--headless', '--convert-to',
                        'docx:MS Word 2007 XML',
                        '--outdir', build, src],
                       capture_output=True, text=True, timeout=cfg['soffice_timeout'])
    if r.returncode != 0:
        print(r.stdout, r.stderr)
        raise SystemExit('BUILD FAILED: LibreOffice docx conversion failed')
    docx_path = os.path.join(build, 'book_docx.docx')
    if not os.path.exists(docx_path):
        raise SystemExit('BUILD FAILED: docx was not produced')

    doc = Document(docx_path)

    # strip all paragraphs that contain drawings (LibreOffice's linked images)
    removed = 0
    for p in list(doc.paragraphs):
        if p._p.findall(f'.//{W_NS}drawing'):
            p._p.getparent().remove(p._p)
            removed += 1
    print('stripped linked-image paragraphs:', removed)

    # embed the cover image right after the title paragraph
    cover_path = os.path.join(build, imgmap['__cover__'])
    title_hits = [p for p in doc.paragraphs if p.text.strip() == cfg['title']]
    if not title_hits:
        raise SystemExit(f'BUILD FAILED: cover title paragraph not found in docx: {cfg["title"]}')
    run = title_hits[0].add_run()
    run.add_break()
    run.add_picture(cover_path, width=Inches(cfg.get('docx_cover_width_in', 4.2)))

    # embed the body pictures before their caption paragraphs, in order
    queue = []
    for e in cfg['images']:
        path = os.path.join(build, imgmap[e['file']])
        with Image.open(path) as im:
            w = min(6.5, im.width / 96.0)
        queue.append((path, w, e['caption']))
    qi = 0
    for p in doc.paragraphs:
        if qi >= len(queue):
            break
        if p.text.strip() == queue[qi][2]:
            path, w, _ = queue[qi]
            run = p.insert_paragraph_before().add_run()
            run.add_picture(path, width=Inches(w))
            qi += 1
    print('photos embedded:', qi, 'of', len(queue))
    if qi != len(queue):
        raise SystemExit('BUILD FAILED: not all photos placed in the docx')

    # every body-level H2 starts on a new page
    n = 0
    for p in doc.paragraphs:
        if p.style and p.style.name == 'Heading 2':
            p.paragraph_format.page_break_before = True
            n += 1
    print('Heading 2 paragraphs given page breaks:', n)

    out = os.path.join(outdir, cfg['output_docx'])
    doc.save(out)

    # verify the embedded media count (python-docx dedupes identical files,
    # so the expected count is the number of DISTINCT prepared images)
    expected = len({imgmap['__cover__']} | {imgmap[e['file']] for e in cfg['images']})
    with zipfile.ZipFile(out) as z:
        media = [n for n in z.namelist() if n.startswith('word/media/')]
    print(f'embedded media files: {len(media)}; expected: {expected}')
    if len(media) != expected:
        raise SystemExit('BUILD FAILED: docx media count mismatch — '
                         'some pictures are not embedded')
    print('docx done ->', out)
    return out


def main(argv):
    cfg = load_config(argv[1] if len(argv) > 1 else 'book_config.json')
    if 'output_docx' not in cfg:
        raise SystemExit('CONFIG ERROR: missing required key "output_docx"')
    build_docx(cfg)


if __name__ == '__main__':
    main(sys.argv)
