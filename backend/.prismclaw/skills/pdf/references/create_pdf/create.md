# Route A: CREATE

Generate a PDF from scratch: the agent writes `content.json`, and the renderer compiles it to PDF via Typst. Formulas, charts, flowcharts, and code blocks are all rendered natively by Typst.

**Core pipeline**: `Init Project → Source Document (optional) → Read Design System → Design Tokens → Write content.json → Validate → Render PDF`

## Workflow

### Step 1: Initialize Project

**GATE**: The user has requested a new PDF; the document topic and type are known.

```bash
python3 scripts/create_pdf/project_manager.py init <project_name> --type <type> --dir .prismclaw
```

> The project folder is created under `.prismclaw/` in the current working directory, named `pdf-{project_name}-{YYYYMMDD}-{uuid8}`, with a standard structure containing `sources/`, `assets/images/`, `build/`, and `exports/`.

**Checkpoint — Project workspace created. Proceed to Step 2.**

---

### Step 2: Source Document Processing (optional)

**GATE**: Step 1 complete; the user provided reference documents, source PDFs, notes, images, or data files.

```bash
python3 scripts/create_pdf/project_manager.py import-sources <project_path> source.pdf notes.md image.png
```

| Format | Handling |
|------|------|
| `.md` / `.txt` | Copied into `sources/`, merged into `source_document.md` |
| `.pdf` | Copied into `sources/`, converted to Markdown, images extracted to `assets/images/`, merged into `source_document.md` |
| `.docx` / `.doc` / `.odt` / `.rtf` / `.epub` / `.html` / `.ipynb` | Copied into `sources/`, converted to Markdown via doc_to_md, images extracted to `assets/images/`, merged into `source_document.md` |
| `.pptx` / `.pptm` / `.ppsx` | Copied into `sources/`, converted to Markdown via ppt_to_md, images extracted to `assets/images/`, merged into `source_document.md` |
| images | Copied into `assets/images/`, represented as Markdown image references |
| `.json` | Copied into `sources/`, represented as a fenced JSON section |

> Skip this step only when the user provided no materials. After running it, read `<project_path>/source_document.md` before writing `content.json`, and use `<project_path>/source_manifest.json` to check provenance and warnings.

**Checkpoint — Source Document generated. Proceed to Step 3.**

---

### Step 3: Read Design System

**GATE**: The project directory exists (Step 2 optional).

Read through [design.md](design.md) — it is the basis for all subsequent authoring decisions. This step is mandatory and must not be skipped. Focus on:
- **Palette templates / Mood → base palette / color-selection rules** — for choosing `--type` and `--accent` in Step 4
- **Block types, inline markup, formula/chart/flowchart rules** — for writing content.json in Step 5
- **Heading numbering, Chinese-quote prohibition** — hard constraints; violations cause non-compliant layout or JSON parse failures

**Checkpoint — design.md read. Proceed to Step 4.**

---

### Step 4: Generate Design Tokens

**GATE**: Step 3 complete; design.md has been read.

```bash
python3 scripts/create_pdf/palette.py \
  --title <title> --type <type> \
  --out <project_path>/tokens.json
```

| Parameter | Values | Default | Description |
|------|------|------|------|
| `--title` | any string | required | Document title (appears in the header) |
| `--type` | palette template name | required | Controls palette selection; see "Palette templates" in [design.md](design.md) for options |
| `--accent` | `#HEX` | derived from type | Override the accent color |
| `--font` | `fangsong`, `serif` | `fangsong` | Zhuque Fangsong or Source Han Serif (only effective with `--lang zh`) |
| `--lang` | `zh`, `en` | `zh` | `zh` uses CJK fonts; `en` uses Helvetica/Times New Roman |
| `--columns` | `1`, `2` | `1` | Use 2 only when the user explicitly requests it |
| `--no-cover` | flag | off | Omit the cover page; use for "no cover", "blank file/blank pdf", or short notes/appendices/blank documents |
| `--no-header` | flag | off | Omit header/footer; use for blank PDFs or when the user says "no header" |
| `--layout` | template name | `default` | Page layout template; see "Layout templates" in [design.md](design.md) for options |
| `--date` | YYYY-MM-DD | today | Document date |
| `--meta` | path to JSON file | — | Alternative: provide title/type/author/date via a JSON file |

> **Color selection**: choose colors from the semantic context rather than a generic default; prefer low-saturation, restrained tones, avoid vivid primary colors, and when in doubt go darker. See [design.md](design.md) for the full palette logic and the by-scenario color table.

**Checkpoint — `tokens.json` generated. Proceed to Step 5.**

---

### Step 5: Write content.json

**GATE**: Step 4 complete (and Step 2 too if there were materials); the project structure is ready.

Write `<project_path>/content.json` — a JSON array of content blocks. If Step 2 produced `source_document.md`, treat it as the authoritative source context, and keep referenced images as `image`/`figure` blocks pointing to project-local paths.

**Full block types, fields, inline markup, formula/chart/flowchart rules, heading numbering, and the Chinese-quote prohibition are all in [design.md](design.md).** You must follow every rule in that document while writing.

**Rendering notes:**
- Images are staged automatically — `image`/`figure` may use an absolute path or a path relative to `<project_path>`; the renderer snapshots the file into `<project_path>/assets/images/` and writes a project-relative path into `build/body.typ`.
- **Never** insert a `pagebreak` before or after `h1`/`h2` headings — the renderer automatically adds `section_gap` for headings. Use `pagebreak` only when the user explicitly requests a forced page break, or when a standalone cover/appendix page is genuinely needed.

**Checkpoint — content.json written. Proceed to Step 6.**

---

### Step 6: Validate

**GATE**: Step 5 complete; content.json exists in `<project_path>`.

```bash
python3 scripts/create_pdf/project_manager.py validate <project_path>
```

Validates: legal JSON structure (array of objects), known block types, no Chinese quotes, and that each block has its required fields.

> If validation fails, fix content.json, re-validate, then continue.

**Checkpoint — Validation passed. Proceed to Step 7.**

---

### Step 7: Render PDF

**GATE**: Step 6 complete; validation passed.

```bash
python3 scripts/create_pdf/render_body_typst_native.py \
  --tokens "<project_path>/tokens.json" \
  --content "<project_path>/content.json" \
  --out output.pdf
```

Output path: use the user-specified location, or the current working directory with a descriptive filename. If the output path is outside `<project_path>`, the renderer first compiles inside `<project_path>/exports/` (to satisfy Typst's project-root rule), then copies the final PDF to the target location. The renderer writes the generated Typst source to `<project_path>/build/body.typ` and compiles with `<project_path>` as the Typst root. Do not move images manually during rendering — let the renderer stage them.

**Checkpoint — PDF rendered. Pipeline complete.**

---

## Environment

All dependencies are installed automatically by the scripts; no manual setup is needed.

| Tool | Required by | Install |
|------|---------|------|
| Python 3.9+ | all scripts | system built-in |
| `typst` | render_body_typst_native.py | auto-downloaded |

---

## Reference Resources

| Resource | Path |
|------|------|
| Visual system & block types | [design.md](design.md) |
