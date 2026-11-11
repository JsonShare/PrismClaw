from __future__ import annotations

from prismclaw.schema.content.context import ContextBlock


def _block_from_text(
    *,
    name: str,
    text: str,
    zone: str,
    truncatable: bool,
    cacheable: bool,
    priority: str = "medium",
    stability: str | None = None,
    replaceable: bool = False,
    metadata: dict[str, object] | None = None,
    block_id: str | None = None,
) -> ContextBlock:
    return ContextBlock(
        id=block_id or name,
        zone=zone,  # type: ignore[arg-type]
        source=name,
        content=text,
        stability=(stability or ("stable" if zone == "static" else "volatile")),  # type: ignore[arg-type]
        priority=priority,  # type: ignore[arg-type]
        cacheable=cacheable,
        truncatable=truncatable,
        replaceable=replaceable,
        metadata=dict(metadata or {}),
    )
