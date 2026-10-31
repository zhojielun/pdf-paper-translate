---
description: Translate a PDF with the existing layout-preserving pipeline
---
Translate the PDF at `$ARGUMENTS` with this repo's existing pipeline. Do not run `/init`;
`AGENTS.md` is already current.

- Read `AGENTS.md` once. Read `SKILL.md` only for the `translations.json` schema or a specific
  error; do not re-read `scripts/pdf_translate.py` unless classification/layout is actually wrong.
- Work under `work/<pdf-stem>/`: run `diagnose` once, then `extract --compact`.
- Build `translations.json` from the compact manifest. Translate body/heading/title/caption only;
  keep formulas, table bodies, figure interiors, and references. Adapt `suggested_layout`:
  convert `fig_regions` to one `[y_max]` or `[x0,x1,y0,y1]` per page, trim `table_bands`, and
  extend `references_pages` across every bibliography page.
- Fonts live in `fonts/` next to the PDF or `--fonts DIR`; ask for the directory if missing.
- Render with `--quiet`, fix every `!!` line, and require `问题页数：0`. Then render 3–5 pages to
  PNG and inspect them. Build the side-by-side PDF with `make_side_by_side.py`.
- For a long paper, translate page-group by page-group into one `translations.json` and render once.
