"""Pipeline-stage hard truncation for tool output.

Execution order: tool.execute() → output_bound (hard truncation) →
output_compressor (semantic compression).

output_bound enforces absolute size limits (lines / bytes). Content that
exceeds the limit is persisted to a spill file on disk so the model can
read it back via the read tool if needed. This is the hard cap; the
subsequent output_compressor does lossless/lossy semantic reduction
*within* whatever output_bound left intact.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class OutputBoundConfig:
    max_lines: int = 2000
    max_bytes: int = 50 * 1024
    preview_bytes: int = 2000
    spill_dir: Path | None = None


def output_bound(
    text: str,
    *,
    config: OutputBoundConfig,
    tool_name: str = "",
) -> tuple[str, dict[str, Any]]:
    """Truncate text to max_lines / max_bytes.

    Returns (truncated_text, extra_metadata). The extra_metadata dict is
    meant to be merged into the tool's own metadata, with tool metadata
    taking priority on key conflicts.
    """
    if not text:
        return text, {}

    extra_meta: dict[str, Any] = {}
    original_lines = text.splitlines(keepends=True)
    original_line_count = len(original_lines)

    # Phase 1: line-based truncation
    truncated_lines = 0
    if original_line_count > config.max_lines:
        kept = original_lines[: config.max_lines]
        truncated_lines = original_line_count - config.max_lines
        text = "".join(kept)

    # Phase 2: byte-based truncation
    encoded = text.encode("utf-8", errors="replace")
    byte_truncated = False
    if len(encoded) > config.max_bytes:
        # Decode back up to max_bytes, avoiding partial multi-byte chars
        truncated_encoded = encoded[: config.max_bytes]
        try:
            text = truncated_encoded.decode("utf-8", errors="ignore")
        except Exception:
            text = truncated_encoded.decode("utf-8", errors="replace")
        byte_truncated = True

    was_truncated = truncated_lines > 0 or byte_truncated
    if not was_truncated:
        return text, {}

    # Persist overflow to spill file
    if config.spill_dir is not None:
        try:
            config.spill_dir.mkdir(parents=True, exist_ok=True)
            import time
            stamp = int(time.monotonic_ns())
            safe_name = tool_name.replace("/", "_").replace(" ", "_") if tool_name else "tool"
            spill_path = config.spill_dir / f"{safe_name}_{stamp}_overflow.log"
            spill_path.write_text(
                "".join(original_lines) if original_lines else text,
                encoding="utf-8",
                errors="replace",
            )
            extra_meta["spill_path"] = str(spill_path)
        except OSError:
            pass  # Spill failure is non-fatal; truncation still works

    if truncated_lines > 0:
        extra_meta["truncated_lines"] = truncated_lines
        text += f"\n[… 另有 {truncated_lines} 行被截断（使用 read 工具查看完整输出）]"

    if byte_truncated and "spill_path" not in extra_meta:
        text += "\n[输出因字节上限被截断]"

    return text, extra_meta
