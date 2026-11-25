# Telugu Book Maker — generic pipeline for whole-book Telugu translations

One pipeline that makes a finished Telugu book — translation, clickable
index, pictures, PDF **and** Word — for **any** book. Nothing book-specific
lives in the code; each book is described by one small `book_config.json`
(see `book_config.example.json`, which holds the real values of the
*Sanskrit* book).

## The one-command path

```bash
# Full run: fetch the source from archive.org, translate it with an AI
# model, verify, and build the finished book.
export BOOK_AI_API_KEY=<your key>
python3 run_book.py <archive.org identifier or URL> --config book_config.json

# A local source file works too (djvu .txt / .xml, or a scan .pdf):
python3 run_book.py ~/books/some-book_djvu.txt --config book_config.json

# Already have the translated Markdown? Skip straight to the build —
# no API key needed:
python3 run_book.py --config book_config.json --translated-md translated.md
```

`run_book.py` chains everything:

1. **Fetch** — for an archive.org identifier/URL it reads the item's
   metadata API, downloads the djvu txt + xml, and extracts per-page text
   (local `.txt` / `.xml` / `.pdf` inputs are read directly).
2. **Split** — the source is cut into part files by scan-page ranges
   (`part_pages` in the config, default 50) under `run_work/parts/`, each
   headed by a coverage marker.
3. **Translate** — each part is translated by an AI model following
   `TRANSLATION_FORMAT.md` (the rules travel inside the code as the prompt
   template). Any **OpenAI-compatible** chat-completions endpoint works:
   set `ai_base_url` / `ai_model` in the config (or `BOOK_AI_BASE_URL` /
   `BOOK_AI_MODEL` in the environment); the key comes only from the
   `BOOK_AI_API_KEY` environment variable. Finished parts are skipped, so
   an interrupted run resumes where it stopped. **Without a key the runner
   stops at this step** with a clear Telugu+English message — use
   `--translated-md` instead.
4. **Merge + verify** — parts are concatenated in order and checked:
   coverage markers tile the whole book exactly once, `[అస్పష్టం]` count,
   word count, and a duplicate-phrase fabrication sanity check.
5. **Build** — `make_book.py` + `make_docx.py` (below) produce the final
   PDF + Word in the config's `output_dir`.

## The stages, run manually

The same pipeline stage by stage — this is also how the four finished
books were actually produced, with the translation done part-by-part
under human/agent supervision:

1. **Fetch the source** — archive.org item: download `<id>_djvu.txt`,
   `<id>_djvu.xml` and the scan PDF; build the per-page text list from the
   XML (index 0 = scan page 1). Identify the book: title, author, scan and
   printed page counts, chapter structure.
2. **Translate in parts** — split by page ranges (~50 pages) and translate
   each part per **TRANSLATION_FORMAT.md**: mirror method (source order
   unchanged), shlokas in Telugu script with ప్రతిపదార్థం + full-meaning
   తాత్పర్యం (+ భావం when the book's brief asks), rich వివరణ for prose,
   `## అధ్యాయ సారాంశం` at every chapter end, `[అస్పష్టం]` wherever the
   scan is unreadable — never guess. Show one sample chapter and the
   pictures early and get the reader's verdict before the long work.
3. **Merge & verify** — concatenate in order; run the checklist in
   TRANSLATION_FORMAT.md §5 (coverage exactly once, counts vs. the source,
   fabrication spot-checks, disclosed deviations).
4. **Prepare pictures** — extract plates/yantras/diagrams from the scan
   PDF (crop them whole; note any scan blemish you cannot crop away), or
   generate contextual pictures when the source has none. The cover uses
   the original book's cover artwork, cleaned, with all cover text in
   Telugu.
5. **Write `book_config.json`** — copy `book_config.example.json` and set
   the title/subtitle/cover lines, the TOC (explicit heading lines, or
   `"auto"`), and the image placements (heading-keyword → file + caption).
6. **Build**:
   ```bash
   python3 make_book.py  book_config.json   # verified clickable-index PDF
   python3 make_docx.py  book_config.json   # matching Word file
   ```
   `make_book.py` builds styled HTML (maroon `#8B1A1A` headings, saffron
   `#C8861A` rules, summary sections as tinted boxes), resizes image
   **files** to ≤ 640 px wide (LibreOffice ignores CSS image widths),
   converts with LibreOffice, finds every TOC section's start page,
   rebuilds the index with Telugu-digit page numbers, **verifies every
   index link annotation points at the right page**, then stamps the
   running header + Telugu-digit footer (all pages except the cover) and
   verifies the links again. Any failed check aborts the build loudly.
   `make_docx.py` converts the same HTML, strips LibreOffice's linked
   image drawings, re-embeds every picture with python-docx, sets
   page-break-before on body-level section headings, and verifies the
   embedded media count.
7. **Look before you deliver** — render and eyeball the cover, the index,
   a chapter opening, every picture page, and the first and last pages of
   the PDF (and the Word cover/index). Never announce a book — or a fix —
   from counts alone.

## Files

| File | What it is |
|---|---|
| `run_book.py` | The single entry point: source → PDF + Word |
| `make_book.py` | Generic PDF builder (two-pass clickable index, stamping) |
| `make_docx.py` | Generic Word builder (embedded pictures, page breaks) |
| `book_config.example.json` | A complete real config to copy and adapt |
| `TRANSLATION_FORMAT.md` | The translation-stage spec the builders assume |
| `test/` | A tiny sample book proving the whole path end-to-end |

## Dependencies

```bash
pip install --break-system-packages markdown pypdf reportlab python-docx Pillow
# system: LibreOffice (soffice), poppler-utils (pdftotext, pdfinfo)
```

Reinstall the pip packages after any VM replacement — they do not survive
it. Telugu fonts (Noto Sans/Serif Telugu) must be present for LibreOffice
and for the stamped header/footer.

## Test

```bash
cd test
python3 ../run_book.py --config book_config.json --translated-md sample.md
```

This builds the 3-section sample book (cover, clickable 3-entry index, one
summary box, one yantra picture) into `test/output/` — the PDF build
verifies its own index links, and the docx build verifies its embedded
pictures, so a silent pass here means the whole chain works.
