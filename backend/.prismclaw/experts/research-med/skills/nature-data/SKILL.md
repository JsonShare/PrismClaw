---
name: nature-data
name_zh: 数据可用性声明
description: >-
  数据可用性声明：Data Availability 声明撰写与审核、数据仓储选择、登录号与数据集引用、FAIR 元数据清单。覆盖受限数据、敏感数据、源数据与补充数据集。触发词：数据可用性、data availability、数据共享、仓储选择、登录号、FAIR。
category: 医学科研
invocation_policy: auto
priority: 8
---




# Nature Data Availability — Router

This skill is split into two layers:

- A **static layer** under `static/` that holds versioned, reusable content fragments (the default stance and source hierarchy, the Chinese-user operating mode, and the workflow with output format).
- A **dynamic layer** (this file plus `manifest.yaml`) that loads the core every time and reaches for the deeper policy/repository/FAIR references only when a step needs them.

Do not try to apply the data-availability logic from memory or from this router. Always load fragments from disk as described below.

## Routing protocol

Follow these four steps every time the skill is invoked.

### 1. Load the core layer

### 1. Load the core layer

本体系不会自动读取 `manifest.yaml`，必须在本技能目录下直接读文件。每次调用先读这三个文件：

- `static/core/stance.md` — what the data-availability package is, the default stance, and the source hierarchy.
- `static/core/chinese-mode.md` — how to operate when the user writes in Chinese (accept Chinese, draft English, convert terms precisely).
- `static/core/workflow.md` — the eight-step workflow and the output format.

`manifest.yaml` 只在需要确认某个 reference 的触发条件时手动查阅，不作为必经步骤。

### 2. No content axis — confirm journal and language inline

Unlike nature-writing or nature-figure, nature-data has no fragment axis. Its variation is handled at runtime, not by loading different content bodies:

- **journal/article type** — if journal-specific instructions conflict with this skill, follow the journal.
- **access route** — each dataset is classified into one route (public repository, controlled access, within paper, reused public, third-party restricted, justified request, or not applicable).
- **user language** — if the user writes Chinese, follow `core/chinese-mode.md` and add the 中文核对 block.

### 3. Run the workflow

Follow the eight-step workflow in `core/workflow.md`: identify the journal, inventory every supporting dataset, classify each into one access route, choose repository and identifier strategy before drafting, draft the statement with explicit dataset-to-location mapping, add formal dataset citations, run the FAIR/metadata audit, and return ready-to-paste text plus unresolved fields.

Do not invent DOIs, accession numbers, repository names, licences, embargo dates, ethics approvals, access committees, or data-use conditions. Flag "available upon request" as weak unless there is a specific legal, ethical, commercial, or third-party restriction.

### 4. Reach for references only when needed

The files under `references/` are deep references, not defaults. Open them on demand per the `references.on_demand` table in the manifest — for example `references/policy-principles.md` for the governing rules and edge cases, `references/repository-and-identifiers.md` for repository/accession/DOI choices, `references/statement-patterns.md` for ready-to-adapt statements, `references/fair-metadata-checklist.md` for the FAIR audit, `references/chinese-author-alignment.md` for Chinese wording, and `references/source-basis.md` to justify a rule with its official source.

When the target is the flagship journal Nature, also open
`references/nature-article-requirements.md` for statement placement,
mandatory-deposition routing, central-code review access, materials and
structure-file checks.

When the target is Nature Machine Intelligence, open
`../nature-shared/journal-formats/nature-machine-intelligence.md`. Enforce a
Data Availability statement and a separate `Code availability` section after
it and before references; check reviewer access, precise restrictions,
repository/identifier quality and the Software Submission Checklist for newly
developed central code.

## Why this split

- The static layer is versioned and reviewable; the core stays small for a normal statement.
- The dynamic layer keeps each invocation cheap: the policy, repository, and FAIR depth load only when a step needs them.
- The router itself is short on purpose. Update fragments and references, not this file, when adding scope.
- This structure mirrors `nature-writing`, `nature-polishing`, `nature-reader`, `nature-paper2ppt`, `nature-figure`, `nature-citation`, and `nature-response`.
