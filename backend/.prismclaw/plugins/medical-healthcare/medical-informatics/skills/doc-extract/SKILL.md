---
name: doc-extract
name_zh: 文档文本抽取
description: 把 PDF、DOCX、XLSX、PPTX、RTF 或纯文本／Markdown／HTML 文档抽取为纯文本。当需要把二进制文档转成可处理文本时使用，例如合同 PDF、EHR 的 DocumentReference 附件。fhir 技能会直接调用 scripts/extract.ts；contracts 的 MCP server 自带一份副本（mcp_servers/documents/src/extract.mjs），改动需两边同步。
category: 医疗信息学
invocation_policy: user_only
---

# doc-extract

Shared document-to-text extraction. One script, no state: reads an input file, prints JSON to stdout, writes nothing to disk (PHI-safe — no caches, no temp files; callers own any caching).

## Setup (once)

```bash
cd <this skill dir> && bun install
```

This pulls [liteparse](https://www.npmjs.com/package/@llamaindex/liteparse) (the `lit` bin, used for PDF/DOCX/XLSX/PPTX, OCR included) and [rtf-to-text](https://www.npmjs.com/package/rtf-to-text) (RTF). Without it, PDFs still work via a `pdftotext -layout` fallback if poppler is installed; other binary formats require liteparse.

## Use

```bash
bun <this skill dir>/scripts/extract.ts <input-file> [--content-type <mime>]
```

Output on stdout:

```json
{ "text": "...", "method": "liteparse | pdftotext | rtf-to-text | passthrough", "pages": 12 }
```

- `text` is page-anchored for paged formats: `=== [page N] ===` markers between pages.
- `pages` is present when page markers exist.
- `method` is the extractor that actually produced the text.
- Format is taken from the file extension; pass `--content-type` (e.g. `application/pdf`) when the file has no useful extension, as with downloaded EHR attachments. Note liteparse refuses extension-less files, so those PDFs go through the `pdftotext` fallback.
- Errors print `{"error": "..."}` to stderr and exit 1.

## Table caveat

Tables with multiple value columns (option A vs option B, in-tier vs out-of-tier) can interleave columns line-by-line in the extracted text: fragments of adjacent cells alternate, and a cell's text can even land mid-sentence inside a neighboring column. Values usually survive, but which column a value belongs to can become ambiguous. When an answer comes from one column of a multi-column table and the document has no redundant restatement of the value elsewhere, verify it by reading the original page directly before treating it as ground truth. The extracted text's `=== [page N] ===` anchor tells you which page: pass it to the Read tool's `pages` parameter (e.g. pages: "37") to render just that page to vision instead of the whole document.

## For other skills

Import the functions instead of shelling out when you're already in bun TS:

```ts
import { extract, resolveLit } from "../doc-extract/scripts/extract";
const lit = resolveLit([myRoot]); // also checks myRoot/node_modules/.bin/lit
const text = extract(lit, "/path/to/file.pdf"); // string | null
```

The contracts skill consumes it this way (its ingest caching stays on the contracts side).


---

## 医疗安全声明

本回复由 AI 辅助生成，仅提供一般性参考框架，不构成医疗建议、诊断或治疗方案，也不替代具有执业资质医师的专业判断。具体医疗问题，请咨询持有效执业证的医师或获准从事医疗业务的机构。

涉及临床决策、处方开具、诊断编码、医保申报等需由专业人员确认的正式输出，须经授权医师/编码员独立审查、修改并以医疗机构名义出具后方可使用。

本回复中的医学知识、编码规则或监管口径为参考时点的有效状态，后续修订或地方实施细则差异请以最新正式发布版本为准。
