# AGENTS.md

## Fast path

- `/init` is not needed after this file exists; OpenCode loads `AGENTS.md` automatically. Use
  `/translate-pdf <pdf>` (`.opencode/commands/translate-pdf.md`) to jump straight to the pipeline.
- For a normal translation, do not read `scripts/pdf_translate.py` or `references/workflow.md`;
  read them only when classification/layout is actually wrong.

## What this repo is

- A Command Code Skill, not a library: `SKILL.md` is the workflow contract, `README.md` the
  overview, and `references/workflow.md` the deep heuristics. The only executables are the two
  standalone scripts in `scripts/`; there is no package manifest.
- No test suite, linter, build, CI, or fixtures. `python3 -m py_compile scripts/*.py` only catches
  syntax errors; real verification means running the pipeline on a PDF and inspecting the output.
  That command can rewrite the tracked `scripts/__pycache__/*.pyc`; run
  `git checkout -- scripts/__pycache__` before committing.
- Docs drift: README's long "Prompt" mentions a `rebuild_zh.py` that does not exist (the renderer
  is `pdf_translate.py render`), and SKILL's front matter advertises a `--lang` flag that the CLI
  does not have.

## Setup

- Dependencies are not vendored: `python3 -m pip install pymupdf fonttools otf2ttf`, with
  `pyftsubset` and `otf2ttf` on `PATH`. `pdf_translate.py` imports `pymupdf` even for `--help`.
- Needs Noto Serif CJK SC Regular + Bold OTF in `fonts/` next to the source PDF, or pass
  `--fonts DIR`. Filenames are hard-coded as `NotoSerifCJKsc-Regular.otf` /
  `NotoSerifCJKsc-Bold.otf`; other fonts must be exposed under those names or `font_paths()` edited.
  A missing font file or a glyph used in `blocks` / `figure_labels` / `affiliations` exits hard
  before writing output; the script selects no fallback font.
- For `render`, `--work` defaults to `build/` next to `--out` (or next to the source when `--out`
  is omitted), not necessarily the cwd. `build/` is the font-subset cache (invalidated by
  `chars.txt`/OTF mtime) and is safe to delete.
- There is no `.gitignore`: keep `build/`, source PDFs, and generated PDFs out of commits.

## Pipeline

```bash
python3 scripts/pdf_translate.py diagnose paper.pdf
python3 scripts/pdf_translate.py extract paper.pdf --compact --out work/compact.json
# author work/translations.json — compact.json is only a manifest
python3 scripts/pdf_translate.py render paper.pdf work/translations.json \
    --out paper-zh.pdf --fonts ./fonts --work ./build --quiet
python3 scripts/make_side_by_side.py paper.pdf paper-zh.pdf --out paper-dual.pdf
```

- `extract` output is a translation manifest, never `render` input. `--compact` emits
  `{pages: {"<page1based>": [{key,size,bold,hint,text}]}, figure_labels: {"<page>": [{text,size}]},
  suggested_layout}`; the full form adds bboxes and `figure_label_candidates`.
- Compact filtering is only as good as `suggested_layout`: a table/figure that extract failed to
  identify stays in `pages` as ordinary body text. Check the suggested maps before translating, or
  use the full manifest.
- `suggested_layout` needs manual adaptation. `fig_regions` is a list of image bboxes per page,
  each `[x0,y0,x1,y1]`, while `render` accepts one region per page as `[y_max]` or
  `[x0,x1,y0,y1]` (swap the middle pair); merge multiple bboxes or pick the relevant one.
  `[y_max]` keeps blocks whose bottom is above it; the 4-value form matches block center-x and
  top-y. Suggestions come only from raster images, so vector-only figures need manual regions and
  produce no `figure_label_candidates`.
- `references_pages` and `title_ids` copy as-is, but `references_pages` only covers the page holding
  the `References` heading — extend it for multi-page bibliographies. `suggested_layout` never
  includes `keep_ids`, `no_indent`, `widen`, `affil_ids`, or `references_heading_ids`; add those
  manually.
- Do not copy `table_bands` blindly. extract runs each band from below a `Table N.` caption to the
  next caption or near the page bottom, so it can swallow following headings/body; trim each band
  to the actual table. A block is inside a band when its center-y falls in it, and swallowed blocks
  render untranslated with no `!!` warning. Suggestions only exist for `Table`/`表` followed by a
  digit, so other caption styles need manual bands.
- `render` never reads the extract manifest. It reclassifies the source PDF using the `layout`
  regexes/maps, so `hint` is advisory only: a block hinted `title` still renders as body unless its
  key is in `layout.title_ids`, and a block hinted `heading` must also match the renderer's rules.
- Block keys use `p<page0>:b<n>` (0-based page). All `layout` page references
  (`fig_regions`, `table_bands`, `references_pages`, `et_al_pages`) and compact page keys are
  1-based.
- Every body/heading/title/caption block needs an entry in `blocks`; missing keys print
  `!! 缺少译文` and the source text is left in place. `keep_ids`, table bands, and reference pages
  are not redacted; reference pages stay verbatim except `references_heading_ids`.
- `affil_ids` blocks are always redacted and need `affiliations` entries for every line; a missing
  line is erased silently (no `!!`). Figure labels are the opposite: only lines present in
  `figure_labels` and inside a `fig_regions` area are redacted/redrawn; other lines stay in the
  source language and are not flagged.
- `figure_labels` and `affiliations` are keyed by exact source line text, not block key.
  `--compact`'s `figure_labels` is a per-page candidate list; flatten it to that schema.
- Formula detection is heuristic (indented/short, math font or `=`); wide or split display formulas
  can surface as body and need `keep_ids`. `keep_ids` neither translates nor redacts a block.
- `et_al_pages` defaults to `"auto"`: after rendering, residual `et al.` on non-reference pages
  (typically kept tables/figures) is rewritten to `等人` in place. An explicit 1-based page list
  narrows it; `null`/`[]` disables it. References pages are always skipped.
- The font subset is built from raw translation text before `clean_citations()` and
  `replace_et_al()` inject `该文献` / `等人`. If those characters are absent from translations,
  body/caption falls back to another font (Droid Sans Fallback), while direct `insert_text` /
  `insert_textbox` draws (headings, et-al replacement) can render blank boxes and `问题页数` stays
  0. Put `"params": {"extra_chars": "该文献等人"}` in `translations.json` or include them in a
  translation.
- `render --quiet` suppresses per-page counts, font logs, `diagnose` details, and the fill-ratio
  table; it keeps every `!!` line, the `已写出` line, and `问题页数`. Use it by default. Drop it to
  debug layout.
- `widen` sets an absolute `x1` for body/caption blocks only; it never changes font size. The
  renderer already shrinks type and adjusts line height, so shorten text for `!! 溢出`; widening
  only gives the line more horizontal room.
- Verify with the acceptance report: `问题页数：0`, no `!! 缺少译文` / `!! 溢出` /
  `!! 标题放不下`. `问题页数` counts only residual `et al` and tofu, not the other `!!` warnings.
  Then render 3–5 pages to PNG and inspect titles, figure labels (they shrink to line width), tables,
  and tofu. `make_side_by_side` warns on page-count/size mismatch but still writes
  `min(page_count)` pages on the first page's canvas.

## Editing notes

- `DEFAULTS` in `scripts/pdf_translate.py` is the source of truth for params/layout. Docs drift:
  the README snippet omits `min_head_size`, `caption_regex`, `heading_regex`, `abstract_headings`,
  and `keywords_prefixes`; `hmin` is documented but never read (heading centering is by bbox
  center); `min_head_size` is the body shrink floor, not a heading threshold. Body/caption size =
  source size × `body_ratio`; heading/title sizes are fixed by `head_size`/`title_size`.
- Caption classification runs before formula detection. The default `caption_regex` is
  case-sensitive and only matches `Fig.`/`Figure`/`Table` + number + `.`/`:` + whitespace, so
  prose such as “Table 11 compares …” is not a caption; set `layout.caption_regex` for `TABLE I`,
  `图 1`, etc.
- Full `extract` never emits `title` / `table` / `reference` hints because it runs with default
  empty layout maps; `--compact` relabels the suggested title block as `title`. The suggestion is
  only the largest wide page-1 block, so add subtitle/wrapped-title keys manually. `title`
  rendering itself comes only from `layout.title_ids`.
- `heading` needs a single-line block ≤20pt tall, bold/italic, plus a `heading_regex`,
  `abstract_headings`, or `keywords_prefixes` match. Unnumbered headings such as `Introduction`
  fall through to body unless listed exactly in `abstract_headings` (case-sensitive);
  `keywords_prefixes` is a case-sensitive prefix. `affil` is never hinted; add `affil_ids` manually.
- `blocks` values are HTML for body/caption (so `<b>`, `<sub>`, `<sup>`, and `\n` work) but plain
  text for title/heading, `figure_labels`, and `affiliations`; tags there render literally.
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
- Some PDFs contain empty type-0 "ghost" blocks; iterate with `text_blocks(page)`, not raw
  `get_text("dict")["blocks"]`, or `max(spans)` crashes during extract/render.
- Chinese is hard-coded via `LANG_PUNCT`, `clean_citations()`, and `tighten()`; other target
  languages require editing all three (see `SKILL.md` §5).
