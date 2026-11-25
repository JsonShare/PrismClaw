from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from prismclaw.schema.session import (
    FILE_CHANGES_CUSTOM_TYPE,
    REFERENCE_SOURCES_CUSTOM_TYPE,
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
    session_root: Path,
) -> list[str]:
    """校验 Agent present_files 的输出路径，返回 ``outbox/<rel>`` 形式的会话相对键。

    返回键与 ArtifactScanner 一致，便于和自动扫描结果求交集；写回消息
    metadata 前由 ``_filter_existing_output_files`` 统一补 ``sessions/<sid>`` 前缀。
    """
    files = arguments.get("files", [])
    if not isinstance(files, list):
        return []
    if workspace_root is None:
        return []

    outbox_root = (session_root.resolve() / "outbox").resolve()

    validated: list[str] = []
    for raw in files:
        if not isinstance(raw, str) or not raw.strip():
            continue
        normalized = raw.strip().replace("\\", "/")

        full = (outbox_root.parent / normalized).resolve()
        if not full.is_file():
            # 也接受显式的 sessions/<sid>/outbox/... 形式
            alt = (workspace_root / normalized).resolve()
            if alt.is_file():
                full = alt
        try:
            rel = full.relative_to(outbox_root)
        except ValueError:
            continue

        if not full.is_file():
            logger.warning("web.outbox.file_not_found: %s", str(full))
            continue

        validated.append(f"outbox/{rel.as_posix()}")
    return validated


def _filter_existing_output_files(
    paths: list[str],
    workspace_root: Path | None,
    *,
    session_root: Path,
) -> list[str]:
    """把会话相对键补成 ``sessions/<sid>/outbox/<rel>``，只保留磁盘上存在的文件。"""
    if workspace_root is None:
        return []

    outbox_root = (session_root.resolve() / "outbox").resolve()
    rel_prefix = f"sessions/{session_root.name}/outbox/"

    validated: list[str] = []
    for raw in paths:
        if not isinstance(raw, str) or not raw.strip():
            continue
        normalized = raw.strip().replace("\\", "/")
        full = (outbox_root.parent / normalized).resolve()
        if not full.is_file():
            alt = (workspace_root / normalized).resolve()
            if alt.is_file():
                full = alt
        try:
            rel = full.relative_to(outbox_root)
        except ValueError:
            continue
        if not full.is_file():
            logger.warning("web.outbox.file_missing_at_done: %s", str(full))
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
    if session_store is None:
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


async def _persist_reference_sources_metadata(
    session_store: SessionStore | None,
    *,
    session_id: str,
    message_id: str,
    sources: list[dict[str, Any]],
) -> None:
    """把结构化参考来源挂到该条 assistant 消息上，供刷新后回放。"""
    normalized = [s for s in sources if isinstance(s, dict)]
    if not message_id or not normalized:
        return
    if session_store is None:
        return
    try:
        tree = await session_store.load(session_id)
    except Exception:
        logger.warning("web.reference_sources.persist.load_failed", exc_info=True)
        return
    if tree is None:
        return
    if message_id not in tree.entries:
        return
    # 同一轮重复收尾时不追加第二条：先看已有挂靠条目能否直接覆盖。
    for existing in tree.entries.values():
        if not isinstance(existing, CustomEntry):
            continue
        if existing.custom_type != REFERENCE_SOURCES_CUSTOM_TYPE:
            continue
        if (existing.data or {}).get("message_id") != message_id:
            continue
        updated = CustomEntry(
            id=existing.id,
            parent_id=existing.parent_id,
            timestamp=now_iso(),
            custom_type=REFERENCE_SOURCES_CUSTOM_TYPE,
            data={"message_id": message_id, "sources": normalized},
        )
        try:
            event = _event_logger.entry_updated(session_id, updated, leaf_id=tree.leaf_id)
            await session_store.commit_events([event])
        except Exception:
            logger.warning("web.reference_sources.persist.append_failed", exc_info=True)
        return
    entry = CustomEntry(
        id=generate_entry_id(tree.all_entry_ids()),
        parent_id=tree.leaf_id,
        timestamp=now_iso(),
        custom_type=REFERENCE_SOURCES_CUSTOM_TYPE,
        data={"message_id": message_id, "sources": normalized},
    )
    try:
        event = _event_logger.entry_updated(session_id, entry, leaf_id=entry.id)
        await session_store.commit_events([event])
    except Exception:
        logger.warning("web.reference_sources.persist.append_failed", exc_info=True)
