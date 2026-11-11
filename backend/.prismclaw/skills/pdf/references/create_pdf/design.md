# Design System

The aesthetic layer. Read this file before running any script. This file answers "what should it look like, and why."

---

## The one rule

Every design decision must be **rooted in the content and purpose of the document**.
Deep teal + beige does not equal "professional"; a serif face + cream does not equal "elegant."
A color chosen because it fits the content always beats one chosen because it "looks safe."

---

## Palette logic

`palette.py` takes a short content description and outputs `tokens.json`. It follows this reasoning:

### Mood → base palette

| Content signal | Mood | Background | Accent | Text |
|---|---|---|---|---|
| Research, science, analysis | Authoritative | `#0F1F2E` deep ink | `#00B4A6` teal | `#F0EDE6` warm white |
| Business, strategy, finance | Confident | `#1C1C2B` near-black | `#E8A020` amber | `#F5F2EC` cream |
| Creative, portfolio, design | Expressive | `#1A0A2E` deep purple | `#FF6B6B` coral | `#FAF5FF` lavender white |
| Education, academic papers | Scholarly | `#FAFAF7` warm white | `#2C4A7C` navy | `#1A1A2E` dark |
| Medical, health | Calm | `#F5F9F8` pale mint | `#2D8B72` forest green | `#1E3830` deep green |
| Resume / personal | Clean | `#FFFFFF` white | drawn from content | `#111111` near-black |
| General / unknown | Neutral | `#F8F6F1` warm off-white | `#3D3D3D` dark gray | `#1A1A1A` black |
| Formal publications, annual reports | Magazine | `#F2F0EC` warm linen | `#1C3557` deep navy | `#0D1A2B` near-black |
| High-end/dark reports, tech reviews | Darkroom | `#151C27` deep navy | `#4A6FA5` steel blue | `#F0EDE6` warm white |
| Technical docs, developer reports | Terminal | `#0D1117` near-black | `#39D353` neon green | `#E6EDF3` cool white |
| Portfolio, creative, photography | Poster | `#FFFFFF` white | `#0A0A0A` near-black | `#0A0A0A` near-black |

### Accent selection rules

- **Use one accent color only.** Two splits the visual focus.
- The accent appears only on: section dividers, callout left borders, table header backgrounds, and the header line. Nowhere else.
- Don't default to blue. Blue is the most overused accent in AI-generated documents.

### Color anti-patterns (never use)

| ❌ Avoid | Why |
|---|---|
| White background with a purple gradient | Quintessential AI aesthetic — instantly "looks generated" |
| Navy + gold | A played-out corporate cliché |
| Fully black background | Prints poorly, feels oppressive |
| More than 3 colors in the system | Visual noise |
| Accent color in body text | Destroys readability |

### Palette templates (`--type`)

Palette JSON files live in `assets/templates/palettes/`; adding a `.json` adds a new document type. The `--type` parameter (in the generate-tokens step of CREATE/REFORMAT) is chosen from the following:

| Type | Mood | Use for |
|------|------|----------|
| `report` | authoritative | Work reports, analytical reports |
| `proposal` | confident | Business proposals, solution docs |
| `resume` | clean | Personal resumes |
| `portfolio` | expressive | Portfolios, showcase docs |
| `academic` | scholarly | Academic papers, research reports |
| `general` | neutral | General documents |
| `minimal` | restrained | Minimalist style |
| `stripe` | bold | Striped-decoration style |
| `diagonal` | dynamic | Diagonal-decoration style |
| `frame` | classical | Framed-decoration style |
| `editorial` | editorial | Publications, journals |
| `magazine` | magazine | Magazine-layout style |
| `darkroom` | darkroom | Dark-theme reports, tech reviews |
| `terminal` | terminal | Technical docs, developer reports |
| `poster` | poster | Posters, large format |

> Custom type: create `assets/templates/palettes/<name>.json` providing the `accent`, `accent_lt`, `page_bg`, `dark`, `body_text`, `muted`, and `mood` fields. For color values, refer to "Mood → base palette" above.

### Layout templates (`--layout`)

Layout templates live in `assets/templates/layouts/`, each a directory containing `preamble.typ`. Currently only `default` (standard A4, accent header line, page numbers, clear heading hierarchy). To add a layout: create `assets/templates/layouts/<name>/preamble.typ` using `{{variable}}` placeholders.

---

## Typography system

### Font-pairing logic

At most two fonts, always.

| Role | Standard | Built-in font |
|---|---|---|
| Display (H1) | Striking, high-contrast, heavy weight | Zhuque Fangsong / Source Han Serif CN Bold |
| Body (body, caption, UI) | Highly readable at 10–11pt | Zhuque Fangsong / Source Han Serif CN Regular |

Body pages use built-in CJK fonts rendered through Typst — no system-font probing needed.

### Type scale

All sizes are in pt. `palette.py` fills `tokens.json` with this scale.

| Token | Size | Leading | Use |
|---|---|---|---|
| `h1` | 22pt | 1.3 | Section titles |
| `h2` | 15pt | 1.4 | Subsection titles |
| `h3` | 11.5pt | 1.5 | Sub-subsections |
| `h4` | 10.5pt bold | 1.5 | Level-4 headings |
| `h5` | 10pt bold | 1.5 | Level-5 headings |
| `h6` | 10pt regular muted | 1.5 | Level-6 headings |
| `body` | 10.5pt | 1.6 | Main body text |
| `caption` | 8.5pt | 1.4 | Figure/table captions |
| `meta` | 8pt | 1.3 | Header/footer text |

### Spacing system

Margins and rhythm are the dividing line between "looks designed" and "looks merely printed."

| Token | Value | Notes |
|---|---|---|
| `margin_outer` | 2.8cm | Left/right margins |
| `margin_top` | 2.8cm | Top margin |
| `margin_bottom` | 2.5cm | Bottom margin |
| `section_gap` | 26pt | Space before an H1 |
| `para_gap` | 16pt | Space after a paragraph |
| `line_gap` | 12pt | Body leading |

Never use default margins (too cramped). Always set them explicitly via tokens.json.

---

## Title page (cover)

The title page is **generated automatically** from `tokens.json` metadata — the model must **not** write a title block in `content.json`.

### Layout

| Element | Style | Source |
|---------|-------|--------|
| Title | 54pt, bold, dark, centered | `tokens.title` |
| Author | 10.5pt, muted, centered below title | `tokens.author` |
| Date | 10.5pt, muted, centered below author | `tokens.date` |
| Accent line | Full-width horizontal line below the title area | `tokens.accent` |

### Rules
- The title page is always page 1; `content.json` content starts on page 2
- If `--author` or `--date` is empty, the corresponding line is omitted
- Title-page styling is controlled by the layout template (`preamble.typ`)
- Don't add an `h1` block repeating the document title — it's already on the cover

---

## Inside-page rules

### What "restraint" means in practice

Every design decision should be removing something, not adding something.
A page is finished when there's nothing left to remove.

- The accent color appears only on section dividers — not on headings, not on list items
- No card components (bordered boxes with colored titles)
- No rounded corners on any element except callout boxes (4px max)
- No shadows anywhere
- Tables: no gridlines except the outer frame
- Callout boxes: no icons

### Header / footer

Header: document title (left, 7.5pt, muted) + accent line (1.5pt, full-width below)
Footer: author name (left, 7.5pt, muted) + page number (right, 7.5pt, muted) + faint line above

---

## Quality bar

A PDF passes when a designer would not be embarrassed to hand it to a client. Specifically:

- Body text is readable at arm's length without squinting
- Every page looks like it belongs to the same document
- No element overflows the edge or overlaps another
- Page numbers are present and correct
- The accent color appears fewer than 8 times per page on average

---

## Heading numbering rules (critical — read before writing any heading)

- `h1` headings **must** use Chinese numbering: `"一、xxx"`, `"二、xxx"`, `"三、xxx"` …
- `h2` headings **must** use hierarchical numbering: `"1.1 xxx"`, `"1.2 xxx"`, `"2.1 xxx"` …
- `h3` headings **must** continue the hierarchy: `"1.1.1 xxx"`, `"1.1.2 xxx"` …
- Never use a bare unnumbered heading — `"必游景点"` is wrong, `"一、必游景点"` is right.

Example:
```json
{"type": "h1", "text": "一、必游景点"},
{"type": "h2", "text": "1.1 故宫博物院"},
{"type": "h3", "text": "1.1.1 游览路线"},
{"type": "h1", "text": "二、美食推荐"},
{"type": "h2", "text": "2.1 经典京味"}
```

---

## Block type reference

All body blocks share one token system — colors and fonts come from `tokens.json`, never hardcoded.

> All headings must be numbered — full rules are in "Heading numbering rules" above; the "Design notes" column below is just a reminder.

| Block | Rendering | Design notes |
|---|----------------------------------------------------------|---|
| `h1` | 22pt title + full-width accent line below | Chinese numbering `"一、"` … |
| `h2` | 15pt title, dark text | Hierarchical numbering `"1.1 "` … |
| `h3` | 11.5pt bold, dark text | `"1.1.1 "` … — h3 uses no accent color |
| `h4` | 11pt bold, dark text | Same weight as h3, distinguished by smaller size |
| `h5` | 10.5pt bold, dark text | Very low visual weight; use sparingly |
| `h6` | 10.5pt bold, dark text | Lowest heading level; muted color signals low hierarchy |
| `body` | 10.5pt justified, 17pt leading, **2em first-line indent** | Supports `<b>` `<i>` `<font>` markup; the renderer auto-indents the first line — do **not** add leading spaces manually |
| `bullet` | Body size with a `•` prefix, 14pt indent | For unordered lists |
| `numbered` | Body size with an `N.` prefix, hanging indent | Counter auto-resets on any non-numbered block — no manual numbering |
| `callout` | Accent left border (4px) + light background | Only field is `text`; at most one callout per section — overuse weakens the effect |
| `table` | Accent header row, alternating row shading, outer frame only | Required: `headers`, `rows`. Optional: `col_widths` (numeric fractions, e.g. `[0.3, 0.5, 0.2]` or `[1, 2, 1]` — values are concatenated with `fr`), `caption`. Default columns are equal `1fr`. **Tables are breakable across pages**; `caption` is rendered as the last (full-width) table row so it always stays attached. |
| `image` | Scaled to column width (100%), aspect ratio preserved | Use `path` **or** `src` (either accepted); `caption` optional. Missing file renders `[Image not found: <path>]` placeholder |
| `figure` | Same as image, but caption auto-prefixed "Figure N:" | Figure counter increments across all `figure`, `chart`, `flowchart` blocks. If `caption` is empty, renders just `"Figure N"` |
| `code` | Courier 8.5pt, accent left border, light background | Optional `language` label (rendered above the block). Lines longer than ~80 chars are **auto-wrapped** by the renderer — but keep originals ≤80 to avoid mid-token breaks |
| `math` | Formula centered, optional right-aligned equation label | Typst math syntax preferred; LaTeX auto-converted. Expressions longer than 200 chars auto-break at top-level `=` / `+` / `-` / `,` / `*` |
| `chart` | Bar/line/pie charts rendered via Typst cetz | `chart_type` defaults to `"bar"`; colors auto-generated from accent (no overrides); requires `caption` when `figure` is true/default |
| `flowchart` | Flowcharts with labeled arrows | 4 node shapes (see below); default shape is `rect`; requires `caption` when `figure` is true/default |
| `bibliography` | Numbered reference list with hanging indent | Required: `items` (array of `{id, text}`); optional: `title` (default `"References"`). Title is rendered as **h1 + accent line** (not h2). Each entry renders as `[id] text` with an 8.5pt bold id and 9pt body text, hanging-indent 28pt. |
| `divider` | Accent-color 1.2pt line with whitespace | No fields; use sparingly — only for major topic shifts |
| `caption` | 8.5pt muted text, centered | Standalone block with a `text` field (figure/table blocks have their own `caption` field) |
| `pagebreak` | Forced page break | **Only** when the user explicitly requests it, or for a standalone cover/appendix page. **Never** insert before/after `h1`/`h2` — headings already carry section spacing. |
| `spacer` | Vertical whitespace | `pt` field (default 12) |

### Math formula guidance

**Rendering engine:** Typst (native). Also accepts LaTeX syntax and auto-converts.

**Typst math syntax (preferred):**

| Meaning | LaTeX | Typst math (preferred) |
|---|---|---|
| Fraction | `\frac{a}{b}` | `frac(a, b)` |
| Square root | `\sqrt{x}` | `sqrt(x)` |
| Subscript | `x_{i}` | `x_i` or `x_(i j)` |
| Superscript | `x^{2}` | `x^2` or `x^(2n+1)` |
| Summation | `\sum_{n=0}^{\infty}` | `sum_(n=0)^infinity` |
| Integral | `\int_0^1 f(x)\,dx` | `integral_0^1 f(x) dif x` |
| Greek letters | `\alpha, \beta` | `alpha, beta` |
| Bold | `\mathbf{E}` | `bold(E)` |
| Hat | `\hat{H}` | `hat(H)` |
| Vector | `\vec{v}` | `arrow(v)` |
| Text in math | `\text{mol}` | `"mol"` |
| Matrix | `\begin{pmatrix}...\end{pmatrix}` | `mat(delim: "(", a, b; c, d)` |
| Ellipsis | `\cdots` | `dots.c` |

**Rules:**
1. Always close delimiters — every `(` has a `)`, every `[` has a `]`
2. Typst passes function arguments with `()`, **not** `{}` — write `frac(a, b)`, not `frac{a}{b}`
3. Separate function arguments with `,`; separate matrix rows with `;`

**Inline math** in `body`/`bullet`/`callout` blocks: wrap in `$...$` (LaTeX syntax) — see "Inline markup rules" below.

**Equation labels:** `"label": "(1)"` — rendered right-aligned to the right of the formula.

**Examples:**
```json
{"type": "math", "text": "E = m c^2", "label": "(1)"}
{"type": "math", "text": "sum_(n=1)^infinity 1/(n^2) = pi^2/6", "label": "(2)"}
{"type": "math", "text": "integral_0^infinity e^(-x) dif x = 1", "label": "(3)"}
```

### Chart guidance

**Rendered natively by Typst cetz** — no external chart service, image files, or network needed.

| chart_type | Use case | Required fields |
|---|---|---|
| `bar` | Compare discrete categories | `labels`, `datasets` |
| `line` | Trends over time or ordered categories | `labels`, `datasets` |
| `pie` | Part-to-whole composition | `labels`, `datasets[0].values` |

**Field reference:**

| Field | Required | Type | Notes |
|---|---|---|---|
| `chart_type` | no | `"bar"` \| `"line"` \| `"pie"` | Defaults to `"bar"` if omitted or unknown |
| `labels` | yes | array of strings | X-axis categories (bar/line) or slice names (pie) |
| `datasets` | yes | array of `{label, values}` | For pie, only `datasets[0].values` is used |
| `datasets[].label` | only if multi-series | string | Shown in legend; defaults to `"Series N"` if absent |
| `datasets[].values` | yes | array of numbers | Length should match `labels`; missing values treated as 0 |
| `title` | no | string | Rendered as 10pt bold above the chart |
| `y_label` | no | string | Rotated 90°, left of the y-axis (bar/line only — ignored on pie) |
| `caption` | conditional | string | Required when `figure` is true (default) — see below |
| `figure` | no | bool | Defaults to `true`; when `true`, caption is auto-prefixed `"Figure N: "` |

**Rules:**
- Colors come from the document accent — **do not pass custom colors** in `datasets`; any color fields are ignored.
- Bar charts always include `0` in the y-axis range so bars have a baseline; if all values are negative, bars grow downward from 0.
- Pie chart percentages are auto-computed from `values` (no need to pre-normalize); slice labels show inline `xx.x%` and the legend shows the category names.
- Missing/empty `caption` with default `figure: true` renders an empty `"Figure N: "` label — provide a real caption.

### Flowchart guidance

**Node shapes:**

| shape | Used for | Typst mapping |
|---|---|---|
| `rect` (default) | Process steps | native `rect` |
| `diamond` | Decision / condition | `fletcher.shapes.diamond` |
| `oval` or `terminal` | Start / end | `fletcher.shapes.pill` |
| `parallelogram` | Input / output | `fletcher.shapes.hexagon` (rendered as a hexagon) |

> Unknown `shape` values silently fall back to `rect`. `terminal` is an alias for `oval`. `parallelogram` actually renders as a hexagon — visually similar enough for I/O nodes.

**Field reference:**

| Field | Required | Type | Notes |
|---|---|---|---|
| `nodes` | yes | array of `{id, label, shape?}` | `id` must be unique; `label` defaults to `id` if omitted |
| `edges` | yes | array of `{from, to, label?}` | `from`/`to` must reference existing node `id`s |
| `caption` | conditional | string | Required when `figure` is true (default) |
| `figure` | no | bool | Defaults to `true`; auto-prefix `"Figure N: "` |

**Layout (auto):**
- The renderer assigns positions via BFS from root nodes (those with no incoming edges); siblings at the same depth are spread horizontally.
- Forward edges draw straight arrows; back-edges (to an earlier node) draw curved arcs.
- Spacing is fixed at `(18mm horizontal, 14mm vertical)` — you cannot override it.

**Design rules (critical):**
- Don't draw a simple top-down linear flow — real flows have **branches** and **loops**.
- Include at least one **diamond (decision) node** with branching edges.
- Prefer **loop-back edges** where appropriate (retry loops, iteration cycles).
- Good pattern: `decision → (branch A continues) + (branch B loops back to an earlier step)`.
- Branching activates a wider layout — without it, the diagram collapses into a single column.
- Keep edge and node labels short (3–5 words max) — figures scale to ~78% at A4 column width; long labels wrap unpredictably.

### Bibliography guidance

**Fields (must match the renderer exactly):**

| Field | Required | Type | Notes |
|---|---|---|---|
| `type` | yes | `"bibliography"` | — |
| `items` | yes | array of `{id, text}` objects | Each entry must be an object; bare strings will not render |
| `items[].id` | yes | string | Citation label, rendered as `[id]` in 8.5pt bold. Use a number (`"1"`, `"2"`) or alphanumeric (`"Smith23"`) |
| `items[].text` | yes | string | Reference text, rendered in 9pt with a 28pt hanging indent |
| `title` | no | string | Section heading. Defaults to `"References"`; pass `""` to hide it |

**Rules:**
- The title is rendered as an **h1-level heading with accent line** (same visual weight as `一、xxx` sections), not as a smaller h2 — keep this in mind when planning the document outline.
- The renderer does not enforce a citation style (APA / Chicago / GB/T 7714 etc.) — pick one and stay consistent across all `text` values.
- Do not put a `bibliography` block inside another section's `h1`; it carries its own section heading.
- Other shapes are not accepted: `references`, `refs`, plain string entries, or a flat `["..."]` array will be silently dropped or render as empty.

**Example:**
```json
{
  "type": "bibliography",
  "title": "参考文献",
  "items": [
    {"id": "1", "text": "张三, 李四. (2024). PDF 文档生成技术研究. 计算机科学出版社."},
    {"id": "Smith23", "text": "Smith, J. (2023). Typst for technical writing. O'Reilly."}
  ]
}
```

### Image / figure guidance

- Preferred formats: PNG, JPEG.
- Either `path` or `src` is accepted (the renderer reads `path` first, then falls back to `src`).
- Paths may be **absolute** or **relative to the project root** (`<project_path>`); the renderer auto-snapshots the source file into `<project_path>/assets/images/` and rewrites the Typst reference — do not manage `assets/images/` manually.
- Image width is fixed at 100% of the column; aspect ratio is preserved. Shrink at source if the original is huge — never enlarge.
- `figure` blocks are auto-numbered (`Figure N: <caption>`); `image` blocks are not. Use `figure` for anything that needs cross-referencing.
- Missing file at render time renders a muted `[Image not found: <path>]` placeholder; if staging fails, you'll see `[Image not staged: <path>]`.

### Code block guidance

- Preserve whitespace exactly — don't indent the JSON value to "look pretty"; leading spaces become part of the rendered code.
- Optional `language` field renders a small language label above the block (e.g. `"language": "python"`). The special value `"text"` is treated as no language.
- No syntax highlighting (by design) — consistent with the restraint principle.
- The renderer **auto-wraps lines longer than 80 characters** at the last space before the limit (or hard-breaks if no space exists). Keep originals ≤80 chars to avoid awkward mid-token splits.
- Triple backticks inside `text` are handled automatically — the renderer escalates the fence to 4+ backticks as needed.

### Table guidance

- `headers` and `rows` are required; `headers` defines the column count and **must** match the cell count in each `rows[i]`.
- `col_widths` accepts numeric fractions (e.g. `[0.3, 0.5, 0.2]` or `[1, 2, 1]`); each value is suffixed with `fr` and passed to Typst. Without it, all columns share width equally (`1fr` each).
- Cells support inline markup (`<b>`, `<i>`, `<code>`, `$...$`); long unbroken Latin tokens get word-break opportunities inserted automatically.
- Tables are **breakable across pages** — the renderer wraps them in `block(breakable: true)`. Don't add manual `pagebreak` to keep a table on one page; if a single table truly cannot break, shorten it.
- `caption`, when present, is rendered as the **last full-width row of the table** (so it always stays attached to the table even across page breaks). It is not auto-prefixed with "Table N:".

### Body / list / callout details

- `body` text is rendered with a **2em first-line indent** (`#h(2em)`). Do not insert leading whitespace, full-width spaces (`　`), or tabs in the text — they stack on top of the auto-indent.
- `bullet` and `numbered` items are rendered with Typst's native list syntax (`-` / `+`). Mixing the two consecutively is fine; the `numbered` counter resets the moment a non-`numbered` block appears (including a single `bullet` between two `numbered` items).
- `callout` accepts only one field — `text`. No icon, no title, no color override.
- Inline math `$...$` is supported in `body`, `bullet`, `numbered`, `callout`, and table cells. Bare LaTeX without `$...$` will render as literal text.

### Heading details

- All six heading levels exist (`h1` through `h6`) and each maps to a Typst heading depth (`=`, `==`, … `======`); only `h1` carries the accent line, `h6` is muted (lowest visual weight).
- Don't repeat the document title as `h1` on page 2 — the cover already carries it; start with `一、xxx`.
- Don't use a heading purely for visual spacing — use `spacer` or let the natural section gap apply.

---

## Chinese quotes are FORBIDDEN (critical)

Chinese curly quotes `""''` and corner brackets `「」『』` **break JSON parsing** and crash the pipeline.

Always convert them to escaped English double quotes `\"`.

| ❌ Wrong (JSON parse error) | ✅ Correct |
|---|---|
| `"text": "被称为"万国建筑博览群""` | `"text": "被称为\"万国建筑博览群\""` |
| `"text": "联想之星"重度起跳"策略"` | `"text": "联想之星\"重度起跳\"策略"` |
| `"text": "所谓'敏捷开发'"` | `"text": "所谓\"敏捷开发\""` |

This rule applies to all text fields (`text`, `caption`, `label`, `headers`, `rows`, node `label`, edge `label`, etc.).

---

## Inline markup rules

| Markup | Use | Example |
|--------|-------|---------|
| `<b>text</b>` | Bold | `"这是<b>重点</b>内容"` |
| `<i>text</i>` | Italic | `"这是<i>术语</i>定义"` |
| `<code>text</code>` | Inline code | `"使用<code>pip install</code>安装"` |
| `$...$` | Inline math (LaTeX) | `"总费用为 $C_{total}$"` |

> Critical: writing bare LaTeX like `C_{total}` without dollar signs produces garbled output. Always wrap inline math in `$...$`.
