from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from prismclaw.schema.config.config import AgentRunConfig
    from prismclaw.schema.content.context import TaskProfile


@dataclass(frozen=True)
class TaskResponsePolicy:
    reasoning_retry_limit: int
    empty_retry_limit: int
    tool_first: bool = False
    strict_verification: bool = False
    # 文本化工具调用（DSML 标记落进 content）的重试上限。与 empty 同族：
    # 本轮没有真正执行工具、也没有可见答复，属于响应质量问题。
    tool_markup_retry_limit: int = 1


@dataclass(frozen=True)
class TaskCompactionPolicy:
    pretrim_trigger_tokens: int
    threshold_override: float
    keep_recent_tokens: int


@dataclass(frozen=True)
class TaskRuntimeStrategy:
    verification_mode: Literal["light", "strict"] | None
    strategies: tuple[Literal["analysis", "execution", "tool"], ...]


class TaskProfileService:
    def runtime_strategy(self, task_profile: TaskProfile) -> TaskRuntimeStrategy:
        verification_mode = task_profile.verification_mode()
        strategies: list[Literal["analysis", "execution", "tool"]] = []

        if task_profile.requires_grounding:
            strategies.append("analysis")
        if task_profile.prefers_tooling:
            strategies.append("execution")
            strategies.append("tool")

        return TaskRuntimeStrategy(
            verification_mode=None if verification_mode == "none" else verification_mode,
            strategies=tuple(strategies),
        )

    def runtime_strategy_notes(self, task_profile: TaskProfile) -> list[str]:
        notes: list[str] = []
        runtime_strategy = self.runtime_strategy(task_profile)

        if runtime_strategy.verification_mode == "strict":
            notes.append("校验模式：严格。下结论前优先使用最新的工作区、附件与近期记忆证据。")
        elif runtime_strategy.verification_mode == "light":
            notes.append("校验模式：轻量。收尾前执行最小但有效的检查。")

        if "analysis" in runtime_strategy.strategies:
            notes.append("分析策略：优先依据事实性记忆，并从证据出发解释结论。")
        if "execution" in runtime_strategy.strategies:
            notes.append("执行策略：优先使用流程性上下文与工具推进，而不是只做解释。")

        if "tool" in runtime_strategy.strategies:
            notes.append("工具策略：当工具能降低不确定性或验证状态时，主动使用工具。")

        return notes

    def response_policy(
        self, task_profile: TaskProfile, *, config: AgentRunConfig
    ) -> TaskResponsePolicy:
        reasoning_limit = max(0, int(config.retry.reasoning_only_limit))
        empty_limit = max(0, int(config.retry.empty_response_limit))
        tool_first = task_profile.prefers_tooling and not task_profile.has_material_type(
            "tool_result"
        )
        strict_verification = task_profile.verification_mode() == "strict"

        if tool_first:
            reasoning_limit = max(0, reasoning_limit - 1)
        if strict_verification:
            reasoning_limit = max(0, reasoning_limit - 1)

        return TaskResponsePolicy(
            reasoning_retry_limit=reasoning_limit,
            empty_retry_limit=empty_limit,
            tool_first=tool_first,
            strict_verification=strict_verification,
            tool_markup_retry_limit=empty_limit,
        )

    def compaction_policy(
        self,
        task_profile: TaskProfile,
        *,
        history_budget: int,
        history_threshold: float,
        keep_recent_tokens: int,
    ) -> TaskCompactionPolicy:
        history_budget = max(0, int(history_budget))
        ratio = min(1.0, max(0.5, float(history_threshold)))
        keep_recent_tokens = max(0, int(keep_recent_tokens))

        if task_profile.prefers_tooling:
            ratio = min(1.0, ratio + 0.1)
            keep_recent_tokens = int(keep_recent_tokens * 1.25)
        elif task_profile.requires_grounding and (task_profile.verification_mode() == "strict"):
            ratio = max(0.65, ratio - 0.1)
            keep_recent_tokens = int(keep_recent_tokens * 1.1)

        ratio = round(ratio, 2)
        trigger_tokens = int(history_budget * ratio) if history_budget > 0 else 0
        return TaskCompactionPolicy(
            pretrim_trigger_tokens=trigger_tokens,
            threshold_override=ratio,
            keep_recent_tokens=keep_recent_tokens,
        )
