from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from prismclaw.schema.session import SessionTree

from prismclaw.schema.llm.request_mode import RequestMode
from prismclaw.schema.content.context import TaskProfile


@dataclass(frozen=True)
class TaskResponsePolicy:
    reasoning_retry_limit: int
    empty_retry_limit: int
    tool_first: bool = False
    strict_verification: bool = False


@dataclass(frozen=True)
class TaskCompactionPolicy:
    pretrim_trigger_tokens: int
    threshold_override: float
    keep_recent_tokens: int


@dataclass(frozen=True)
class TaskRuntimeStrategy:
    turn_mode: RequestMode | None
    verification_mode: Literal["light", "strict"] | None
    strategies: tuple[Literal["analysis", "execution", "tool"], ...]


class TaskProfileService:
    def resolve_request_mode(
        self,
        *,
        tree: "SessionTree",
        run_seconds: int = 300,
        channel: str = "cli",
    ) -> RequestMode | None:
        if run_seconds < 30 and channel == "cli":
            return "fast_path"
            
        prior_entries = []
        for entry_id in tree.order[:-1]:
            entry = tree.entries.get(entry_id)
            if entry is None:
                continue
            if entry.type in {"message", "compaction"}:
                prior_entries.append(entry)
        return "cold_start" if not prior_entries else "follow_up"

    def retrieval_limits(
        self,
        task_profile: TaskProfile,
        *,
        l2_quota: int,
        l3_quota: int,
        archive_limit: int,
        request_mode: RequestMode | None = None,
    ) -> tuple[int, int, int]:
        l2_limit = max(0, l2_quota)
        l3_limit = max(0, l3_quota)
        archive_max = max(0, archive_limit)

        if task_profile.requires_grounding:
            l2_limit += 2
            if l3_limit > 0:
                l3_limit = max(1, l3_limit - 1)
        elif task_profile.prefers_tooling:
            l3_limit += 2
            if l2_limit > 0:
                l2_limit = max(1, l2_limit - 1)
            archive_max = max(0, archive_max - 1)

        if task_profile.requires_grounding:
            l2_limit += 1
            archive_max = min(archive_max, 1 if archive_limit > 0 else 0)

        if request_mode == "cold_start":
            archive_max = min(archive_max, 1 if archive_limit > 0 else 0)
        elif request_mode == "follow_up":
            l2_floor = 1 if l2_quota > 0 else 0
            l3_floor = 1 if l3_quota > 0 and task_profile.prefers_tooling else 0
            l2_limit = max(l2_floor, l2_limit - 1)
            l3_limit = max(l3_floor, l3_limit - 1)
            archive_max = min(archive_max, 1 if archive_limit > 0 else 0)

        return l2_limit, l3_limit, archive_max

    def runtime_strategy(
        self,
        task_profile: TaskProfile,
        *,
        request_mode: RequestMode | None,
    ) -> TaskRuntimeStrategy:
        verification_mode = task_profile.verification_mode()
        strategies: list[Literal["analysis", "execution", "tool"]] = []

        if task_profile.requires_grounding:
            strategies.append("analysis")
        if task_profile.prefers_tooling:
            strategies.append("execution")
            strategies.append("tool")

        return TaskRuntimeStrategy(
            turn_mode=request_mode,
            verification_mode=None if verification_mode == "none" else verification_mode,
            strategies=tuple(strategies),
        )

    def runtime_strategy_notes(
        self,
        task_profile: TaskProfile,
        *,
        request_mode: RequestMode | None,
    ) -> list[str]:
        notes: list[str] = []
        runtime_strategy = self.runtime_strategy(task_profile, request_mode=request_mode)

        if runtime_strategy.turn_mode == "cold_start":
            notes.append(
                "轮次模式：cold_start。请以最少的易变上下文建立整体认知，避免不必要的大规模检索。"
            )
        elif runtime_strategy.turn_mode == "follow_up":
            notes.append(
                "轮次模式：follow_up。保持稳定前缀不变，新增的动态上下文按增量方式添加。"
            )

        if runtime_strategy.verification_mode == "strict":
            notes.append(
                "校验模式：严格。下结论前优先使用最新的工作区、附件与近期记忆证据。"
            )
        elif runtime_strategy.verification_mode == "light":
            notes.append(
                "校验模式：轻量。收尾前执行最小但有效的检查。"
            )

        if "analysis" in runtime_strategy.strategies:
            notes.append(
                "分析策略：优先依据事实性记忆，并从证据出发解释结论。"
            )
        if "execution" in runtime_strategy.strategies:
            notes.append(
                "执行策略：优先使用流程性上下文与工具推进，而不是只做解释。"
            )

        if "tool" in runtime_strategy.strategies:
            notes.append(
                "工具策略：当工具能降低不确定性或验证状态时，主动使用工具。"
            )

        return notes

    def response_policy(self, task_profile: TaskProfile, *, config: Any) -> TaskResponsePolicy:
        reasoning_limit = max(0, int(config.retry.reasoning_only_limit))
        empty_limit = max(0, int(config.retry.empty_response_limit))
        tool_first = task_profile.prefers_tooling and not task_profile.has_material_type("tool_result")
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
        elif task_profile.requires_grounding and (
            task_profile.verification_mode() == "strict"
        ):
            ratio = max(0.65, ratio - 0.1)
            keep_recent_tokens = int(keep_recent_tokens * 1.1)

        ratio = round(ratio, 2)
        trigger_tokens = int(history_budget * ratio) if history_budget > 0 else 0
        return TaskCompactionPolicy(
            pretrim_trigger_tokens=trigger_tokens,
            threshold_override=ratio,
            keep_recent_tokens=keep_recent_tokens,
        )
