# Route D: PARSE

Extract PDF contents into Markdown. Supports automatic scanned-PDF detection and OCR. The same parser is also used internally by CREATE/REFORMAT Source Document processing when PDF files are provided as source materials.

---

## Basic usage

```bash
# Basic conversion — outputs <input>.md alongside the PDF
python3 scripts/parse_pdf/pdf_to_markdown.py input.pdf

# Specify output file
python3 scripts/parse_pdf/pdf_to_markdown.py input.pdf -o output.md

# Convert to Markdown and extract images to a directory
python3 scripts/parse_pdf/pdf_to_markdown.py input.pdf --write-images --images-dir ./images

# Convert only specific pages (0-based)
python3 scripts/parse_pdf/pdf_to_markdown.py input.pdf --pages 0 1 2 5

# Auto-detect scanned PDF and enable OCR when needed
python3 scripts/parse_pdf/pdf_to_markdown.py input.pdf --ocr auto --ocr-language chi_sim+eng

# Force OCR on all pages (for scanned PDFs)
python3 scripts/parse_pdf/pdf_to_markdown.py input.pdf --ocr force --ocr-language chi_sim+eng

# Detect-only mode — report whether PDF is scanned (no conversion)
python3 scripts/parse_pdf/pdf_to_markdown.py input.pdf --detect

# Batch-convert all PDFs in a directory
python3 scripts/parse_pdf/pdf_to_markdown.py ./pdf_folder/
```

---

## As Source Document Converter

When the user wants a *designed* PDF (CREATE/REFORMAT), do NOT use PARSE as the final route. Instead run `project_manager.py import-sources`, which invokes this parser for PDFs and writes normalized `source_document.md` / `source_manifest.json` / extracted images into the project. Authoritative details on Source Document generation live in [create.md](../create_pdf/create.md) (Step 3) and [reformat.md](../reformat_pdf/reformat.md).

---

## Options

| Option                    | Effect |
|---------------------------|---|
| `-o <path>`               | Output file path (default: same name as input, `.md` or `.json`) |
| `--detect`                | Only detect whether the PDF is scanned; print a report without converting |
| `--write-images`          | Extract images to files |
| `--embed-images`          | Embed images as base64 in Markdown (mutually exclusive with `--write-images`) |
| `--images-dir <dir>`      | Image output directory (only with `--write-images`) |
| `--image-format png\|jpg` | Image format (default: `png`) |
| `--dpi <int>`             | Image resolution (default: `150`) |
| `--pages <int ...>`       | Page numbers to convert (0-based) |
| `--page-chunks`           | Output per-page chunks as JSON instead of a single Markdown file |
| `--ocr auto\|no\|force`   | OCR mode: `auto` = detect scanned pages (default), `no` = disabled and OCR only when needed, `force` = OCR all pages |
| `--ocr-language <code>`   | Tesseract language code, e.g. `eng`, `chi_sim`, `chi_sim+eng` (default: `eng`) |
| `--margins <float ...>`   | Crop margins: single value or `left top right bottom` |

---

## Scanned PDF detection

`--ocr auto` inspects each page — if extractable text < 30 chars and the page contains images, it is classified as a scanned page. When ≥ 50% of pages are scanned, OCR is automatically enabled.

---

## OCR dependencies

Auto-installed on Ubuntu/Debian: `tesseract-ocr` + language packs (e.g. `tesseract-ocr-chi-sim`).

On macOS: `brew install tesseract tesseract-lang`.

---

## Exit codes

`0` success · `1` argument error · `2` dependency missing · `3` conversion error
