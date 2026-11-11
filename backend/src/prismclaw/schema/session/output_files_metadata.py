"""交付物文件 ↔ 消息气泡绑定（主链路落库的单一真源）。

GET /api/sessions/{id}/messages 只从 OUTPUT_FILES CustomEntry 回填
output_files 元数据，前端据此渲染交付物文件卡片。定时任务已复用
chat_runner._run_chat 主链路，与普通 WebSocket 会话走同一段落库代码；
这里仍是唯一写入点——否则文件明明写在 outputs/，打开会话却看不到卡片。
"""

from __future__ import annotations

import logging

from prismclaw.schema.session import (
    OUTPUT_FILES_CUSTOM_TYPE,
    CustomEntry,
    generate_entry_id,
    now_iso,
)
from prismclaw.schema.session.event_logger import SessionEventLogger
from prismclaw.schema.storage.storage_types import SessionStore

logger = logging.getLogger(__name__)
_event_logger = SessionEventLogger()


async def persist_output_files_metadata(
    session_store: SessionStore | None,
    *,
    session_id: str,
    message_id: str,
    files: list[str],
) -> None:
    """把交付物路径列表绑定到某条 assistant 消息（幂等于空输入）。"""
    if session_store is None or not message_id:
        return
    seen: set[str] = set()
    normalized: list[str] = []
    for f in files:
        if isinstance(f, str) and f.strip() and f.strip() not in seen:
            seen.add(f.strip())
            normalized.append(f.strip())
    if not normalized:
        return
    try:
        tree = await session_store.load(session_id)
    except Exception:
        logger.warning("web.outputs.persist.load_failed", exc_info=True)
        return
    if tree is None or message_id not in tree.entries:
        return
    entry = CustomEntry(
        id=generate_entry_id(tree.all_entry_ids()),
        parent_id=tree.leaf_id,
        timestamp=now_iso(),
        custom_type=OUTPUT_FILES_CUSTOM_TYPE,
        data={"message_id": message_id, "files": normalized},
    )
    try:
        event = _event_logger.entry_updated(session_id, entry, leaf_id=entry.id)
        await session_store.commit_events([event])
    except Exception:
        logger.warning("web.outputs.persist.append_failed", exc_info=True)
