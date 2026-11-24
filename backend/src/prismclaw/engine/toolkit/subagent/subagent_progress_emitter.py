"""V1.5 Phase 2 SubagentProgressEmitter — coalesce sub-agent progress updates.

Owned by `_run_subagent_parallel_bucket` for the duration of a fan-out batch.
Sub-agents call ``update(item)`` at tool_call boundaries; a 250ms flush loop
coalesces high-frequency updates into one ``SubagentProgressBatch`` event
per window and pushes it to the channel-supplied sink callback.

The sink bypasses the runner's async generator (Decision 9) — runner is
blocked on ``await execute_tool_calls`` during fan-out and cannot ``yield``
new events. The channel registers the sink via ``ToolContext.subagent_progress_sink`` at
runner construction time; the emitter calls it directly.

Race-safety contract (Decision 3): ``_dirty`` is cleared BEFORE awaiting
the sink, never after. A concurrent ``update()`` arriving during the await
re-sets ``_dirty=True`` and is captured on the next tick.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from prismclaw.schema.content.streaming import SubagentProgressBatch, SubagentProgressItem

if TYPE_CHECKING:
    from prismclaw.engine.execution.handoff.supervisor import CapExceededInfo

logger = logging.getLogger(__name__)


SubagentProgressSink = Callable[[SubagentProgressBatch], Awaitable[None]]


_DEFAULT_FLUSH_INTERVAL_S = 0.25

# 每个子代理保留的最近动作条数上限。它只是「还活着」的证据，不是执行日志——
# 再多就超出运行态展示的用途，应该去看完整轨迹了。
_MAX_RECENT_ACTIVITY = 5


class SubagentProgressEmitter:
    def __init__(
        self,
        *,
        progress_batch_id: str,
        sink: SubagentProgressSink | None,
        flush_interval_s: float = _DEFAULT_FLUSH_INTERVAL_S,
    ) -> None:
        self._progress_batch_id = progress_batch_id
        self._sink = sink
        self._flush_interval_s = flush_interval_s
        self._states: dict[str, SubagentProgressItem] = {}
        self._dirty = False
        self._seq = 0
        self._closed = False

    def update(self, item: SubagentProgressItem) -> None:
        if self._closed:
            return
        prev = self._states.get(item.sub_session_id)
        activity = list(prev.recent_activity) if prev is not None else []
        # 只在「工具身份变化」时追一条：同工具同轮次的重复 update 是高频噪声，
        # 同工具跨轮次（prev.turn 不同）才是新动作。
        if item.current_tool and (
            prev is None or prev.current_tool != item.current_tool or prev.turn != item.turn
        ):
            label = f"{item.current_tool} {item.current_tool_summary or ''}".strip()
            if not activity or activity[-1] != label:
                activity.append(label)
        if len(activity) > _MAX_RECENT_ACTIVITY:
            activity = activity[-_MAX_RECENT_ACTIVITY:]
        self._states[item.sub_session_id] = item.model_copy(update={"recent_activity": activity})
        self._dirty = True

    async def _flush_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._flush_interval_s)
                await self._maybe_flush()
        except asyncio.CancelledError:
            return

    async def flush_final(self) -> None:
        await self._maybe_flush(force=True)
        self._closed = True

    async def _maybe_flush(self, *, force: bool = False) -> None:
        if not self._dirty and not force:
            return
        if self._sink is None:
            self._dirty = False
            return
        self._dirty = False
        items_snapshot = list(self._states.values())
        if not items_snapshot and not force:
            return
        self._seq += 1
        batch = SubagentProgressBatch(
            progress_batch_id=self._progress_batch_id,
            batch_seq=self._seq,
            items=items_snapshot,
        )
        try:
            await self._sink(batch)
        except Exception:
            logger.warning("subagent.progress.sink_failed", exc_info=True)


__all__ = ["SubagentProgressEmitter", "SubagentProgressSink"]


async def _emit_cap_exceeded_synthetic_progress(
    *,
    sink: SubagentProgressSink | None,
    cap_info_by_call: dict[str, CapExceededInfo],
) -> None:
    import uuid as _uuid

    # 本地的包级重导入已删除：SubagentProgressBatch / SubagentProgressItem 均在
    # 模块顶部从 prismclaw.schema.content.streaming 导入，而 schema.agent 的惰性
    # 门面表只收录 SubagentProgressBatch、没有 SubagentProgressItem，此处的包级
    # 导入会直接 ImportError（覆盖掉顶部正确的绑定）。

    if sink is None:
        return

    items: list[SubagentProgressItem] = []
    for call_id, cap_info in cap_info_by_call.items():
        synthetic_name = f"$synthetic:chain_cap_exceeded:{cap_info.requested_target}"
        items.append(
            SubagentProgressItem(
                sub_session_id=f"$synthetic#{call_id}",
                subagent_name=synthetic_name,
                turn=0,
                max_turns=1,
                partial_status="failed",
                current_tool=None,
                duration_so_far=0.0,
                handoff_chain_id=cap_info.handoff_chain_id,
                from_subagent=cap_info.source_subagent,
            )
        )
    if not items:
        return
    batch = SubagentProgressBatch(
        progress_batch_id=f"$synthetic-cap-{_uuid.uuid4().hex[:12]}",
        batch_seq=1,
        items=items,
    )
    await sink(batch)
