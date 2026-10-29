# Workflow & heuristics — deep reference

Read this when the quick steps in `SKILL.md` are not enough: how to derive the layout fields,
why the renderer does what it does, and how to fix the common failures.

## The core idea

The original PDF is a **canvas**, not something to reflow. For each page:

1. Classify each text block (`page.get_text("dict")`).
2. For blocks you will translate, add a redaction annotation over the block's exact bbox and
   `apply_redactions(images=NONE, graphics=NONE, text=REMOVE)` — this deletes only text, leaving
   figures, vector art and tables untouched.
3. Draw the translation back into that same bbox with `insert_htmlbox`, then nudge the text
   metrics (font size, line height) so the block height is filled without stretching
   inter-character spacing.

Headings can't go through `insert_htmlbox` (it silently returns −1 in a tight rect), so they use
`insert_textbox` at a fixed size. Figure labels are redrawn line-by-line at the original baseline
and centered, sized to `min(original_size, line_width / glyph_count)`. Tables, displayed
formulas, and references are never touched.

## Token-efficient runs

The expensive part is context, not rendering:

- **`extract --compact`** writes only translatable blocks, grouped by page, without bboxes. Read
  that instead of the full manifest. The compact file is also *not* render input: it is a
  manifest for translation, and `suggested_layout` still needs its `fig_regions` converted.
- **`render --quiet`** keeps `!!` lines and `问题页数`, drops per-page counts, font logs,
  `diagnose` details and the fill-ratio table. Use it by default.
- **`et_al_pages: "auto"`** handles stray citations in kept tables/figures after rendering, so
  you do not need a per-page patch pass.
- Work in page groups and merge translations once. Re-render once after fixing every `!!` in the
  current report; visual checking is a final step, not a loop.

## Deriving layout fields

### fig_regions

`extract` proposes regions by merging the page's image blocks (pymupdf returns `type == 1`
blocks; figures with embedded raster art show up there, often as many fragments that must be
union-merged with a small pad). Two accepted shapes per page:

- `[y_max]` — everything with `bbox.y1 <= y_max` is figure territory (use for a full-width
  figure across the top of a page).
- `[x0, x1, y0, y1]` — a figure confined to one column: a block is inside if its horizontal
  center is in `[x0,x1]` **and** its `bbox.y0` is in `[y0,y1]`.

Tighten/loosen so that exactly the figure's own text lands inside and the caption/prose below
does not. The `figure_label_candidates` list shows which lines you captured — translate those
into `figure_labels`.

### table_bands

`extract` proposes a band per `Table N.` caption: from just below the caption to the next
caption (or the page bottom). A text block whose **center-y** falls in the band is kept as the
original table. Verify visually — a ruled table is often a set of small text blocks plus vector
rules; the band should cover the rule area, not the caption. Only the caption is translated, so
start the band below it; if the caption must be translated, either move the band or add the
caption block to `blocks` explicitly.

### et_al_pages

After the main render, any `et al.` still visible must either be inside a translated block
(cleaned by `clean_citations`) or inside a kept table/figure. `et_al_pages: "auto"` (default)
searches each non-reference page for the phrase and rewrites it to `等人` at the original
baseline, preserving the span's size, color and rotation. Use an explicit 1-based list to limit
it, or `null` / `[]` to disable. Reference pages are always skipped.

### keep_ids

Blocks to leave completely alone:

- the author line and footnote markers,
- rotated arXiv / conference watermark stamps,
- wide displayed formulas that pymupdf splits into several blocks (keeping them avoids erasing
  part of an equation),
- anything the source already shows in the target language.

`extract` won't guess these reliably — scan `blocks.json` for keys whose `hint` is `formula` or
whose text is a watermark, and add them.

### no_indent

Body blocks that are a continuation of a paragraph started in a previous block or on the
previous page (the first line is not a true paragraph start). Without this they'd get a spurious
first-line indent. `extract` can't detect these; read the source and list them.

### widen

A single-line block (e.g. a short definition, `其中 ℓ 为 MSE。`) whose column has empty space to
the right. Set `widen[key]` to the column's right edge so the line isn't needlessly cramped.

## Typography parameters

Defaults reproduce a standard two-column letter paper (612×792pt, columns at x≈54–298 and
x≈315–559). Meaning:

- `body_ratio` (0.98) — body font size = original western size × this (CJK reads equal at a
  slightly smaller nominal size).
- `l0` / `lmax` (1.28 / 1.72) — start line-height multiple, and the ceiling. The renderer lays
  out at `l0`, reads the returned `spare`, back-infers the line count, and grows line-height to
  fill the block — never above `lmax` (unfilled space is left blank rather than stretched).
- `indent_em` (2.0) — first-line indent, in em.
- `head_size` / `title_size` (9.6 / 16.5) — heading and paper-title font sizes.
- `hmin` (10.5) — a centered-heading heuristic threshold (size ≥ this and not starting with a
  digit ⇒ centered).
- `alpha` (0.35) — height weight in the DP block-alignment cost (only used for bilingual
  sources).

Tune only if lines overflow or gaps look wrong.

## Two-column geometry

`col_bounds()` returns the left/right column x-range scaled from a 612pt baseline, so it works
for the common letter/A4 two-column layout. For single-column or unusual layouts, either accept
the default (headings will center on the full width) or add a per-document override in the
script.

## Verification checklist

1. `render --quiet` prints `问题页数：0` — no leftover `et al` (except the references page) and no
   tofu. Drop `--quiet` only if you need the fill-ratio table.
2. No `!! 缺少译文` / `!! 溢出` / `!! 标题放不下` lines.
3. Render 3–5 pages to PNG and eyeball: title size, headings not colliding with rules, figure
   labels legible and on-baseline, tables intact, captions above their table.
4. The dual PDF: both halves aligned, divider centered, nothing clipped.

## Failure modes and fixes

| Symptom | Cause | Fix |
|---|---|---|
| `字体缺字（会渲染成方块）` | glyph absent from the OTF | install a fuller font, or replace the char |
| `!! 溢出 … spare=` | translation too long for bbox | shorten text / add to `widen`; small overflow is OK |
| `!! 缺少译文 <key>` | no entry in `blocks` | add it, or add `<key>` to `keep_ids` |
| `!! 标题放不下` | heading text too wide for column | shorten heading |
| garbled digits in copy/paste | GSUB `locl` localization | already handled — `_strip_locl()` removes it |
| file size balloons | full OTF embedded | ensure the subset step ran (delete `build/` to force) |

## Fonts: why subset → CFF→TTF

Noto Serif CJK SC is a CFF/OTF font. Embedding the whole thing adds ~10MB+ per style.
`doc.subset_fonts()` is a no-op on CFF/CID fonts, and mupdf renders a naively subset CFF with
the wrong GIDs (garbage glyphs). The reliable path — used here — is `pyftsubset` to a subset OTF,
`otf2ttf` to convert CFF outlines to TrueType (glyf), strip the `locl` feature, then embed the
TTF. The subset charset is exactly the set of characters that will be drawn.
