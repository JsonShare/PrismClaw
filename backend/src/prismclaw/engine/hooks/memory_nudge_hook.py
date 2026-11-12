from __future__ import annotations

from prismclaw.engine.hooks import (
    CompactionContext,
    PromptBuildContext,
    PromptBuildResult,
    ResponseObservation,
)
from prismclaw.schema.content.compaction import CompactResult

# ── 重叠审计（与 L1 frozen/per-turn section 的关系） ──
#
# 注入内容: <nudge> 周期性提醒使用 memory 工具持久化事实/工作流
# 注入时机: append 侧，每 interval 轮触发一次
# 与 L1 的重叠关系:
#   - _GOAL_POLICY 有"能用工具验证的疑问不要猜测"——是"遇到疑问时用工具"的通用原则
#   - memory_nudge 是"主动主动持久化"的周期性提醒，语义不同，非冗余
#   - runtime_context_section 的 TaskSpec 可能声明了任务上下文，但不含"记忆持久化"引导
# 保留理由: 周期性提醒与 GOAL_POLICY 的通用原则互补，不重复

_NUDGE_TEXT = (
    "<nudge>可考虑使用 `memory` 工具（action=\"save\"）将重要事实或工作流"
    "持久化，供后续会话使用。</nudge>"
)


class MemoryNudgeHook:
    """AgentHook that periodically reminds the agent to memorize."""

    def __init__(self, interval: int = 10) -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        self._interval = interval
        self._counts: dict[str, int] = {}

    def reset_counter(self, session_id: str) -> None:
        """Reset (clear) the nudge counter for a session.

        Removes the entry entirely (not just zeroes it) to prevent
        unbounded dict growth over a long-running process.
        """
        self._counts.pop(session_id, None)

    async def before_prompt_build(self, context: PromptBuildContext) -> PromptBuildResult | None:
        session_id = context.session_id
        self._counts[session_id] = self._counts.get(session_id, 0) + 1
        count = self._counts[session_id]
        if count > 0 and count % self._interval == 0:
            return PromptBuildResult(append=_NUDGE_TEXT)
        return None

    async def after_response(self, observation: ResponseObservation) -> None:
        for call in observation.tool_calls:
            fn = (call or {}).get("function") or {}
            name = fn.get("name", "") if isinstance(fn, dict) else ""
            if name == "memory":
                self._counts[observation.session_id] = 0
                return
        return None

    async def before_compaction(self, context: CompactionContext) -> None:
        return None

    async def after_compaction(self, context: CompactionContext, result: CompactResult) -> None:
        return None
