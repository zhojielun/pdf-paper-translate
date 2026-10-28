---
name: pdf-paper-translate
description: Translate a PDF (academic paper or similar) into another language while preserving the original page layout — redact each translated text block and re-typeset the translation into the same bbox, keeping figures, math, tables and references intact. Also emits a left-original / right-translation side-by-side PDF. Use when the user asks to translate a PDF, make a bilingual/dual PDF, or "翻译这个 PDF / 对照版".
argument-hint: "<src.pdf> [--fonts DIR] [--lang zh]"
metadata:
  version: "1.0"
  pipeline: "pymupdf + fontTools/pyftsubset + otf2ttf"
---

# PDF Paper Translate

Rebuild a translated PDF **on the original as canvas**: keep the source page geometry and every
non-text object (figures, formulas, tables, references) exactly as-is, redact only the text
blocks you translated, and re-typeset the translation into each block's own bbox on the same
line grid. Then optionally stitch a side-by-side bilingual PDF.

The work is split into two scripts plus one config file you author:

- `scripts/pdf_translate.py` — `diagnose` / `extract` / `render` (the renderer).
- `scripts/make_side_by_side.py` — left-original / right-translation PDF.
- `translations.json` — your translations + layout hints (schema below).

Default target language is **Chinese**. For other targets, adjust `LANG_PUNCT` and the
citation regexes in `pdf_translate.py` (see "Other languages").

## 1. Setup (once per machine)

    python3 -m pip install pymupdf fonttools otf2ttf

Fonts — the pipeline needs **Noto Serif CJK SC** (Regular + Bold) as OTF, and the CLI tools
`pyftsubset` and `otf2ttf` on `PATH` (both ship with the `fonttools` / `otf2ttf` packages).
Put the OTFs in a `fonts/` dir next to the source PDF (or pass `--fonts DIR`):

    fonts/NotoSerifCJKsc-Regular.otf
    fonts/NotoSerifCJKsc-Bold.otf

The scripts subset the OTFs to just the glyphs actually drawn, then convert CFF→TTF before
embedding. Do **not** embed the full OTF (bloats the file ~10×) and do **not** rely on
`doc.subset_fonts()` for CJK (it no-ops on CFF/CID and mupdf then renders wrong GIDs).

## 2. Workflow

### Step 1 — diagnose the source

    python3 scripts/pdf_translate.py diagnose "paper.pdf"

Read the per-page output: images/block counts and whether pages alternate source/translation
(a pre-existing bilingual PDF) or are single-language (the usual case). Single-language →
translate 1:1 page by page, block coordinates come straight from the source.

### Step 2 — extract the translatable blocks

    python3 scripts/pdf_translate.py extract "paper.pdf" --out work/blocks.json

`blocks.json` lists every text block with `key` (`p<page0>:b<n>`), `page`, `bbox`, `size`,
`bold`, a coarse `hint` (`body` / `heading` / `title` / `caption` / `formula` / `table` /
`figure` / `reference`), and the de-hyphenated `text`. It also proposes `fig_regions`,
`table_bands`, `references_pages`, `title_ids`, and a list of `figure_label_candidates`
(text lines sitting inside figures).

### Step 3 — translate into `translations.json`

Translate every block whose `hint` is `body` / `heading` / `title` / `caption`. **Do not**
translate `formula`, `table`, `figure` (as a block), or `reference` blocks — those stay as the
original. For figure interiors, translate only the individual labels you collected in
`figure_label_candidates` into `figure_labels`. Translate author affiliations into
`affiliations`. Use the schema below.

Translation rules the renderer assumes:

- **Translate naturally, keep citations inline.** Do not hand-clean `[12]`, `(Smith et al.,
  2020)`, etc. — `render` runs `clean_citations()` on every block automatically (NFC → replace
  "Author et al. (year)" with a placeholder → drop dangling parenthetical years → tidy
  fragments). Just produce fluent target-language prose.
- **Run-in bold headings**: if the source block's first line is bold and ends in a period, wrap
  the translated lead-in, up to the first period, in `<b>…</b>`.
- **Math in prose**: keep sub/superscripts as `<sub>` / `<sup>`; e.g. `H<sup>T</sup> ∈
  R<sup>B×D×T×d<sub>T</sub></sup>`.
- **Multi-paragraph blocks**: separate paragraphs with `\n` inside the string; each becomes a
  `<p>`.
- **Numbered headings / captions**: translate the text but keep the number (`2.1. 方法`,
  `图 1. …`, `表 2. …`). The renderer keeps the label and its following space together.

Schema:

```json
{
  "params": { "body_ratio": 0.98, "l0": 1.28, "lmax": 1.72, "indent_em": 2.0,
              "head_size": 9.6, "title_size": 16.5, "hmin": 10.5, "alpha": 0.35 },
  "layout": {
    "keep_ids": [], "no_indent": [], "widen": {}, "affil_ids": [],
    "table_bands": {}, "fig_regions": {}, "references_pages": [],
    "references_heading_ids": [], "title_ids": [],
    "caption_regex": "^(Fig\\.|Table)\\s", "heading_regex": "^\\d+(?:\\.\\d+)*\\.?\\s+\\S",
    "abstract_headings": ["ABSTRACT"], "keywords_prefixes": ["Index Terms"]
  },
  "blocks":        { "p0:b0": "xx", "p0:b1": "xx" },
  "figure_labels": { "xx": "xx" },
  "affiliations":  { "xx": "xx" }
}
```

### Step 4 — render the translation PDF

    python3 scripts/pdf_translate.py render "paper.pdf" work/translations.json \
        --out "paper-zh.pdf" --fonts ./fonts --work ./build

It writes `paper-zh.pdf` (same page count/size as the source), then prints an acceptance
report: per-page fonts, leftover `et al` count, and any missing-glyph boxes. **Fix every
`!! 缺少译文` (missing translation) and `!! 溢出` (overflow) line before moving on.**

### Step 5 — build the side-by-side PDF

    python3 scripts/make_side_by_side.py "paper.pdf" "paper-zh.pdf" --out "paper-dual.pdf"

Pages are `(2W+G)×H` with a thin gray divider at `x = W + G/2` (`--gutter` sets `G`, default
24pt).

### Step 6 — verify visually

Render a few pages to PNG and inspect them (title sizing, figure-label placement, tables still
intact, heads not colliding with the rule below, no tofu boxes):

    python3 - <<'EOF'
    import pymupdf
    d = pymupdf.open("paper-zh.pdf")
    for i in (0, 1, 2, 3):
        d[i].get_pixmap(dpi=110).save(f"/tmp/zh{i+1}.png")
    EOF

Then open the PNGs. Re-check the acceptance report for `et al` leaks and tofu.

## 3. Layout fields — how to fill them

Most fields have safe defaults; only fill what the paper needs. Full guidance and how to derive
each one is in `references/workflow.md`. Quick reference:

- `fig_regions` — `{"<page1based>": [y_max]}` (full-width figure at the top, keep text with
  `bbox.y1 ≤ y_max`) or `{"<page>": [x0,x1,y0,y1]}` (figure in one column; matches a block whose
  center-x falls in `[x0,x1]` and whose `y0` is in `[y0,y1]`). Blocks inside are treated as
  figure labels (translated line-by-line) instead of body.
- `table_bands` — `{"<page>": [[y0,y1], …]}`; text blocks whose center-y falls in a band are
  left as the original table. Only the `Table N.` caption gets translated.
- `keep_ids` — block keys to leave untouched (author line, arXiv/watermark stamps, wide
  display-formula blocks that pymupdf splits oddly).
- `no_indent` — body blocks that continue a previous block/page (no first-line indent).
- `widen` — `{"<key>": new_x1}` to widen a short single-line block whose column is empty to the
  right.
- `affil_ids` — author-affiliation block keys; replaced line-by-line, horizontally centered.
- `references_pages` — 1-based pages kept verbatim (the bibliography).
- `title_ids` / `references_heading_ids` — keys rendered as centered headings.
- `params` — typography knobs; leave at defaults unless lines overflow or look too loose.

## 4. Troubleshooting

- **`字体缺字（会渲染成方块）`** — a character you drew is missing from the OTF. Install a font
  that covers it (Noto Serif CJK SC covers common CJK + Latin) or replace the character.
- **`!! 溢出 … spare=…`** — the translation is too long for the block. Shorten it, or lower that
  block's `size` by putting it in `widen`, or the loop already shrinks font automatically —
  check `dump_stats` fill ratios; some over-fill is fine.
- **`!! 缺少译文`** — you did not provide a translation for that block key. Add it (or, if it is
  a formula/table that should be kept, add its key to `keep_ids`).
- **`!! 标题放不下`** — a heading string is too long/short for its rect; shorten the heading or
  the renderer will keep shrinking it.
- **Warning about block count mismatch** only appears for alternating bilingual sources.

## 5. Other languages

The pipeline is Chinese-tuned. To target another language: edit `LANG_PUNCT` (the punctuation
subset added to the font subset), the `clean_citations()` replacement string, and the guillemet
/ bracket classes in `tighten()`. Everything else (bbox re-typesetting, line-fill, subsets) is
language-agnostic.

## Files

- `scripts/pdf_translate.py` — diagnose / extract / render.
- `scripts/make_side_by_side.py` — bilingual side-by-side builder.
- `references/workflow.md` — deeper heuristics and rationale.

> Skills are instructions plus executable code — review the scripts before trusting them.
