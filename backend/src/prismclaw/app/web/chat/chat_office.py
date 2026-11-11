from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from prismclaw.schema.session import (
    FILE_CHANGES_CUSTOM_TYPE,
    CustomEntry,
    generate_entry_id,
    now_iso,
)
from prismclaw.schema.session.event_logger import SessionEventLogger
from prismclaw.schema.storage.storage_types import SessionStore

logger = logging.getLogger(__name__)
_event_logger = SessionEventLogger()


def _dedupe_simple(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _extract_presented_paths(
    arguments: dict[str, Any],
    workspace_root: Path | None,
    *,
    session_root: Path | None = None,
) -> list[str]:
    """校验 Agent present_files 的输出路径。

    outputs 现在在 sessions/<sid>/outputs/，所以 outputs_root 从 session_root 算。
    session_root 传了 → 精确匹配 session outputs；不传 → workspace 根回退（兼容）。
    """
    files = arguments.get("files", [])
    if not isinstance(files, list):
        return []
    if workspace_root is None:
        return []

    # outputs_root: session 级优先
    if session_root is not None:
        base_root = session_root.resolve()
        outputs_root = (base_root / "outputs").resolve()
    else:
        base_root = workspace_root.resolve()
        outputs_root = (base_root / "outputs").resolve()
    # 这里返回与 ArtifactScanner 一致的 session 相对键（outputs/<rel>），
    # 便于和自动扫描结果求交集；写回消息 metadata 前由
    # _filter_existing_output_files 统一补上 sessions/<sid> 前缀。

    validated: list[str] = []
    for raw in files:
        if not isinstance(raw, str) or not raw.strip():
            continue
        normalized = raw.strip().replace("\\", "/")

        full = (base_root / normalized).resolve()
        if not full.is_file():
            # present_files 也可能直接收到 sessions/<sid>/outputs/... 形式
            alt = (workspace_root / normalized).resolve()
            if alt.is_file():
                full = alt
        try:
            rel = full.relative_to(outputs_root)
        except ValueError:
            continue

        if not full.is_file():
            logger.warning("web.outputs.file_not_found: %s", str(full))
            continue

        validated.append(f"outputs/{rel.as_posix()}")
    return validated


def _filter_existing_output_files(
    paths: list[str],
    workspace_root: Path | None,
    *,
    session_root: Path | None = None,
) -> list[str]:
    if workspace_root is None:
        return []

    if session_root is not None:
        base_root = session_root.resolve()
        outputs_root = (base_root / "outputs").resolve()
        rel_prefix = f"sessions/{session_root.name}/outputs/"
    else:
        base_root = workspace_root.resolve()
        outputs_root = (base_root / "outputs").resolve()
        rel_prefix = "outputs/"

    validated: list[str] = []
    for raw in paths:
        if not isinstance(raw, str) or not raw.strip():
            continue
        normalized = raw.strip().replace("\\", "/")
        full = (base_root / normalized).resolve()
        if not full.is_file():
            alt = (workspace_root / normalized).resolve()
            if alt.is_file():
                full = alt
        try:
            rel = full.relative_to(outputs_root)
        except ValueError:
            continue
        if not full.is_file():
            logger.warning("web.outputs.file_missing_at_done: %s", str(full))
            continue
        validated.append(f"{rel_prefix}{rel.as_posix()}")
    return _dedupe_simple(validated)


async def _persist_file_changes_metadata(
    session_store: SessionStore | None,
    *,
    session_id: str,
    message_id: str,
    changes: list[dict[str, Any]],
) -> None:
    normalized = [c for c in changes if isinstance(c, dict)]
    if not message_id or not normalized:
        return
    try:
        tree = await session_store.load(session_id)
    except Exception:
        logger.warning("web.file_changes.persist.load_failed", exc_info=True)
        return
    if tree is None:
        return
    if message_id not in tree.entries:
        return
    entry = CustomEntry(
        id=generate_entry_id(tree.all_entry_ids()),
        parent_id=tree.leaf_id,
        timestamp=now_iso(),
        custom_type=FILE_CHANGES_CUSTOM_TYPE,
        data={"message_id": message_id, "changes": normalized},
    )
    try:
        event = _event_logger.entry_updated(session_id, entry, leaf_id=entry.id)
        await session_store.commit_events([event])
    except Exception:
        logger.warning("web.file_changes.persist.append_failed", exc_info=True)