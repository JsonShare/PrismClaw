from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from prismclaw.schema.content.context import ContextBlock, TaskProfile
from prismclaw.schema.content.context import ContextPackLayout
from prismclaw.schema.shared.utils import estimate_tokens

if TYPE_CHECKING:
    # 类型注解所需，runtime 不依赖 task_profile。
    from prismclaw.engine.task_profile.service import TaskCompactionPolicy

logger = logging.getLogger(__name__)

# 冻结区超预算时的裁剪顺序（越靠前越先被持久化/丢弃）。
# 原则：先撤可随时重建或按需读回的内容，最后才动"发现面"。
#   - tools / skills 是技能与工具的唯二发现入口：一旦被撤，模型既看不到
#     技能名也看不到 skill_search，插件生态等于对模型不可见——必须最后撤。
#   - l1_snapshot 已移至 per-turn 动态区（SDD D3），不再出现在冻结区。
#   - 名单须与 system_prompt.build_frozen_prompt_sections 的 section.name 一致；
#     "subagents" 曾漏登记，导致该块因落不到名次而永远最后被裁。
#     data_safety 故意不登记：安全约束内容极短，且不应先于任何内容被撤。
SYSTEM_TRUNCATION_PRIORITY = [
    "repository_map",
    "bootstrap",
    "subagents",
    "workspace",
    "invoked_skill_attachments",  # cacheable=False 段，撤掉不影响前面的缓存
    "skills",
    "tools",
]

DYNAMIC_TRUNCATION_PRIORITY = [
    # l1_snapshot 随长期记忆单调增长、内容可按需重读磁盘，动态区里先撤。
    "l1_snapshot",
    "hooks_prepend",
    "hooks_append",
    "memory_archives",
    "memory_procedures",
    "subagent_advisory",
    "attachment_guidance",
    "memory_facts",
    "runtime_context",
]


def truncate_context_block_for_overflow(
    block: ContextBlock,
    *,
    overflow_tokens: int,
    working_dir: str | Path | None = None,
    ports: Any = None,
) -> ContextBlock | None:
    """逐级披露模式：persist → drop。

    当上下文块超出预算时，将完整内容持久化到磁盘并替换为引用，
    模型可通过 Read 工具按需查阅。仅当持久化不可用时才完全丢弃。

    信息零丢失：永远不做部分截断（截断会丢失信息且无法恢复），
    而是通过持久化 + 引用实现"完整内容离线、摘要在线"的逐级披露。

    ports 注入 persist_context_block，避免反向 import。
    ports=None 时跳过持久化直接丢弃。
    """
    content_tokens = estimate_tokens(block.content)
    if content_tokens <= overflow_tokens:
        # Not actually overflowing — no action needed
        return block

    # Path 1: 持久化优先 —— 将完整内容写入磁盘，原位替换为引用
    if working_dir is not None and ports is not None:
        persisted_ref = ports.persist_context_block(
            block_id=block.id,
            source=block.source,
            content=block.content,
            reason="budget_overflow",
            working_dir=working_dir,
        )
        if persisted_ref is not None:
            logger.info(
                "budget_overflow: persisted context block %s (id=%s) → reference",
                block.source,
                block.id,
            )
            return block.model_copy(
                update={
                    "content": persisted_ref,
                    "estimated_tokens": estimate_tokens(persisted_ref),
                    "metadata": {
                        **dict(block.metadata),
                        "persisted": True,
                    },
                }
            )

    # Path 2: 持久化失败 —— 完全丢弃但记录元信息
    logger.warning(
        "budget_overflow: context block %s (id=%s) dropped (persist unavailable, "
        "refusing to truncate — truncation loses information irrecoverably)",
        block.source,
        block.id,
    )
    return None


@dataclass(frozen=True)
class BudgetDecision:
    stage: str
    action: str
    reason: str
    zone: str
    block_id: str | None = None
    source: str | None = None
    before_tokens: int = 0
    after_tokens: int = 0

    def to_metadata_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "action": self.action,
            "reason": self.reason,
            "zone": self.zone,
            "block_id": self.block_id,
            "source": self.source,
            "before_tokens": self.before_tokens,
            "after_tokens": self.after_tokens,
        }


@dataclass
class BudgetAuditTrail:
    decisions: list[BudgetDecision] = field(default_factory=list)

    @classmethod
    def from_decisions(
        cls, decisions: list[BudgetDecision] | tuple[BudgetDecision, ...]
    ) -> "BudgetAuditTrail":
        return cls(decisions=list(decisions))

    @property
    def decision_count(self) -> int:
        return len(self.decisions)

    def stage_counts(self) -> dict[str, int]:
        return _count_by(self.decisions, key=lambda decision: decision.stage)

    def action_counts(self) -> dict[str, int]:
        return _count_by(self.decisions, key=lambda decision: decision.action)

    def reason_counts(self) -> dict[str, int]:
        return _count_by(self.decisions, key=lambda decision: decision.reason)

    def to_metadata_dict(self) -> dict[str, object]:
        return {
            "decision_count": self.decision_count,
            "stage_counts": self.stage_counts(),
            "action_counts": self.action_counts(),
            "reason_counts": self.reason_counts(),
            "decisions": [decision.to_metadata_dict() for decision in self.decisions],
        }

    @classmethod
    def from_metadata(
        cls, data: dict[str, object] | None
    ) -> "BudgetAuditTrail | None":
        if not isinstance(data, dict):
            return None
        raw_decisions = data.get("decisions")
        if not isinstance(raw_decisions, list):
            return None
        decisions: list[BudgetDecision] = []
        for item in raw_decisions:
            if not isinstance(item, dict):
                continue
            stage = item.get("stage")
            action = item.get("action")
            reason = item.get("reason")
            zone = item.get("zone")
            if not all(isinstance(value, str) and value for value in (stage, action, reason, zone)):
                continue
            decisions.append(
                BudgetDecision(
                    stage=stage,
                    action=action,
                    reason=reason,
                    zone=zone,
                    block_id=item.get("block_id") if isinstance(item.get("block_id"), str) else None,
                    source=item.get("source") if isinstance(item.get("source"), str) else None,
                    before_tokens=_coerce_non_negative_int(item.get("before_tokens")),
                    after_tokens=_coerce_non_negative_int(item.get("after_tokens")),
                )
            )
        return cls(decisions=decisions)


@dataclass
class BudgetApplicationResult:
    context_layout: ContextPackLayout
    audit_trail: BudgetAuditTrail = field(default_factory=BudgetAuditTrail)

    @property
    def decisions(self) -> list[BudgetDecision]:
        return self.audit_trail.decisions


@dataclass(frozen=True)
class HistoryCompactionAssessment:
    should_compact: bool
    trigger_budget: int
    threshold_override: float | None = None
    keep_recent_tokens_override: int | None = None
    decisions: tuple[BudgetDecision, ...] = ()
    # 实测上轮 usage.input_tokens 与估算 history_tokens 的取大者——观测字段，
    # 不参与 should_compact 的现有判定（absolute 原逻辑不变）。
    total_input_tokens: int | None = None

    @property
    def audit_trail(self) -> BudgetAuditTrail:
        return BudgetAuditTrail.from_decisions(self.decisions)


class BudgetOrchestrator:
    def __init__(
        self,
        *,
        working_dir: str | Path | None = None,
        ports: Any = None,
    ):
        self._working_dir = working_dir
        self._ports = ports

    def select_non_static_blocks(
        self,
        blocks: list[ContextBlock],
        *,
        budget: int,
        task_profile: TaskProfile | None,
        zone: str = "dynamic",
    ) -> tuple[list[ContextBlock], list[BudgetDecision]]:
        if not blocks:
            return [], []
        total = sum(block.estimated_tokens for block in blocks)
        if budget <= 0 or total <= budget:
            return list(blocks), [
                BudgetDecision(
                    stage="context_value_selection",
                    action="keep",
                    reason="within_budget",
                    zone=zone,
                    before_tokens=total,
                    after_tokens=total,
                )
            ]

        decisions: list[BudgetDecision] = []
        required = [block for block in blocks if _is_required_state_anchor(block)]
        optional = [block for block in blocks if not _is_required_state_anchor(block)]
        selected = list(required)
        selected_ids = {block.id for block in selected}
        selected_tokens = sum(block.estimated_tokens for block in selected)

        for block in required:
            decisions.append(
                BudgetDecision(
                    stage="context_value_selection",
                    action="keep",
                    reason="required_state_anchor",
                    zone=zone,
                    block_id=block.id,
                    source=block.source,
                    before_tokens=block.estimated_tokens,
                    after_tokens=block.estimated_tokens,
                )
            )

        ranked_optional = sorted(
            optional,
            key=lambda block: _context_selection_sort_key(block, task_profile),
        )
        for block in ranked_optional:
            next_tokens = selected_tokens + block.estimated_tokens
            if next_tokens <= budget:
                selected.append(block)
                selected_ids.add(block.id)
                selected_tokens = next_tokens
                decisions.append(
                    BudgetDecision(
                        stage="context_value_selection",
                        action="keep",
                        reason="selected_by_value",
                        zone=zone,
                        block_id=block.id,
                        source=block.source,
                        before_tokens=block.estimated_tokens,
                        after_tokens=block.estimated_tokens,
                    )
                )
                continue
            decisions.append(
                BudgetDecision(
                    stage="context_value_selection",
                    action="skip",
                    reason="skip_lower_value_block",
                    zone=zone,
                    block_id=block.id,
                    source=block.source,
                    before_tokens=block.estimated_tokens,
                    after_tokens=0,
                )
            )

        if selected_tokens > budget:
            decisions.append(
                BudgetDecision(
                    stage="context_value_selection",
                    action="keep",
                    reason="over_budget_required_state_headroom",
                    zone=zone,
                    before_tokens=total,
                    after_tokens=selected_tokens,
                )
            )
        elif len(selected_ids) != len(blocks):
            decisions.append(
                BudgetDecision(
                    stage="context_value_selection",
                    action="keep",
                    reason="reduced_by_value",
                    zone=zone,
                    before_tokens=total,
                    after_tokens=selected_tokens,
                )
            )
        return selected, decisions

    def apply_context_layout_budgets(
        self,
        plan: ContextPackLayout,
        *,
        system_budget: int,
        dynamic_budget: int,
        result_budget: int | None = None,
    ) -> BudgetApplicationResult:
        # 缺陷3修复：硬隔离静态与动态预算池
        # 静态池 (static_blocks) 只受 system_budget 约束，绝对不参与动态裁剪竞争
        # 动态池 (dynamic_blocks/result_blocks) 受 dynamic_budget 约束

        # 1. 处理静态池 (严格硬隔离)
        static_blocks, static_decisions = _apply_budget_blocks(
            plan.static_blocks,
            budget=system_budget,
            zone="static",
            stage="system_budget",
            truncation_priority=SYSTEM_TRUNCATION_PRIORITY,
            use_block_priority=False,
            working_dir=self._working_dir,
            ports=self._ports,
        )

        # 2. 处理动态池与结果池 (独立竞争，结果池受独立 result_budget 约束)
        non_static_blocks, dynamic_decisions = self.apply_dynamic_budget(
            plan.non_static_blocks(),
            budget=dynamic_budget,
            zone="dynamic",
            result_budget=result_budget,
        )

        trimmed_plan = ContextPackLayout(static_blocks=static_blocks)
        trimmed_plan.replace_non_static_blocks(non_static_blocks)
        return BudgetApplicationResult(
            context_layout=trimmed_plan,
            audit_trail=BudgetAuditTrail(
                decisions=[*static_decisions, *dynamic_decisions]
            ),
        )

    def apply_dynamic_budget(
        self,
        blocks: list[ContextBlock],
        *,
        budget: int,
        zone: str = "dynamic",
        result_budget: int | None = None,
    ) -> tuple[list[ContextBlock], list[BudgetDecision]]:
        decisions: list[BudgetDecision] = []
        current_blocks = list(blocks)

        result_blocks = [block for block in current_blocks if block.zone == "result"]
        if result_blocks:
            effective_result_budget = max(0, min(budget, result_budget or budget))
            enforced_result_blocks, result_decisions = _apply_budget_blocks(
                result_blocks,
                budget=effective_result_budget,
                zone=zone,
                stage="result_budget",
                truncation_priority=DYNAMIC_TRUNCATION_PRIORITY,
                use_block_priority=True,
                working_dir=self._working_dir,
                ports=self._ports,
            )
            normalized_result_decisions = [
                decision
                if not (
                    decision.stage == "result_budget"
                    and decision.action == "keep"
                    and decision.reason == "within_budget"
                )
                else BudgetDecision(
                    stage=decision.stage,
                    action=decision.action,
                    reason="result_compaction_applied_upstream",
                    zone=decision.zone,
                    block_id=decision.block_id,
                    source=decision.source,
                    before_tokens=decision.before_tokens,
                    after_tokens=decision.after_tokens,
                )
                for decision in result_decisions
            ]
            non_result_blocks = [
                block for block in current_blocks if block.zone != "result"
            ]
            current_blocks = [*non_result_blocks, *enforced_result_blocks]
            decisions.extend(normalized_result_decisions)

        total_after_result = sum(block.estimated_tokens for block in current_blocks)
        if total_after_result <= budget:
            decisions.append(
                BudgetDecision(
                    stage="dynamic_budget",
                    action="keep",
                    reason="within_budget_after_result_budget"
                    if result_blocks
                    else "within_budget",
                    zone=zone,
                    before_tokens=total_after_result,
                    after_tokens=total_after_result,
                )
            )
            return current_blocks, decisions

        result: list[ContextBlock | None] = list(current_blocks)
        overflow = total_after_result - budget

        overflow = _drop_low_priority_volatile_blocks(
            result,
            overflow=overflow,
            zone=zone,
            decisions=decisions,
        )
        overflow = _trim_low_value_memory_archives(
            result,
            overflow=overflow,
            zone=zone,
            decisions=decisions,
            working_dir=self._working_dir,
            ports=self._ports,
        )

        remaining = [block for block in result if block is not None]
        remaining_blocks, final_decisions = _apply_budget_blocks(
            remaining,
            budget=budget,
            zone=zone,
            stage="dynamic_budget",
            truncation_priority=DYNAMIC_TRUNCATION_PRIORITY,
            use_block_priority=True,
            working_dir=self._working_dir,
            ports=self._ports,
        )
        decisions.extend(final_decisions)
        return remaining_blocks, decisions

    def assess_history_compaction(
        self,
        *,
        history_tokens: int,
        history_budget: int,
        compaction_policy: TaskCompactionPolicy | None = None,
        last_prompt_tokens: int | None = None,
    ) -> HistoryCompactionAssessment:
        threshold_override = None
        keep_recent_tokens_override = None
        if compaction_policy is not None:
            threshold_override = compaction_policy.threshold_override
            keep_recent_tokens_override = compaction_policy.keep_recent_tokens

        # last_usage 作为 total_input 的上限来源之一：真实上轮 input 与历史
        # 估算取大。
        total_input = max(int(history_tokens), int(last_prompt_tokens or 0))

        trigger_budget = history_budget
        if compaction_policy is not None:
            trigger_budget = compaction_policy.pretrim_trigger_tokens or history_budget
        should_compact = history_budget > 0 and history_tokens > trigger_budget

        reason = "history_over_trigger" if should_compact else "history_within_budget"
        return HistoryCompactionAssessment(
            should_compact=should_compact,
            trigger_budget=trigger_budget,
            threshold_override=threshold_override,
            keep_recent_tokens_override=keep_recent_tokens_override,
            total_input_tokens=total_input,
            decisions=(
                BudgetDecision(
                    stage="history_budget",
                    action="compact_history" if should_compact else "keep",
                    reason=reason,
                    zone="history",
                    before_tokens=history_tokens,
                    after_tokens=history_tokens,
                ),
            ),
        )


def log_budget_decisions(
    *,
    logger_: logging.Logger,
    decisions: BudgetAuditTrail | list[BudgetDecision] | tuple[BudgetDecision, ...],
    iteration: int | None = None,
) -> None:
    audit_trail = (
        decisions
        if isinstance(decisions, BudgetAuditTrail)
        else BudgetAuditTrail.from_decisions(decisions)
    )
    logger_.info(
        "budget_audit_summary turn=%s decisions=%d stages=%s actions=%s reasons=%s",
        iteration if iteration is not None else "-",
        audit_trail.decision_count,
        _format_counts(audit_trail.stage_counts()),
        _format_counts(audit_trail.action_counts()),
        _format_counts(audit_trail.reason_counts()),
    )
    for decision in audit_trail.decisions:
        logger_.info(
            "budget_audit turn=%s stage=%s action=%s zone=%s source=%s block_id=%s before=%d after=%d reason=%s",
            iteration if iteration is not None else "-",
            decision.stage,
            decision.action,
            decision.zone,
            decision.source or "-",
            decision.block_id or "-",
            decision.before_tokens,
            decision.after_tokens,
            decision.reason,
        )


def _count_by(
    decisions: list[BudgetDecision], *, key: Callable[[BudgetDecision], str]
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for decision in decisions:
        bucket = str(key(decision))
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "-"
    return ",".join(f"{name}:{counts[name]}" for name in sorted(counts))


def merge_budget_audit_trails(*trails: BudgetAuditTrail | None) -> BudgetAuditTrail:
    merged: list[BudgetDecision] = []
    for trail in trails:
        if trail is None:
            continue
        merged.extend(trail.decisions)
    return BudgetAuditTrail(decisions=merged)


def load_budget_audit_trail(metadata: dict[str, Any] | None) -> BudgetAuditTrail | None:
    if not isinstance(metadata, dict):
        return None
    raw_audit = metadata.get("budget_audit")
    if not isinstance(raw_audit, dict):
        return None
    return BudgetAuditTrail.from_metadata(raw_audit)


def _coerce_non_negative_int(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _is_required_state_anchor(block: ContextBlock) -> bool:
    if block.source in {"session_state", "history_compaction", "extra_system"}:
        return True
    if block.priority == "critical":
        return True
    return bool(block.metadata.get("required_state_anchor"))


def _has_operational_state(block: ContextBlock) -> bool:
    payload = block.state_payload
    if not isinstance(payload, dict):
        return False
    for key in ("primary_goal", "constraints", "decisions", "todos", "open_questions"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, list) and value:
            return True
    return False


def _context_value_category(block: ContextBlock, task_profile: TaskProfile | None) -> int:
    prefers_tooling = bool(task_profile.prefers_tooling) if task_profile is not None else False
    requires_grounding = bool(task_profile.requires_grounding) if task_profile is not None else False
    if _is_required_state_anchor(block):
        return 0
    if _has_operational_state(block):
        return 1
    if block.source in {"attachment_guidance", "runtime_context"}:
        return 1
    if block.zone == "history" and requires_grounding:
        return 1
    if block.zone == "result" and prefers_tooling:
        return 1
    if block.source == "memory_procedures":
        return 1 if prefers_tooling else 2
    if block.source == "memory_facts":
        return 1 if requires_grounding else 2
    if block.source == "memory_archives":
        return 3
    if block.priority == "high":
        return 2
    if block.priority == "medium":
        return 3
    return 4


def _context_selection_sort_key(
    block: ContextBlock,
    task_profile: TaskProfile | None,
) -> tuple[int, int, int, int, int]:
    priority_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return (
        _context_value_category(block, task_profile),
        0 if block.metadata.get("grounding_required") else 1,
        priority_rank.get(block.priority, 2),
        0 if not block.replaceable else 1,
        block.estimated_tokens,
    )


def _drop_low_priority_volatile_blocks(
    blocks: list[ContextBlock | None],
    *,
    overflow: int,
    zone: str,
    decisions: list[BudgetDecision],
) -> int:
    candidates = [
        (idx, block)
        for idx, block in enumerate(blocks)
        if block is not None
        and block.priority == "low"
        and block.stability == "volatile"
        and block.source != "memory_archives"
    ]
    candidates.sort(key=lambda item: (0 if item[1].replaceable else 1, item[0]))
    for idx, block in candidates:
        if overflow <= 0:
            break
        if block.estimated_tokens <= 0:
            continue
        blocks[idx] = None
        overflow -= block.estimated_tokens
        decisions.append(
            BudgetDecision(
                stage="dynamic_budget",
                action="drop",
                reason="drop_low_priority_volatile",
                zone=zone,
                block_id=block.id,
                source=block.source,
                before_tokens=block.estimated_tokens,
                after_tokens=0,
            )
        )
    return overflow


def _trim_low_value_memory_archives(
    blocks: list[ContextBlock | None],
    *,
    overflow: int,
    zone: str,
    decisions: list[BudgetDecision],
    working_dir: str | Path | None = None,
    ports: Any = None,
) -> int:
    candidates = [
        (idx, block)
        for idx, block in enumerate(blocks)
        if block is not None
        and block.source == "memory_archives"
        and block.priority in {"low", "medium"}
        and block.truncatable
    ]
    for idx, block in candidates:
        if overflow <= 0:
            break
        if block.estimated_tokens <= overflow:
            blocks[idx] = None
            overflow -= block.estimated_tokens
            decisions.append(
                BudgetDecision(
                    stage="dynamic_budget",
                    action="drop",
                    reason="trim_low_value_memory_archives",
                    zone=zone,
                    block_id=block.id,
                    source=block.source,
                    before_tokens=block.estimated_tokens,
                    after_tokens=0,
                )
            )
            continue

        truncated = truncate_context_block_for_overflow(block, overflow_tokens=overflow, working_dir=working_dir, ports=ports)
        if truncated is None:
            blocks[idx] = None
            overflow -= block.estimated_tokens
            decisions.append(
                BudgetDecision(
                    stage="dynamic_budget",
                    action="drop",
                    reason="trim_low_value_memory_archives",
                    zone=zone,
                    block_id=block.id,
                    source=block.source,
                    before_tokens=block.estimated_tokens,
                    after_tokens=0,
                )
            )
            continue

        blocks[idx] = truncated
        decisions.append(
            BudgetDecision(
                stage="dynamic_budget",
                action="truncate",
                reason="trim_low_value_memory_archives",
                zone=zone,
                block_id=block.id,
                source=block.source,
                before_tokens=block.estimated_tokens,
                after_tokens=truncated.estimated_tokens,
            )
        )
        overflow = 0
    return overflow


def _apply_budget_blocks(
    blocks: list[ContextBlock],
    *,
    budget: int,
    zone: str,
    stage: str,
    truncation_priority: list[str],
    use_block_priority: bool,
    working_dir: str | Path | None = None,
    ports: Any = None,
) -> tuple[list[ContextBlock], list[BudgetDecision]]:
    total = sum(block.estimated_tokens for block in blocks)
    if total <= budget:
        return list(blocks), [
            BudgetDecision(
                stage=stage,
                action="keep",
                reason="within_budget",
                zone=zone,
                before_tokens=total,
                after_tokens=total,
            )
        ]

    overflow = total - budget
    result: list[ContextBlock | None] = list(blocks)
    decisions: list[BudgetDecision] = []
    named_priority_index = {name: idx for idx, name in enumerate(truncation_priority)}
    priority_rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}

    candidates = [
        (idx, block)
        for idx, block in enumerate(result)
        if block is not None and block.truncatable and not _is_required_state_anchor(block)
    ]
    if use_block_priority:
        candidates.sort(
            key=lambda item: (
                priority_rank.get(item[1].priority, 1),
                0 if item[1].replaceable else 1,
                1 if item[1].metadata.get("grounding_required") else 0,
                named_priority_index.get(item[1].source, len(named_priority_index)),
                item[0],
            )
        )
    else:
        candidates.sort(
            key=lambda item: (
                named_priority_index.get(item[1].source, len(named_priority_index)),
                item[0],
            )
        )

    for idx, _block in candidates:
        current = result[idx]
        if current is None or overflow <= 0:
            continue
        # 逐级披露：一律先尝试持久化为磁盘引用（信息零丢失），只有引用
        # 也无法腾出空间时才整块丢弃。旧实现按"块是否装得进溢出量"分流，
        # 装得下的块被直接 drop——tools/skills 这类索引块因此连同指向
        # 磁盘的引用一起消失，模型彻底失去技能发现面。
        persisted = truncate_context_block_for_overflow(
            current, overflow_tokens=0, working_dir=working_dir, ports=ports
        )
        if persisted is not None and persisted.estimated_tokens < current.estimated_tokens:
            delta = current.estimated_tokens - persisted.estimated_tokens
            overflow -= delta
            result[idx] = persisted
            decisions.append(
                BudgetDecision(
                    stage=stage,
                    action="truncate",
                    reason="persist_for_budget",
                    zone=zone,
                    block_id=current.id,
                    source=current.source,
                    before_tokens=current.estimated_tokens,
                    after_tokens=persisted.estimated_tokens,
                )
            )
            continue

        overflow -= current.estimated_tokens
        result[idx] = None
        decisions.append(
            BudgetDecision(
                stage=stage,
                action="drop",
                reason="remove_for_budget",
                zone=zone,
                block_id=current.id,
                source=current.source,
                before_tokens=current.estimated_tokens,
                after_tokens=0,
            )
        )
        continue

    if overflow > 0:
        decisions.append(
            BudgetDecision(
                stage=stage,
                action="keep",
                reason="still_over_budget_non_truncatable",
                zone=zone,
                before_tokens=total,
                after_tokens=sum(
                    block.estimated_tokens for block in result if block is not None
                ),
            )
        )
    return [block for block in result if block is not None and block.content], decisions
