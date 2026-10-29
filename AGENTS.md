# AGENTS.md

## What this repo is

- A Command Code Skill, not a library: `SKILL.md` is the workflow contract, `README.md` the
  overview, and `references/workflow.md` the deep heuristics. The only executables are the two
  standalone scripts in `scripts/`; there is no package manifest.
- No test suite, linter, build, CI, or fixtures. `python3 -m py_compile scripts/*.py` only catches
  syntax errors; real verification means running the pipeline on a PDF and inspecting the output.

## Setup

- Dependencies are not vendored: `python3 -m pip install pymupdf fonttools otf2ttf`, with
  `pyftsubset` and `otf2ttf` on `PATH`.
- Needs Noto Serif CJK SC Regular + Bold OTF in `fonts/` next to the source PDF, or pass
  `--fonts DIR`. A missing font or glyph exits hard; there is no fallback font.
- Source PDFs, `build/`, and generated PDFs are not tracked. `build/` is the font-subset cache and
  is safe to delete.

## Pipeline

```bash
python3 scripts/pdf_translate.py diagnose paper.pdf
python3 scripts/pdf_translate.py extract paper.pdf --compact --out work/compact.json
# author work/translations.json — compact.json is only a manifest
python3 scripts/pdf_translate.py render paper.pdf work/translations.json \
    --out paper-zh.pdf --fonts ./fonts --work ./build --quiet
python3 scripts/make_side_by_side.py paper.pdf paper-zh.pdf --out paper-dual.pdf
```

- `extract` output cannot be passed to `render`. Author `translations.json` per `SKILL.md`.
  `--compact` groups translatable blocks by page with no bboxes and filters kept regions; the
  full `blocks.json` is still available without the flag.
- `suggested_layout` is not directly compatible: `fig_regions` is emitted as a list of image
  bboxes per page, but `render` expects one `[y_max]` or `[x0,x1,y0,y1]` per page (note the
  coordinate order). Convert it manually; `table_bands`, `references_pages`, and `title_ids` can be
  copied as-is, though `references_pages` only covers the page with the `References` heading —
  extend it for multi-page bibliographies.
- `render` ignores the `hint` in `blocks.json`. It reclassifies pages from `layout` regexes
  (`caption_regex`, `heading_regex`, ...) and `layout` maps. The hints only tell you which blocks
  are worth translating; `extract` classifies with empty defaults, so it never emits the documented
  `table`, `reference`, or `title` hints.
- Block keys use `p<page0>:b<n>` (0-based page), while every page map in `layout`
  (`fig_regions`, `table_bands`, `references_pages`) is 1-based.
- Every body/heading/title/caption block needs an entry in `blocks`; missing keys print
  `!! 缺少译文`. Formulas, table bands, figure regions, and reference pages are kept
  automatically. `affil_ids` require `affiliations`; figure labels require `figure_labels`.
- `et_al_pages` defaults to `"auto"`: after rendering, residual `et al.` on non-reference pages
  (typically kept tables/figures) is rewritten to `等人` in place. An explicit 1-based page list
  narrows it; `null`/`[]` disables it. References pages are always skipped.
- `render --quiet` keeps only `!!` lines and `问题页数`; use it by default to keep the context
  small. Drop it to see `diagnose`, font logs, per-page counts, and the fill-ratio table.
- Figure labels absent from `figure_labels` are silently left in the source language — the
  acceptance report does not flag them. Both `figure_labels` and `affiliations` are keyed by exact
  source line text, not by block key.
- `widen` only extends a block's right edge (`x1`); it does not change font size. The renderer
  already shrinks font and adjusts line height to fit; shorten text for `!! 溢出`.
- Verify with the Chinese acceptance report: `问题页数：0`, no `!! 缺少译文` / `!! 溢出` /
  `!! 标题放不下`, then render 3–5 pages to PNG and inspect them. `make_side_by_side` warns about
  page-count mismatch but still processes `min(page_count)` pages.

## Editing notes

- `DEFAULTS` in `scripts/pdf_translate.py` is the source of truth for params/layout. The README
  JSON snippet is partial: it omits `min_head_size`, `caption_regex`, `heading_regex`,
  `abstract_headings`, and `keywords_prefixes`.
- Caption classification runs before formula detection, and the default `caption_regex` requires
  a caption number plus `.`/`:` (e.g. `Figure 3.`), so prose such as “Table 11 compares …” is not
  mistaken for a caption.
- Keep the token-efficient path intact: `--compact` must stay a translation manifest (never render
  input), `--quiet` must keep every `!!` line and `问题页数`, and `et_al_pages: "auto"` must skip
  `references_pages`.
- Keep JSON keys and user-facing messages stable. The Chinese acceptance strings are referenced
  verbatim by the docs and by verification instructions; don't translate or reword them.
- `align_blocks()` exists but is not wired into any CLI command; `render` only rebuilds
  single-language sources. Don't assume an alternating bilingual PDF is paired automatically.
- Font handling is deliberate: subset with `pyftsubset`, convert CFF→TTF with `otf2ttf`, then strip
  GSUB `locl`. Do not replace with `doc.subset_fonts()` or embed full OTFs (wrong GIDs during
  MuPDF rendering, ~10× file size).
- During redaction, images and vector graphics must be preserved (`PDF_REDACT_IMAGE_NONE`,
  `PDF_REDACT_LINE_ART_NONE`); only text is removed.
- Chinese is hard-coded via `LANG_PUNCT` and `clean_citations()`; other target languages require
  editing both (see `SKILL.md` §5).
