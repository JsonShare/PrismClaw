"""Request-format helpers: context-payload rendering and cache-control breakpoints.

Moved from ``prismclaw.engine.agent.request.packing`` as part of the
contracts extraction (P1-1b) so that downstream consumers can depend on
the stable ``prismclaw.contracts.llm`` surface without reaching into
engine internals.
"""

from __future__ import annotations

from typing import Any

from prismclaw.schema.llm.cache_types import EPHEMERAL_CACHE_CONTROL, mark_cacheable


def render_context_payload_text(context_payload: dict[str, Any]) -> str:
    """Render context payload blocks as readable text for the legacy bridge.

    SDD residual-remediation R-A1 (A-0 spike finding E16/F2): the previous
    implementation dumped the whole payload as ``ensure_ascii=True`` JSON —
    every CJK char inflated to a 6-char ``\\uXXXX`` escape (1.44x tokens) and
    the per-block envelope (id/zone/priority/stability/cacheable/state_payload/
    metadata) roughly tripled the wire size while carrying nothing the model
    can use. Block contents are already designed readable text
    (``<runtime_context>...``, ``<agent_listing_delta>...``, memory tags),
    so the render is content-only: ``[block_id]\\ncontent`` per block.
    """
    blocks = context_payload.get("blocks") or []
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        content = block.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        title = str(block.get("id") or block.get("source") or "context")
        parts.append(f"[{title}]\n{content.strip()}")
    return "\n\n".join(parts)


def inject_cache_control_breakpoints(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deep Refactoring: reference-implementation-style caching.
    Place a cache_control breakpoint on the last two user messages or tool results
    in the conversation history to maximize prefix caching for recent turns.
    """
    if not messages:
        return messages

    injected = [dict(m) for m in messages]
    breakpoints_added = 0

    # Traverse backwards to find the last 2 user or tool messages
    for i in range(len(injected) - 1, -1, -1):
        msg = injected[i]
        role = msg.get("role")

        if role in {"user", "tool"}:
            content = msg.get("content")
            if isinstance(content, str):
                msg["content"] = [
                    {
                        "type": "text",
                        "text": content,
                        "cache_control": EPHEMERAL_CACHE_CONTROL,
                    }
                ]
                breakpoints_added += 1
            elif isinstance(content, list) and content:
                # Add to the last block of the content array
                last_block = dict(content[-1])
                mark_cacheable(last_block)
                new_content = list(content[:-1]) + [last_block]
                msg["content"] = new_content
                breakpoints_added += 1

        if breakpoints_added >= 2:
            break

    return injected
