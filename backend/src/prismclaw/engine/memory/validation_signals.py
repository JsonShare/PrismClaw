"""注入→验证信号的最小簿记（SDD D9/T1-5 闭环修复）。

根因：AttributionRecorder 只在 ``attribution_enabled=True`` 时装配
（runner_assembly L486-L492），生产默认 False 时：验证信号（T1-5 路径 B）
与 use_count 回写（路径 A）的生产者整体失活——curator 毕业在无归因环境
下断链。本 ledger 独立于归因开关，只承担两项最小职责：

1. retriever 注入后记录本回合注入的条目 ids；
2. aftermath 回合尾取走 pending、产出 task-outcome 信号写库并回写 use_count。

不含 reference log 与用户纠错检测——那是 AttributionRecorder 的完整职责；
此处分层边界：ledger 是最小证据环，不做任何 D14 开关语义的变更。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ValidationSignalLedger:
    """Per-session injection bookkeeping + signal flush, decoupled from B1."""

    def __init__(self, memory_store: Any) -> None:
        self._memory_store = memory_store
        self._pending: dict[str, list[str]] = {}

    def note_injections(self, session_id: str, entry_ids: list[str]) -> None:
        if entry_ids:
            # 去重保序；仅保留本回合最近一次注入面。
            self._pending[session_id] = list(dict.fromkeys(entry_ids))

    def take_pending(self, session_id: str) -> list[str]:
        return self._pending.pop(session_id, [])

    async def record_events(self, session_key: str, events: list[dict[str, Any]]) -> int:
        if not events:
            return 0
        try:
            return await self._memory_store.record_validation_events(session_key, events)
        except Exception:
            logger.warning("validation ledger signal flush failed", exc_info=True)
            return 0

    async def bump_use_counts(self, session_key: str, entry_ids: list[str]) -> int:
        if not entry_ids:
            return 0
        try:
            return await self._memory_store.writeback_use_counts(session_key, entry_ids)
        except Exception:
            logger.warning("validation ledger use-count bump failed", exc_info=True)
            return 0