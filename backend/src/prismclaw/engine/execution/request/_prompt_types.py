from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field

from prismclaw.engine.execution.checkpoint import (
    SystemPromptSectionRecord,
    SystemPromptSectionState,
)
from prismclaw.schema.agent.task_types import TaskSpec
from prismclaw.schema.content.context import ContextBlock, ContextPackLayout


def _wrap_xml(tag: str, content: str | None) -> str | None:
    if not content or not content.strip():
        return None
    return f"<{tag}>\n{content.strip()}\n</{tag}>"


@dataclass(frozen=True)
class MarketplaceSummary:
    """专家包索引行（`<packages>` 的唯一数据源）。

    只承载"存在某个能力域"这一事实：包名、外层描述、技能计数。子专家包、
    技能名、子代理名一律不进入固定 prompt，由 skill_search / subagent_search
    逐级披露。
    """

    name: str
    description: str = ""  # 构建期已截断
    skill_count: int = 0


@dataclass(frozen=True)
class AvailableSubagentEntry:
    """子代理目录条目（固定 prompt 与包计数共用）。

    Single catalog source: RuntimeCatalog.list_agent_entries()，
    与派发时使用同一注册表，prompt 名单与运行时校验不可能漂移。

    ``expert_name is None`` 表示工作区核心子代理（进入 ``<subagents>``）；
    专家包子代理只用于 ``<packages>`` 计数与 ``subagent_search`` 检索，其名称
    不进入固定 prompt。
    """

    name: str
    description: str  # already truncated to 100 chars for prompt compactness
    expert_name: str | None = None
    expert_description: str | None = None


@dataclass
class PromptInputs:
    session_id: str
    workspace_id: str
    agent_id: str
    tools: Sequence[tuple[str, str]] = ()
    # 仅项目级技能名（用户主动安装、数量有界）；专家包技能走 skill_search。
    project_skill_names: tuple[str, ...] = ()
    # 专家包索引：`<packages>` 的技能计数与描述来源
    marketplace_summaries: Sequence[MarketplaceSummary] = ()
    available_subagents: Sequence[AvailableSubagentEntry] = ()
    workspace_path: str | None = None
    now_iso: str | None = None
    identity: str | None = None  # 由 build_identity_text() 从 IDENTITY.md 读取；缺失时为空
    preferences: str | None = None  # 由 build_preferences_text() 显式传入；文件缺失时为空
    expert_persona: str | None = None
    subagent_registry_size: int = 0
    runtime_strategy_notes: Sequence[str] = ()
    task_spec: TaskSpec | None = None
    active_skill_name: str | None = None
    allowed_tools_override: Sequence[str] = ()
    skill_is_active: bool = False
    # 区分调用来源: "skill" 或 "command"。命令 dispatch 时设为 "command",
    # 系统提示词据此输出 <active_skill source="command"> 而非 source="skill"。
    invocation_context: str = "skill"


@dataclass(frozen=True, slots=True)
class PromptSectionSpec:
    name: str
    text: str
    zone: str
    truncatable: bool
    cacheable: bool
    priority: str = "medium"
    stability: str | None = None
    replaceable: bool = False
    metadata: dict[str, object] = field(default_factory=dict)
    block_id: str | None = None


@dataclass
class SystemPromptResult:
    text: str
    token_breakdown: dict[str, int]
    blocks: list[ContextBlock]

    @classmethod
    def from_blocks(
        cls,
        blocks: list[ContextBlock],
    ) -> SystemPromptResult:
        text = "\n\n".join(block.content for block in blocks)
        token_breakdown = {block.source: block.estimated_tokens for block in blocks}
        return cls(
            text=text,
            token_breakdown=token_breakdown,
            blocks=list(blocks),
        )


def _section_record_from_block(block: ContextBlock) -> SystemPromptSectionRecord:
    return SystemPromptSectionRecord(
        name=block.source,
        block_id=block.id,
        zone=str(block.zone),
        stability=str(block.stability) if block.stability is not None else None,
        priority=str(block.priority),
        cacheable=block.cacheable,
        truncatable=block.truncatable,
        replaceable=block.replaceable,
        estimated_tokens=block.estimated_tokens,
        content_digest=hashlib.sha1(block.content.encode("utf-8")).hexdigest()[:16],
        metadata=dict(block.metadata),
    )


def build_system_prompt_section_state(
    plan: ContextPackLayout,
) -> SystemPromptSectionState:
    static_sections = [_section_record_from_block(block) for block in plan.static_blocks]
    dynamic_sections = [_section_record_from_block(block) for block in plan.dynamic_blocks]
    cache_break_sections = list(
        dict.fromkeys(
            record.name for record in [*static_sections, *dynamic_sections] if not record.cacheable
        )
    )
    return SystemPromptSectionState(
        lifecycle_phase="active",
        static_sections=static_sections,
        dynamic_sections=dynamic_sections,
        cache_break_sections=cache_break_sections,
    )
