from __future__ import annotations

import hashlib
import json
from typing import Any

from prismclaw.engine.compression.cache_block_packing import (
    MIN_CACHE_TOKENS,
    CacheBlockPacker,
)
from prismclaw.schema.content.replacement_state import ReplacementDecisionState
from prismclaw.schema.content import (
    append_replacement_decisions,
    extract_replacement_decisions_from_blocks,
)
from prismclaw.engine.context.projection_models import PromptProjection
from prismclaw.engine.state.state_protocol import normalize_state_payload
from prismclaw.schema.agent.data_types import CacheStrategySnapshot
from prismclaw.schema.content.context import ContextPackLayout

_MIN_CACHE_TOKENS = MIN_CACHE_TOKENS
_CONTEXT_PAYLOAD_SCHEMA_VERSION = "prism_context_payload.v2"


from prismclaw.schema.llm.request import PackedLLMRequest


class RequestPackager:
    def __init__(self, *, cache_block_packer: CacheBlockPacker | None = None) -> None:
        self._cache_block_packer = cache_block_packer or CacheBlockPacker()

    def pack_projection(
        self,
        *,
        projection: PromptProjection,
        model: str,
        request_mode: str | None = None,
        previous_cache_strategy: CacheStrategySnapshot | None = None,
        previous_replacement_state: ReplacementDecisionState | None = None,
        session_id: str | None = None,
    ) -> PackedLLMRequest:
        static_plan = ContextPackLayout(static_blocks=projection.system_blocks)
        current_replacement_state = append_replacement_decisions(
            previous_replacement_state,
            extract_replacement_decisions_from_blocks(
                [
                    *projection.dynamic_projection,
                    *projection.history_projection,
                ]
            ),
        )
        system = self._cache_block_packer.pack(
            plan=static_plan,
            model=model,
            request_mode=request_mode,
            previous_cache_strategy=previous_cache_strategy,
            replacement_state=current_replacement_state,
            previous_replacement_state=previous_replacement_state,
        )

        # T5：工具结果唯一保留在 messages 的 tool 角色，不再渲染进
        # context_payload（此前工具结果块经投影通道重复注入）。
        dynamic_blocks = [
            *projection.dynamic_projection,
            *projection.history_projection,
        ]
        context_payload = _build_context_payload(dynamic_blocks) if dynamic_blocks else None

        # Estimate token overhead that legacy bridge injection would add to
        # history_budget.  The structured_extension path sends the payload via
        # ``extra_body`` and does NOT consume message tokens, so we only count
        # the rendered text + wrapping tags here.
        context_payload_tokens = 0
        if context_payload is not None:
            from prismclaw.engine.compression.compaction import estimate_tokens
            from prismclaw.schema.llm.request_format import render_context_payload_text
            rendered = render_context_payload_text(context_payload)
            # +role_text (~15 tokens) + <system-context> tags (~15 tokens)
            context_payload_tokens = estimate_tokens(rendered) + 30

        return PackedLLMRequest(
            messages=projection.projected_messages,
            system=system,
            context_payload=context_payload,
            context_payload_tokens=context_payload_tokens,
            session_id=session_id,
            cache_strategy=self._cache_block_packer.describe_strategy(
                plan=static_plan,
                model=model,
                request_mode=request_mode,
                previous_cache_strategy=previous_cache_strategy,
                replacement_state=current_replacement_state,
                previous_replacement_state=previous_replacement_state,
            ),
        )


def _build_context_payload(blocks: list[Any]) -> dict[str, Any]:
    rendered_blocks: list[dict[str, Any]] = []
    rendered_attachments: list[dict[str, Any]] = []
    for index, block in enumerate(blocks, start=1):
        state_payload = getattr(block, "state_payload", None)
        normalized_state = (
            normalize_state_payload(state_payload)
            if isinstance(state_payload, dict) and state_payload
            else None
        )
        metadata = getattr(block, "metadata", None)
        rendered_block = {
            "index": index,
            "id": str(getattr(block, "id", "") or ""),
            "source": str(getattr(block, "source", "") or ""),
            "zone": str(getattr(block, "zone", "") or ""),
            "priority": str(getattr(block, "priority", "") or ""),
            "stability": str(getattr(block, "stability", "") or ""),
            "content": str(getattr(block, "content", "") or ""),
            "estimated_tokens": int(getattr(block, "estimated_tokens", 0) or 0),
            "truncatable": bool(getattr(block, "truncatable", True)),
            "cacheable": bool(getattr(block, "cacheable", False)),
            "replaceable": bool(getattr(block, "replaceable", False)),
            "state_payload": normalized_state,
            "metadata": metadata if isinstance(metadata, dict) and metadata else None,
        }
        rendered_blocks.append(rendered_block)
        attachment_entry = _attachment_transport_entry(rendered_block)
        if attachment_entry is not None:
            rendered_attachments.append(attachment_entry)

    payload = {
        "type": "prism_context_payload",
        "schema_version": _CONTEXT_PAYLOAD_SCHEMA_VERSION,
        "blocks": rendered_blocks,
    }
    if rendered_attachments:
        payload["attachments"] = rendered_attachments
        payload["attachment_count"] = len(rendered_attachments)
        payload["attachment_revision_digest"] = _attachment_revision_digest(
            rendered_attachments
        )
        payload["attachments_present"] = True
    else:
        payload["attachments_present"] = False
    return payload


def _attachment_transport_entry(rendered_block: dict[str, Any]) -> dict[str, Any] | None:
    metadata = rendered_block.get("metadata")
    if not isinstance(metadata, dict):
        return None
    if metadata.get("source_kind") != "attachment":
        return None
    return {
        "attachment_id": str(rendered_block.get("id") or rendered_block.get("source") or ""),
        "attachment_kind": str(metadata.get("attachment_kind") or "generic_attachment"),
        "source": str(rendered_block.get("source") or ""),
        "zone": str(rendered_block.get("zone") or ""),
        "replaceable": bool(rendered_block.get("replaceable", False)),
        "revision": str(metadata.get("digest") or _attachment_block_revision(rendered_block)),
        "content": str(rendered_block.get("content") or ""),
        "state_payload": rendered_block.get("state_payload"),
        "metadata": metadata,
    }


def _attachment_block_revision(rendered_block: dict[str, Any]) -> str:
    payload = {
        "id": rendered_block.get("id"),
        "source": rendered_block.get("source"),
        "content": rendered_block.get("content"),
        "state_payload": rendered_block.get("state_payload"),
        "metadata": rendered_block.get("metadata"),
        "replaceable": rendered_block.get("replaceable"),
    }
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


def _attachment_revision_digest(attachments: list[dict[str, Any]]) -> str:
    canonical = json.dumps(
        [
            {
                "attachment_id": attachment.get("attachment_id"),
                "attachment_kind": attachment.get("attachment_kind"),
                "revision": attachment.get("revision"),
            }
            for attachment in attachments
        ],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()
