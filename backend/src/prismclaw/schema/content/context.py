from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from prismclaw.schema.agent.task_types import TaskRiskLevel as TaskRiskLevel  # noqa: F401

ContextZone = Literal["static", "dynamic", "history", "result"]
ContextStability = Literal["stable", "semi_stable", "volatile"]
ContextPriority = Literal["critical", "high", "medium", "low"]

TaskMaterialType = Literal[
    "text",
    "code",
    "document",
    "table",
    "image",
    "tool_result",
    "retrieval_result",
    "mixed",
]


def _estimate_tokens(text: str) -> int:
    """Delegate to the calibrated estimator (SDD context-governance W1).

    Formerly a len//4 copy that drifted from the compaction estimator;
    now both share prismclaw.types.shared.estimate_tokens.
    """
    from prismclaw.schema.shared.utils import estimate_tokens

    return estimate_tokens(text)


def _promote_priority(priority: ContextPriority) -> ContextPriority:
    order: tuple[ContextPriority, ...] = ("low", "medium", "high", "critical")
    idx = order.index(priority)
    return order[min(idx + 1, len(order) - 1)]


class ContextBlock(BaseModel):
    id: str
    zone: ContextZone
    source: str
    content: str
    stability: ContextStability = "volatile"
    priority: ContextPriority = "medium"
    cacheable: bool = False
    truncatable: bool = True
    replaceable: bool = False
    estimated_tokens: int = 0
    state_payload: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if self.estimated_tokens <= 0:
            self.estimated_tokens = _estimate_tokens(self.content)


class TaskProfile(BaseModel):
    risk_level: TaskRiskLevel = "normal"
    material_types: list[TaskMaterialType] = Field(default_factory=list)
    prefers_tooling: bool = False
    requires_grounding: bool = False
    notes: list[str] = Field(default_factory=list)

    def has_material_type(self, *materials: TaskMaterialType) -> bool:
        return any(material in self.material_types for material in materials)

    def has_non_text_inputs(self) -> bool:
        return self.has_material_type("document", "table", "image")

    def verification_mode(self) -> Literal["none", "light", "strict"]:
        if self.requires_grounding or self.risk_level == "high":
            return "strict"
        if self.prefers_tooling:
            return "light"
        return "none"

    def tune_block(self, block: ContextBlock) -> ContextBlock:
        priority = block.priority
        metadata = dict(block.metadata)
        verification_mode = self.verification_mode()

        # memory 优先级提升策略：靠 material_types + prefers_tooling / requires_grounding
        # 推断，不再需要 execution_mode 标签。
        if self.has_material_type("retrieval_result"):
            if block.source == "memory_procedures" and self.prefers_tooling:
                # 执行/编辑/创建类场景（prefers_tooling=true）→ 流程记忆最珍贵
                priority = _promote_priority(_promote_priority(priority))
            elif block.source == "memory_facts" and self.requires_grounding:
                # 需要 grounding 时 → 事实记忆更关键
                priority = _promote_priority(priority)
            elif block.source == "memory_archives" and self.requires_grounding:
                priority = _promote_priority(priority)
            elif block.source.startswith("memory_"):
                # 通用 memory 上下文 → 轻微提升
                priority = _promote_priority(priority)
        if block.source.startswith("memory_") and verification_mode != "none":
            metadata["verification_mode"] = verification_mode
        if block.source == "memory_archives" and verification_mode == "strict":
            metadata["archive_role"] = "secondary_evidence"
        if self.has_non_text_inputs() and block.source == "attachment_guidance":
            priority = _promote_priority(priority)
        if self.prefers_tooling and block.source == "runtime_context":
            priority = _promote_priority(priority)
        if self.requires_grounding and block.source == "runtime_context":
            metadata["grounding_required"] = True
            metadata["verification_mode"] = verification_mode
            priority = _promote_priority(priority)
        if self.prefers_tooling and block.zone == "result":
            priority = _promote_priority(priority)
        if not self.prefers_tooling and block.zone == "history":
            priority = _promote_priority(priority)

        if priority == block.priority and metadata == block.metadata:
            return block
        return block.model_copy(update={"priority": priority, "metadata": metadata})


class ContextPackLayout(BaseModel):
    static_blocks: list[ContextBlock] = Field(default_factory=list)
    dynamic_blocks: list[ContextBlock] = Field(default_factory=list)
    history_blocks: list[ContextBlock] = Field(default_factory=list)
    result_blocks: list[ContextBlock] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def all_blocks(self) -> list[ContextBlock]:
        return [
            *self.static_blocks,
            *self.dynamic_blocks,
            *self.history_blocks,
            *self.result_blocks,
        ]

    @classmethod
    def from_blocks(cls, blocks: list[ContextBlock]) -> ContextPackLayout:
        plan = cls()
        for block in blocks:
            if block.zone == "static":
                plan.static_blocks.append(block)
            elif block.zone == "dynamic":
                plan.dynamic_blocks.append(block)
            elif block.zone == "history":
                plan.history_blocks.append(block)
            else:
                plan.result_blocks.append(block)
        return plan

    def token_breakdown(self) -> dict[str, int]:
        breakdown: dict[str, int] = {}
        for block in self.all_blocks():
            breakdown[block.id] = breakdown.get(block.id, 0) + block.estimated_tokens
        return breakdown

    def zone_blocks(self, zone: ContextZone) -> list[ContextBlock]:
        if zone == "static":
            return list(self.static_blocks)
        if zone == "dynamic":
            return list(self.dynamic_blocks)
        if zone == "history":
            return list(self.history_blocks)
        return list(self.result_blocks)

    def non_static_blocks(self) -> list[ContextBlock]:
        return [*self.dynamic_blocks, *self.history_blocks, *self.result_blocks]

    def replace_non_static_blocks(self, blocks: list[ContextBlock]) -> None:
        self.dynamic_blocks = [block for block in blocks if block.zone == "dynamic"]
        self.history_blocks = [block for block in blocks if block.zone == "history"]
        self.result_blocks = [block for block in blocks if block.zone == "result"]

    def apply_task_profile(self, task_profile: TaskProfile | None) -> None:
        if task_profile is None:
            return
        self.replace_non_static_blocks(
            [task_profile.tune_block(block) for block in self.non_static_blocks()]
        )
