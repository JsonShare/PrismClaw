# Route E: TOOLKIT

All-in-one PDF manipulation via `scripts/toolkit/pdf_toolkit.py`.

## Merge multiple PDFs

```bash
python3 scripts/toolkit/pdf_toolkit.py merge --inputs a.pdf b.pdf c.pdf --out merged.pdf
```

## Split / extract pages

```bash
# Extract specific pages (1-based)
python3 scripts/toolkit/pdf_toolkit.py split --input doc.pdf --pages 1-3,5,8-10 --out extract.pdf

# Split into chunks of N pages
python3 scripts/toolkit/pdf_toolkit.py split --input doc.pdf --every 5 --out-dir ./chunks/
```

## Rotate pages

```bash
# Rotate all pages 90° clockwise
python3 scripts/toolkit/pdf_toolkit.py rotate --input doc.pdf --angle 90 --out rotated.pdf

# Rotate only specific pages
python3 scripts/toolkit/pdf_toolkit.py rotate --input doc.pdf --angle 180 --pages 2,4-6 --out rotated.pdf
```

Allowed angles: `90` · `180` · `270` (clockwise).

## Add watermark

```bash
# Center mode (default) — single text at page center
python3 scripts/toolkit/pdf_toolkit.py watermark --input doc.pdf --text "CONFIDENTIAL" --out marked.pdf

# Full mode — tiled grid covering the entire page
python3 scripts/toolkit/pdf_toolkit.py watermark --input doc.pdf --text "DRAFT" --mode full --out marked.pdf

# Chinese text watermark (auto-detects CJK, uses SourceHanSerifCN-Bold)
python3 scripts/toolkit/pdf_toolkit.py watermark --input doc.pdf --text "机密文件" --mode full --out marked.pdf

# Custom style
python3 scripts/toolkit/pdf_toolkit.py watermark --input doc.pdf --text "DRAFT" \
  --mode full --font-size 36 --opacity 0.1 --angle 30 --pages 1-3 --out marked.pdf

# PDF stamp watermark (overlay first page of stamp onto every page)
python3 scripts/toolkit/pdf_toolkit.py watermark --input doc.pdf --stamp logo.pdf --out marked.pdf
```

Chinese/CJK text is auto-detected; the script registers `SourceHanSerifCN-Bold` from `scripts/create_pdf/fonts/SourceHanSerifCN/`.

| Option | Default | Effect |
|---|---|---|
| `--text` | — | Watermark text (mutually exclusive with `--stamp`) |
| `--stamp` | — | PDF file whose first page is overlaid as watermark |
| `--mode` | `center` | `center`: single text at page center; `full`: tiled grid across entire page |
| `--font-size` | `48` | Font size for text watermark |
| `--opacity` | `0.15` | Opacity 0.0–1.0 |
| `--angle` | `45` | Rotation angle for text watermark |
| `--pages` | all | Page spec, e.g. `1-3,5` |

## Encrypt PDF

```bash
python3 scripts/toolkit/pdf_toolkit.py encrypt --input doc.pdf --password secret --out locked.pdf

# With separate owner password
python3 scripts/toolkit/pdf_toolkit.py encrypt --input doc.pdf --password user123 \
  --owner-password admin456 --out locked.pdf
```

## Decrypt PDF

```bash
python3 scripts/toolkit/pdf_toolkit.py decrypt --input locked.pdf --password secret --out unlocked.pdf
```

## Images to PDF

Combine multiple images into a single PDF. Each image becomes one page, preserving original dimensions.

```bash
python3 scripts/toolkit/pdf_toolkit.py images2pdf --inputs img1.png img2.jpg photo.webp --out album.pdf
```

Supports png, jpg, webp, bmp, tiff, etc. RGBA images are auto-composited onto white background.

## PDF to Images

Render PDF pages to image files. Two modes:

```bash
# One image per page (default)
python3 scripts/toolkit/pdf_toolkit.py pdf2images --input doc.pdf --out-dir ./pages/

# Only specific pages
python3 scripts/toolkit/pdf_toolkit.py pdf2images --input doc.pdf --pages 1-3,5 --out-dir ./pages/

# Single long image (all pages stitched vertically)
python3 scripts/toolkit/pdf_toolkit.py pdf2images --input doc.pdf --mode long --out long.png

# Custom DPI and format
python3 scripts/toolkit/pdf_toolkit.py pdf2images --input doc.pdf --dpi 300 --format jpg --out-dir ./pages/
```

| Option | Default | Effect |
|---|---|---|
| `--mode` | `pages` | `pages`: one image per page; `long`: single vertically-stitched image |
| `--out-dir` | same as input | Output directory (for `pages` mode) |
| `--out` | — | Output image path (for `long` mode) |
| `--dpi` | `300` | Rendering resolution |
| `--format` | `png` | Image format: `png` or `jpg` |
| `--pages` | all | Page spec, e.g. `1-3,5` |

## Notes

**Page spec format:** `1-3,5,8-10` — 1-based, comma-separated, supports ranges. Used by `split --pages`, `rotate --pages`, `watermark --pages`, `pdf2images --pages`.

**Exit codes:** `0` success · `1` argument error · `2` dependency missing · `3` runtime error
