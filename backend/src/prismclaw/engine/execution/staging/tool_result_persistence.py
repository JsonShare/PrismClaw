"""Tool result disk persistence.

When a tool result exceeds its inline budget, the full text content is
written to a ``.txt`` file **inside the conversation's working directory**
under a ``tool_results`` subdirectory, and replaced inline by a
``<persisted-output>`` reference the model can read back via the Read
tool.

This module owns **only** persistence. The decision of *whether* to
persist is made upstream (see
``compression/result_compaction.py::_materialize_single_result``).

Design constraints (refactor 2026-07-24):
- Text-dominant results are persisted as clean ``.txt`` (model reads back
  real text, not JSON noise). Pure-image results are NOT persisted — the
  caller falls back to inline truncation.
- Storage lives under ``{working_dir}/tool_results`` so persisted files
  sit next to the workspace the agent operated on. A resumed session can
  still read them back because they are not in ``/tmp``.
- References carry a **workspace-relative** path (``tool_results/tr_*.txt``)
  rather than an absolute one: it shortens what the model must copy verbatim
  and the Read tool resolves it against the workspace root anyway.
- Writes are offloaded to a thread so they never block the event loop.
- No record dataclass, no legacy helpers — only the live API.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

from prismclaw.schema.agent.data_types import ContentBlock, TextBlock

logger = logging.getLogger(__name__)

PERSISTED_OUTPUT_TAG = "<persisted-output>"
DEFAULT_PREVIEW_CHARS = 500
TOOL_RESULTS_SUBDIR = "tool_results"


def _safe_segment(value: str) -> str:
    """Sanitize an id segment so it cannot escape the storage directory."""
    return value.replace("/", "_").replace("\\", "_").replace("..", "_").replace(":", "_")


def _tool_results_dir(working_dir: str | Path) -> Path:
    return Path(working_dir) / TOOL_RESULTS_SUBDIR


# ── Content → text ────────────────────────────────────────────────────


def _content_to_text(content: list[ContentBlock]) -> str | None:
    """Flatten text blocks into a single string.

    Returns ``None`` when there is no text to persist (e.g. an empty
    result or a pure-image result), signalling the caller to fall back to
    inline truncation instead of persisting.
    """
    if not content:
        return None
    parts = [b.text for b in content if isinstance(b, TextBlock)]
    if not parts:
        return None
    return "\n".join(parts)


# ── Reference text ────────────────────────────────────────────────────


def _build_reference_text(*, tool_use_id: str, text: str, path: Path) -> str:
    # 引用发工作区相对路径（``tool_results/tr_<id>_<hash>.txt``），不发绝对路径：
    # 模型必须逐字抄回这段路径才能读回全文，绝对路径前缀长、随机段（call_id +
    # 内容哈希）多，实测出现过抄错一位十六进制而 ``read: file not found``
    # （web:admin 会话 2026-10-20）。相对路径由读工具按工作区根解析，等效可达。
    relative_path = f"{TOOL_RESULTS_SUBDIR}/{path.name}"
    preview = text[:DEFAULT_PREVIEW_CHARS]
    return (
        f"{PERSISTED_OUTPUT_TAG}\n"
        f"tool_use_id: {tool_use_id}\n"
        f"size: {len(text)} chars\n"
        f"preview: {preview}\n"
        f"[完整输出已归档至：{relative_path}] 请用 read 工具查看完整内容。\n"
    )


# ── Core write (blocking, runs in a thread) ──────────────────────────


def _persist_core(
    *,
    tool_use_id: str,
    text: str,
    reason: str,
    working_dir: str | Path,
    tool_name: str,
) -> str | None:
    try:
        results_dir = _tool_results_dir(working_dir)
        results_dir.mkdir(parents=True, exist_ok=True)
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        filename = f"tr_{_safe_segment(tool_use_id)}_{content_hash}.txt"
        persist_path = results_dir / filename
        # Persist the FULL content (reference implementation parity): truncation only
        # happens to the inline reference, never to the on-disk file, so a
        # resumed/Read-back session can recover the complete result.
        persist_path.write_text(text, encoding="utf-8")
        reference = _build_reference_text(
            tool_use_id=tool_use_id, text=text, path=persist_path
        )
        logger.info(
            "persisted tool result: id=%s reason=%s path=%s",
            tool_use_id,
            reason,
            persist_path,
        )
        return reference
    except OSError as exc:
        logger.warning("tool result persist failed: id=%s err=%s", tool_use_id, exc)
        return None


# ── Public API ────────────────────────────────────────────────────────


async def persist_tool_result(
    *,
    tool_use_id: str,
    content: list[ContentBlock],
    reason: str,
    working_dir: str | Path,
    tool_name: str = "",
) -> str | None:
    """Persist a tool result to disk and return the inline reference text.

    Returns ``None`` when there is nothing to persist (pure-image result),
    ``working_dir`` is unavailable, or the write fails — the caller should
    fall back to inline truncation. The write is offloaded to a worker
    thread so it never blocks the event loop.
    """
    if working_dir is None:
        return None
    text = _content_to_text(content)
    if text is None:
        return None
    return await asyncio.to_thread(
        _persist_core,
        tool_use_id=tool_use_id,
        text=text,
        reason=reason,
        working_dir=working_dir,
        tool_name=tool_name,
    )


def persist_tool_result_sync(
    *,
    tool_use_id: str,
    content: list[ContentBlock],
    reason: str,
    working_dir: str | Path,
    tool_name: str = "",
) -> str | None:
    """Synchronous variant for the offline (non-streaming) compaction path."""
    if working_dir is None:
        return None
    text = _content_to_text(content)
    if text is None:
        return None
    return _persist_core(
        tool_use_id=tool_use_id,
        text=text,
        reason=reason,
        working_dir=working_dir,
        tool_name=tool_name,
    )


def load_persisted_text(path: str) -> str | None:
    """Load the persisted text of a tool result from disk (used by Read)."""
    p = Path(path)
    if not p.exists():
        logger.warning("persisted file missing: %s", path)
        return None
    try:
        return p.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("persisted file read failed: %s err=%s", path, exc)
        return None


def is_persisted_content(content: object) -> bool:
    """Whether content has already been replaced by a persisted reference.

    Accepts a raw string or a ``list[ContentBlock]`` (the forms a tool
    result's content takes at the call sites that matter — microcompact
    receives ``MessageEntry.content`` as ``list[ContentBlock]``).
    """
    if isinstance(content, str):
        return content.startswith(PERSISTED_OUTPUT_TAG)
    if isinstance(content, list):
        for block in content:
            if isinstance(block, TextBlock) and block.text.startswith(
                PERSISTED_OUTPUT_TAG
            ):
                return True
    return False


# ── Cleanup ───────────────────────────────────────────────────────────


async def cleanup_working_dir(working_dir: str | Path) -> int:
    """Remove all persisted files for a working directory (best-effort)."""

    def _rm() -> int:
        d = _tool_results_dir(working_dir)
        if not d.exists():
            return 0
        removed = 0
        for p in d.iterdir():
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
        return removed

    return await asyncio.to_thread(_rm)

