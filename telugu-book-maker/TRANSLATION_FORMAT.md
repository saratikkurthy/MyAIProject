# Translation Format Spec — how a book becomes the Telugu Markdown

This is the translation-stage contract for the Telugu book pipeline. The
builders (`make_book.py`, `make_docx.py`, `run_book.py`) consume the single
merged Markdown file this stage produces. The rules distill the standing
format agreed with the reader (2026-11-17 / 2026-11-20) and the lessons of
four finished books (Rajamriganka, Vishnu Purana, Hindu America, Sarva Karma
Anushthan Prakash Part 3).

---

## 1. Inputs — get the source

- **archive.org scans** (public-domain books): from the item page take the
  identifier (e.g. `sarva-karma-anushthan-prakash-devi-mantra`). Download:
  - `<id>_djvu.txt` — the OCR text. It is usually ONE continuous stream with
    **no page breaks**; do not rely on form feeds.
  - `<id>_djvu.xml` — the OCR with coordinates; this is the reliable source
    for **per-page text** (index 0 = scan page 1). Build a `pages` list from
    it and keep every later reference in scan-page numbers.
  - the scan PDF itself, later, for extracting plates/yantras/diagrams.
- **Sringeri Purana Sharada** (puranasharada.sringeri.net) for Sanskrit
  Purana texts — verify amsha / chapter / shloka counts there first.
- Before translating anything, **identify the book**: title, author/editor,
  edition, scan page count, printed page count, chapter/section structure.
  Write these down; every later count is checked against them.

## 2. The mirror method (అద్దం పద్ధతి)

The translation **mirrors the source book exactly**: same sections, in the
same order, with the same arrangement. Nothing is reordered, dropped, or
added — the only additions are the format elements of §3 (meanings,
commentary, chapter-end summaries, and bracketed notes where the source
itself is inconsistent). Front matter, prefaces, notes, the book's own
table of contents, and back-matter lists are translated too — never just
the main text.

## 3. Format of the translation

### Verse (Sanskrit shlokas / mantras / stotras)

For **every** shloka, in this order:

1. **The shloka itself in Telugu script** — Devanagari transliterated
   as-is into Telugu script, numbered as in the source.
2. **ప్రతిపదార్థం** — word-by-word meanings, in Telugu.
3. **తాత్పర్యం** — the purport, in Telugu. It must carry the **whole
   meaning** of the shloka — every idea, image, number and detail in it.
   A brief tatparyam under a rich shloka is a defect, not a style.
4. **భావం** — the essence/import, in Telugu — **when the book's brief asks
   for it** (it was requested for Sarva Karma; keep it for a whole book or
   not at all, never mixed).

For long namavalis (sahasranamam name-lists) the shlokas stay complete and
individual, but ప్రతిపదార్థం / తాత్పర్యం / భావం may be grouped by
shloka-groups, with the grouping made visible. Mechanical repetitions
(e.g. the same nyasa repeated for 53 mantras) may be given once as a
pattern with a `[గమనిక]` note saying so.

### Prose (Hindi / Sanskrit / Tamil / English prose)

- Translate **fully and richly** (వివరణ): the author's argument,
  background and implications rendered completely — not a bare,
  sentence-by-sentence literal translation. The reader must never be left
  wanting more explanation. (Standing quality bar, 2026-11-17.)
- Keep the source language straight passage by passage: a book may mix
  Sanskrit verse with Hindi prose, or Sanskrit with Tamil commentary —
  translate from the language each passage actually uses.
- Proper names in Telugu script, with the original (English/source form)
  in brackets at first occurrence.

### Every chapter

- Add a **summary at the END of every chapter**, headed exactly
  `## అధ్యాయ సారాంశం`, in Telugu, in addition to the tatparyam. (The
  builder renders these as tinted summary boxes.) Chapter-opening
  summaries that exist in the source stay where they are.

### When the source is unclear or inconsistent

- Mark any passage the scan/OCR leaves unreadable as **[అస్పష్టం]**.
  **Never guess** a wording, a name, or a quantity. An honest mark is a
  feature of the book; an invented reading is a defect.
- Where the source contradicts itself (a heading count vs. its own list,
  a page number in the printed TOC vs. the text), keep the source's
  numbering/order and record the discrepancy in a `[గమనిక]` note.
- A passage absent from the source's main text but named in its index is
  reported exactly so — never silently dropped, never reconstructed.

## 4. Working in parts

- Split the book into parts by **scan-page ranges** (about 50 pages each)
  or by whole chapters/sections; keep a manifest: part → page range →
  chapter/shloka counts.
- Translate part by part into separate files (`part_01.md`, `part_02.md`, …).
- **Check every boundary against the source before merging**: each part
  must start exactly where the source says it starts; no chapter split,
  duplicated, or skipped at a seam.
- Show **one sample chapter and the planned pictures early**, and get the
  reader's verdict on the depth of explanation before the long work begins.

## 5. Merge + verification checklist

Merge the parts in order into one Markdown file, then verify — all of
these, every time:

- [ ] **Coverage**: scan pages 1…N are covered **exactly once** — no gaps,
      no overlaps (walk the part manifest and the seam pages).
- [ ] **Counts vs. source**: chapters, sections and shlokas counted in the
      merged file match the source's own counts/structure.
- [ ] **Fabrication check**: pattern-generated text is the known failure
      mode for long repetitive lists (it happened once: a sahasranamam's
      middle was "reconstructed" from a pattern instead of translated).
      Search the merged file for any phrase repeated an implausible number
      of times, and spot-check list middles against the actual source
      pages. Zero invented text is the bar.
- [ ] **Summaries**: one `## అధ్యాయ సారాంశం` per chapter, at chapter ends.
- [ ] **[అస్పష్టం] count**: counted and reported honestly in the delivery
      note, with the total word count of the merged file.
- [ ] **Deviations disclosed**: any compression, grouping, or summarized
      passage (and why) is listed for the reader — nothing is silently
      shortened. Where a choice matters (e.g. a passage rendered as a
      summary vs. in full), ask the reader and follow their answer.
- [ ] The merged file's first line is the book title as a single `# `
      heading (the builder drops it — the cover carries the title), and
      section headings are `## ` lines whose exact text the build config's
      TOC and image placements will match against.

Only after this checklist passes does the build stage (README.md, steps
4–7) begin.
