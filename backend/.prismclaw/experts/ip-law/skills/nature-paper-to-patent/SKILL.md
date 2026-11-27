---
name: nature-paper-to-patent
name_zh: 论文转专利交底书
description: Convert scientific papers, theses, technical reports, source code, figures, inventor notes, or research manuscripts into evidence-grounded Chinese invention patent drafts and attorney-facing technical disclosure materials. Use when an AI agent must mine patent points, draft or revise a Chinese technical disclosure (技术交底书), run prior-art comparison, convert Office project materials, map every claimed feature to source evidence, preserve core formulas as editable Office Math, generate claim-aligned flowcharts and methodology figures, compare a paper with an existing patent, audit support and consistency, or deliver Chinese DOCX patent/disclosure files.
category: 知识产权
invocation_policy: user_only
priority: 8
---

# Paper to Chinese Patent

Use this file as the router for the patent-drafting workflow. Do not draft the
application directly from the paper abstract or contribution list.

## 1. Load the workflow

本体系不会自动读取 `manifest.yaml`，必须在本技能目录下直接读文件。每次调用先读以下核心文件（always_load）：

- `static/core/principles.md` — 专利撰写的底层原则与红线；
- `static/core/workflow.md` — 全流程阶段门、中间产物与交付包格式；
- `static/core/output-contract.md` — 交付物质量阈值与「incomplete draft」判定。

`manifest.yaml` 仅在需要核对某个 reference 触发条件时手动查阅，不作为必经步骤。

从用户文件与需求中检测三个轴，并用一行简短声明检测值：

- `source_format`：`pdf-text`（可选文本 PDF）/ `scanned-pdf`（图像或 OCR PDF）/ `pasted-text`（用户粘贴文本）/ `mixed-project`（工作区含源码、补充材料、发明人笔记、附图或已有专利）。可多选，默认 `pdf-text`。按需加载 `static/fragments/source/<值>.md`。
- `task_mode`：`full-draft` / `claim-set` / `disclosure-analysis` / `technical-disclosure` / `disclosure-iteration` / `paper-patent-audit`，单选，默认 `full-draft`。按需加载 `static/fragments/task/<值>.md`。
- `invention_type`：`algorithm-software` / `apparatus-system` / `process-material` / `mixed`，单选，默认 `algorithm-software`。按需加载 `static/fragments/invention/<值>.md`。

仅加载与检测值匹配的 fragment 文件。详细 references 仅在其条件满足时加载：

- 撰写或审计中文权利要求/说明书 → `references/cn-patent-drafting-guide.md`
- 生成附图或选型论文方法学图 → `references/patent-figure-guide.md`
- 填充或校验结构化草稿 → `references/draft-schema.md`
- 学习论文-专利配对示例的转化模式 → `references/corpus-derived-patterns.md`
- 将提供的论文与已有专利对比 → `references/corpus-pair-audit.md`

## 2. Preserve source grounding

Create stable source IDs before drafting:

- `P001...` for paper text blocks;
- `E001...` for equations;
- `F001...` for source figures;
- `C001...` for source-code or supplementary evidence.

Every material feature in a formal claim must map to one or more source IDs.
Use only `explicit`, `inherent`, `needs-confirmation`, or `unsupported` as
support states. Exclude `unsupported` features from formal claims.

Never infer inventorship, ownership, unpublished implementation details,
publication dates, prior-art conclusions, or legal sufficiency. Use
`[TO CONFIRM: specific question]` outside formal claims when facts are missing.

## 3. Draft through stage gates

For `full-draft`, `claim-set`, `disclosure-analysis`, and `paper-patent-audit`,
complete the stages in `static/core/workflow.md` in order. Persist the
intermediate artifacts specified there. Do not move to formal claims until the
source map, terminology ledger, inventories, evidence ledger, and invention
concept pass their gates.

For `technical-disclosure`, follow the ordered prompt references in
`static/fragments/task/technical-disclosure.md`. For `disclosure-iteration`,
follow `static/fragments/task/disclosure-iteration.md` and preserve the prior
draft instead of restarting the formal application workflow.

For a full application, draft claims first, then align the specification,
figures, embodiments, and abstract to the claim terminology and step order.

## 4. Produce Chinese formal documents

Agent-facing analysis may use the user's preferred language. Produce formal
Chinese patent deliverables in Chinese when the task is a formal application
package:

- 权利要求书;
- 说明书;
- 说明书摘要;
- 摘要附图;
- figure labels and descriptions.

For `technical-disclosure` and `disclosure-iteration`, produce the Chinese
technical disclosure (`技术交底书`) as timestamped Markdown plus matching DOCX,
with Mermaid system/process diagrams rendered through `scripts/disclosure/`.

For algorithmic inventions, retain source-supported core formulas, define every
symbol, explain each formula's technical operation, and render formulas as
native editable Office Math in DOCX. Do not use plain LaTeX strings as the
visible formula.

Generate the main flowchart from the ordered steps of the principal method
claim. Its final node must name the concrete domain output, such as a defect
detection result, target pose, state estimate, or control instruction. Reuse
the same main figure as the abstract figure and a specification figure.

## 5. Validate before delivery

For formal application packages, populate the structured draft described in
`references/draft-schema.md`, then run:

```bash
python scripts/validate_patent_draft.py draft.json
python scripts/build_patent_package.py draft.json --output-dir outputs --prefix patent
```

Resolve all validation `ERROR` findings. Review every `WARNING` against the
source. Label the result `incomplete draft` when a required quality threshold
in `static/core/output-contract.md` is not met.

For technical disclosures, run the internal checks in
`references/disclosure/disclosure_self_check.md`, render Mermaid/Word outputs
with `scripts/disclosure/mermaid_render.py`, and resolve formula, parameter,
prior-art URL, and chapter-consistency issues before delivery.

The generated package is a drafting aid for inventor and patent-professional
review, not a patentability opinion, infringement opinion, or filing guarantee.
