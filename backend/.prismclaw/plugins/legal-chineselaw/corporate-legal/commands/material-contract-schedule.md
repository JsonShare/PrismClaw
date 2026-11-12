---
name: material-contract-schedule
description: 从尽职调查结论出发编制重大合同披露清单：依据并购协议中「重大合同」的定义筛选合同，并按协议约定的清单格式排版。当用户说「做合同披露清单」「披露清单」「Schedule 3.X」「重大合同列表」，或需要起草披露清单时使用。
argument_hint: '[purchase agreement path, or paste the Material Contract definition]'
category: 公司法务
---

# /material-contract-schedule

1. Load purchase agreement → Material Contract definition + schedule format.
2. Use the workflow below.
3. Apply definition to diligence findings. Flag edge cases.
4. Format per agreement. Consent overlay feeds closing checklist.

---

## Matter context

**Matter context.** Check `## Matter workspaces` in the practice-level PRISMCLAW.md. If `Enabled` is `✗` (the default for in-house users), skip the rest of this paragraph — skills use practice-level context and the matter machinery is invisible. If enabled and there is no active matter, ask: "Which matter is this for? Run `/corporate-legal:matter-workspace switch <slug>` or say `practice-level`." Load the active matter's `matter.md` for matter-specific context and overrides. Write outputs to the matter folder at `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/matters/<matter-slug>/`. Never read another matter's files unless `Cross-matter context` is `on`.

---

## Purpose

The purchase agreement has a rep: "Schedule 3.X lists all Material Contracts." This skill builds that schedule from the diligence findings — which contracts are material per the agreement's definition, in the format the agreement requires.

## Load context

- Purchase agreement draft — for the definition of "Material Contract" and the schedule format
- `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md` → materiality thresholds (may differ from the agreement definition — use the agreement's)
- Diligence findings from diligence-issue-extraction — contract-level data

## Workflow

### Step 1: Get the definition

Pull the definition of "Material Contract" from the purchase agreement — the PA definition controls. Deal-structure differences (stock vs. asset vs. merger) can change how a prong is interpreted, and regulated-industry overlays (healthcare, defense, financial services, telecom, government contracting) can add consent requirements that live outside the PA. If the deal involves any of those overlays, research the applicable anti-assignment or novation rules (for example, federal contracts, government contracting novation, sector-specific consent statutes) and cite the controlling rule.

Common prong categories to look for in the PA definition — these are not a substitute for reading the PA, and the list the PA uses controls:

- Dollar-value threshold (annual or aggregate)
- Term length
- Change-of-control or anti-assignment provision
- Exclusivity or non-compete
- Top N customer or supplier contracts
- Real property leases
- IP licenses (in-bound and out-bound)
- Related-party agreements
- Government contracts
- Contracts outside the ordinary course

The PA's definition is the test. Apply it mechanically — every contract that meets any prong in the PA's definition goes on the schedule.

### Step 2: Apply the definition to the findings

For each contract reviewed in diligence:

| Contract | Meets prong(s) | Include |
|---|---|---|
| [name] | [$X+ annual value; CoC provision] | Yes |
| [name] | [none] | No |

**Edge cases to flag for human decision:**
- Contract is $X-1 (just under threshold) but important to the business
- Contract meets a prong but is being terminated anyway
- Oral agreements or side letters that may or may not count

### Step 3: Gather schedule data

For each included contract, the schedule typically needs:

| Field | Source |
|---|---|
| Counterparty name | Contract |
| Contract title/type | Contract |
| Date | Contract |
| Term / expiration | Contract |
| Annual/total value | Contract or management data |
| Which materiality prong it meets | Step 2 analysis |
| Consent required for the deal | Diligence finding |
| VDR reference | Diligence inventory |

Pull from existing diligence extractions. If a field is missing, flag it — don't guess.

### Step 4: Format per the agreement

Disclosure schedules have a format — usually a numbered list or a table, sometimes with sub-parts by contract type. Match the format of the other schedules in the draft agreement.

```markdown
## Schedule 3.[X] — Material Contracts

The following are the Material Contracts as of the date hereof:

### (a) Customer Contracts

1. [Agreement Title], dated [date], between [Target] and [Counterparty].
   [Brief description if the format calls for it.]
   [VDR: path]

2. [...]

### (b) Supplier Contracts

[...]

### (c) Real Property

[...]

[etc. — sub-parts per the agreement's definition structure]
```

### Step 5: Consent tracking overlay

Separately (not in the schedule itself — this is internal), track which scheduled contracts require consent.

> The consent overlay and any pre-delivery working draft of the schedule are derived from privileged diligence materials and inherit their privilege and confidentiality status — distribution beyond the privilege circle can waive privilege. The schedule itself, once delivered as an exhibit to the executed PA, is a deal document and is not privileged; strip any internal annotations before delivery.


| Schedule # | Counterparty | Consent required | Status | Owner | Due |
|---|---|---|---|---|---|
| 3.X(a)(1) | [name] | Yes — CoC §12.2 | Requested | [name] | [date] |

This feeds closing-checklist.

## Cross-check

Before delivering:

- Every contract that met a prong is on the schedule (completeness)
- No contract is on the schedule that doesn't meet a prong (no over-disclosure — it's a rep, not a data dump)
- Schedule is consistent with the other reps (a contract on Schedule 3.X that creates a lien should also be on the liens schedule)
- Every entry has a VDR cite so buyer's counsel can find the underlying doc

## Handoffs

- **From diligence-issue-extraction:** Contract-level findings are the input.
- **To closing-checklist:** Consent items go on the checklist.

## What this skill does not do

- It doesn't decide the materiality definition — that's in the purchase agreement.
- It doesn't obtain consents — it tracks which ones are needed.
- It doesn't draft the rep — it populates the schedule the rep references.


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
