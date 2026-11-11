# Contract Reasoning

> See **Security** below before running on real contracts.

Answers questions across a corpus of contract documents with verified citations. You ask in plain English — "which of these contracts let the buyer terminate for convenience, and on what notice?" — and the session reads every contract in parallel using **Read**, **Grep**, and **SearchCodebase**, then composes the answer in chat with source references. Every claim carries a quote traced back to the source document.

**No external services, no databases, no server dependencies.** The skill is fully self-contained and reads contract files directly from the local filesystem using PrismClaw's built-in tools.

## Quick start

In PrismClaw, from a directory containing your contracts:

```
/contracts which of these have an evergreen renewal clause?
```

The skill reads your contracts and shows you a plan — how it read the question, what it will read — and waits for your go. After you confirm, the reading happens and the answer arrives in chat: every claim backed by a quote from the source document.

## What's local

This is **single-user, local-only**:

- Contracts must be on the **local filesystem** — the same machine as PrismClaw.
- No database, no MCP server, no network calls. All analysis is done in-context using PrismClaw's built-in tools.

## Security

Contract text is untrusted input. The design keeps the blast radius structural, not behavioral:

- **The skill only uses Read, Grep, and SearchCodebase** — read-only tools that cannot modify files or make network requests.
- **Citations are verified against the source** — every claim references a specific file and section, and the user can verify by reading the same file.
- **Don't run this on a corpus you don't trust.**

## How it's built

- `SKILL.md` — the whole flow in one file: bootstrap, plan (with the one confirmation pause), scope, read, and the chat answer. **This is the file to read or edit to understand or change run behavior.**
