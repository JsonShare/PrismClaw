# Route C: REFORMAT

Parse an existing document into `content.json`, then run the CREATE render pipeline to produce a beautifully styled PDF.

**Core Pipeline**: `Init Project → Source Document → Design Tokens → Parse source_document.md → Review → Validate → Render PDF`

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/create_pdf/project_manager.py` | Project init / Source Document import / validate / info |
| `scripts/create_pdf/palette.py` | Generate design tokens (`tokens.json`) |
| `scripts/create_pdf/reformat_parse.py` | Parse `source_document.md` → content.json |
| `scripts/create_pdf/render_body_typst_native.py` | Compile content.json → PDF via Typst |

## Supported Input Formats

| Format | Notes |
|--------|-------|
| `.md` | Markdown — ATX headings, images, fenced code, blockquotes, bullets, tables, display math |
| `.txt` | Plain text — normalized into Markdown before parsing |
| `.pdf` | Converted through the PARSE engine into Markdown with extracted images |
| images | Copied into `assets/images/` and converted to Markdown image references |
| `.json` | Represented as fenced JSON in `source_document.md`, or validated directly when passed to `--input` |

---

## Workflow

### Step 1: Initialize Project

**GATE**: User has provided an existing document to reformat.

```bash
python3 scripts/create_pdf/project_manager.py init <project_name> --type <type> --dir .prismclaw
```

> Project folders are created under `.prismclaw/` with the naming convention `pdf-{project_name}-{YYYYMMDD}-{uuid8}`.

**Checkpoint — Project workspace created. Proceed to Step 2.**

---

### Step 2: Build Source Document

**GATE**: Step 1 complete; source file available.

```bash
python3 scripts/create_pdf/project_manager.py import-sources <project_path> <source_file>
```

This writes `<project_path>/source_document.md`, snapshots extracted/copied images into `<project_path>/assets/images/`, and records provenance in `<project_path>/source_manifest.json`.

**Checkpoint — Source Document generated. Proceed to Step 3.**

---

### Step 3: Generate Design Tokens

**GATE**: Step 2 complete; `source_document.md` exists and content is known.

```bash
python3 scripts/create_pdf/palette.py \
  --title <title> --type <type> \
  --out <project_path>/tokens.json
```

| Parameter | Values | Default | Description |
|------|------|------|------|
| `--title` | any string | required | Document title (appears in the header) |
| `--type` | palette template name | required | Controls palette selection; see "Palette templates" in [design.md](../create_pdf/design.md) for options |
| `--accent` | `#HEX` | derived from type | Override the accent color |
| `--font` | `fangsong`, `serif` | `fangsong` | Zhuque Fangsong or Source Han Serif (only effective with `--lang zh`) |
| `--lang` | `zh`, `en` | `zh` | `zh` uses CJK fonts; `en` uses Helvetica/Times New Roman |
| `--columns` | `1`, `2` | `1` | Use 2 only when the user explicitly requests it |
| `--no-cover` | flag | off | Omit the cover page |
| `--no-header` | flag | off | Omit header/footer |
| `--layout` | template name | `default` | Page layout template; see "Layout templates" in [design.md](../create_pdf/design.md) for options |
| `--date` | YYYY-MM-DD | today | Document date |
| `--meta` | path to JSON file | — | Alternative: provide title/type/author/date via a JSON file |

> `--lang` rule: if the user wants an English document or the content is pure English, you must pass `--lang en`; for Chinese documents use the default `--lang zh`.

**Checkpoint — `tokens.json` generated. Proceed to Step 4.**

---

### Step 4: Parse Source Document

**GATE**: Step 3 complete; `source_document.md` exists.

```bash
python3 scripts/create_pdf/reformat_parse.py --project <project_path> --out <project_path>/content.json
```

**Parser behavior:**
- Markdown `**bold**` / `*italic*` / `` `code` `` → `<b>` / `<i>` / `<code>` markup
- ATX headings (`#`, `##`, `###`) → `h1`, `h2`, `h3` blocks
- Markdown images `![caption](path)` → `figure` blocks with project-local paths
- Fenced code blocks → `code` blocks with `language` field
- Tables → `table` blocks
- Plain text sections → paragraph blocks

**Checkpoint — content.json parsed. Proceed to Step 5.**

---

### Step 5: Review & Fix

**GATE**: Step 4 complete; `content.json` exists.

Review the generated `content.json` for accuracy:
- Verify heading hierarchy and numbering (h1: `一、`, h2: `1.1`, h3: `1.1.1` — full rules in [design.md](../create_pdf/design.md))
- Check that no Chinese quotes leaked through
- Fix any misclassified blocks (e.g., plain text parsed as heading)
- Add missing structure if needed (callouts, dividers, pagebreaks)

> This step is mandatory — automated parsing is imperfect. Always review before rendering.

**Checkpoint — content.json reviewed and corrected. Proceed to Step 6.**

---

### Step 6: Validate

**GATE**: Step 5 complete; content.json has been reviewed.

```bash
python3 scripts/create_pdf/project_manager.py validate <project_path>
```

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

Output path: use the user-specified location, or current working directory with a descriptive filename.

**Checkpoint — PDF rendered. Pipeline complete.**

---

## Reference Resources

| Resource | Path |
|----------|------|
| Visual system & block types | [design.md](../create_pdf/design.md) |
