"""Context block disk persistence.

When a ``ContextBlock`` exceeds its inline budget, the full text content is
written to a ``.txt`` file **inside the conversation's working directory**
under a ``context_blocks`` subdirectory, and replaced inline by a
``<context-block-ref>`` reference the model can read back via the Read tool.

This module owns **only** persistence. The decision of *whether* to
persist is made upstream in ``compression/budget_block_helpers.py``.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CONTEXT_BLOCK_REF_TAG = "<context-block-ref>"
CONTEXT_BLOCKS_SUBDIR = "context_blocks"


def _safe_segment(value: str) -> str:
    return value.replace("/", "_").replace("\\", "_").replace("..", "_").replace(":", "_")


def _context_blocks_dir(working_dir: str | Path) -> Path:
    return Path(working_dir) / CONTEXT_BLOCKS_SUBDIR


def _build_reference_text(*, block_id: str, source: str, text: str, path: Path) -> str:
    # 与 tool_result_persistence 同口径：引用发工作区相对路径，读工具按工作区根
    # 解析。模型抄写面积小，避免长绝对路径抄错一位就不可恢复。
    relative_path = f"{CONTEXT_BLOCKS_SUBDIR}/{path.name}"
    preview = text[:500]
    return (
        f"{CONTEXT_BLOCK_REF_TAG}\n"
        f"block_id: {block_id}\n"
        f"source: {source}\n"
        f"size: {len(text)} chars\n"
        f"preview: {preview}\n"
        f"[完整内容已归档至：{relative_path}] 请用 read 工具查看完整内容。\n"
    )


def persist_context_block(
    *,
    block_id: str,
    source: str,
    content: str,
    reason: str,
    working_dir: str | Path,
) -> str | None:
    """Persist a context block to disk and return the inline reference text.

    Returns ``None`` when ``working_dir`` is unavailable or the write fails.
    """
    if not working_dir or not content:
        return None
    try:
        blocks_dir = _context_blocks_dir(working_dir)
        blocks_dir.mkdir(parents=True, exist_ok=True)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
        filename = f"cb_{_safe_segment(block_id)}_{content_hash}.txt"
        persist_path = blocks_dir / filename
        # 同一 block_id 写新内容时，删掉旧版本（glob cb_<block_id>_*.txt 除当前 hash 外的全删）。
        # 原因：block 内容变化会得到不同 content_hash，不清理会让每次预算触发
        # 都留下一个新文件，无限增长。
        _prefix = f"cb_{_safe_segment(block_id)}_"
        for _old in blocks_dir.glob(f"{_prefix}*.txt"):
            if _old.name != filename:
                try:
                    _old.unlink()
                    logger.debug("context block gc: removed stale %s", _old.name)
                except OSError:
                    pass
        # dedup: hash 已编码进文件名，存在即同内容，跳过重复磁盘写 + 日志。
        if not persist_path.exists():
            persist_path.write_text(content, encoding="utf-8")
        reference = _build_reference_text(
            block_id=block_id,
            source=source,
            text=content,
            path=persist_path,
        )
        logger.info(
            "persisted context block: id=%s source=%s reason=%s path=%s",
            block_id,
            source,
            reason,
            persist_path,
        )
        return reference
    except OSError as exc:
        logger.warning("context block persist failed: id=%s err=%s", block_id, exc)
        return None


def is_context_block_ref(text: str) -> bool:
    return text.startswith(CONTEXT_BLOCK_REF_TAG)


def load_persisted_context_block(path: str) -> str | None:
    p = Path(path)
    if not p.exists():
        logger.warning("persisted context block missing: %s", path)
        return None
    try:
        return p.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("persisted context block read failed: %s err=%s", path, exc)
        return None
