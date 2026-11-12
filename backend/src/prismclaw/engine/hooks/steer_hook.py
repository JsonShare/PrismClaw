from __future__ import annotations

import logging

from prismclaw.schema.agent.run_control_types import RunControl
from prismclaw.engine.hooks import (
    CompactionContext,
    PromptBuildContext,
    PromptBuildResult,
    ResponseObservation,
)
from prismclaw.engine.utils.xml import xml_escape
from prismclaw.schema.content.compaction import CompactResult

logger = logging.getLogger(__name__)

# ── 重叠审计 ──
# 注入内容: <user_steer>（运行中调整指令）+ <user_sidebar>（附带问题）
# 注入时机: append 侧，仅在 RunControl.pending_steers 非空时触发
# 与 L1 的重叠关系: 无。L1 frozen/per-turn 没有 steer / sidebar section。
# 保留理由: 唯一承载运行时用户指令覆盖的通道，不可替代。

_SIDEBAR_INSTRUCTION = "（请先简要回答这些附带问题，然后回到主任务。）"

_STEER_HEADER = (
    "重要：用户已通过 /steer 发出运行中的调整指令。"
    "这些指令会覆盖主用户消息中任何冲突的方向。"
    "即使它们与用户最初的要求相矛盾，你也必须遵循。"
)


class SteerHook:
    """Drains RunControl.pending_steers each iteration and injects XML into the per-turn suffix.

    Captures RunControl via on_run_start(session_id, control) — the existing
    AgentHook extension point. Drains via swap-idiom for asyncio atomicity.
    Applies xml_escape to prevent user-supplied text from breaking out of the
    <user_steer> / <user_sidebar> blocks.
    """

    def __init__(self) -> None:
        self._rc_by_session: dict[str, RunControl] = {}

    async def on_run_start(self, session_id: str, control: RunControl) -> None:
        self._rc_by_session[session_id] = control
        control.pending_steers.clear()

    async def on_run_end(self, session_id: str, terminated_by: str) -> None:
        self._rc_by_session.pop(session_id, None)

    async def before_prompt_build(self, context: PromptBuildContext) -> PromptBuildResult | None:
        rc = self._rc_by_session.get(context.session_id)
        if rc is None or not rc.pending_steers:
            return None

        msgs, rc.pending_steers = rc.pending_steers, []

        try:
            steers = [m for m in msgs if m.kind == "steer"]
            sidebars = [m for m in msgs if m.kind == "sidebar"]

            parts: list[str] = []
            if steers:
                lines = ["<user_steer>", _STEER_HEADER]
                for m in steers:
                    lines.append(f"- {xml_escape(m.text)}")
                lines.append("</user_steer>")
                parts.append("\n".join(lines))
            if sidebars:
                lines = ["<user_sidebar>"]
                for m in sidebars:
                    lines.append(f"- {xml_escape(m.text)}")
                lines.append("</user_sidebar>")
                lines.append(_SIDEBAR_INSTRUCTION)
                parts.append("\n".join(lines))

            if not parts:
                return None
            return PromptBuildResult(append="\n\n".join(parts))
        except Exception:
            logger.exception("SteerHook.before_prompt_build rendering failed")
            rc.pending_steers = msgs + rc.pending_steers
            return None

    async def after_response(self, observation: ResponseObservation) -> None:
        return None

    async def before_compaction(self, context: CompactionContext) -> None:
        return None

    async def after_compaction(self, context: CompactionContext, result: CompactResult) -> None:
        return None
