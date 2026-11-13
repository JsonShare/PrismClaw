from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from prismclaw.engine.execution.checkpoint import (
    SystemPromptSectionRecord,
    SystemPromptSectionState,
)
from prismclaw.schema.agent.task_runtime import ReviewRequirements
from prismclaw.schema.agent.task_types import TaskSpec
from prismclaw.engine.security.gate.policies import WorkspaceTag
from prismclaw.schema.content.context import ContextBlock


def _wrap_xml(tag: str, content: str | None) -> str | None:
    if not content or not content.strip():
        return None
    return f"<{tag}>\n{content.strip()}\n</{tag}>"


@dataclass(frozen=True)
class MarketplaceSummary:
    """插件包索引行（`<packages>` 的唯一数据源）。

    只承载"存在某个能力域"这一事实：包名、外层描述、技能计数。子插件、
    技能名、子代理名一律不进入固定 prompt，由 skill_search / subagent_search
    逐级披露。
    """

    name: str
    description: str = ""  # 构建期已截断
    skill_count: int = 0


@dataclass(frozen=True)
class AvailableSubagentEntry:
    """子代理目录条目（固定 prompt 与包计数共用）。

    Single catalog source: CapabilityCatalogService.list_agent_entries()，
    与派发时使用同一注册表，prompt 名单与运行时校验不可能漂移。

    ``plugin_name is None`` 表示工作区核心子代理（进入 ``<subagents>``）；
    插件子代理只用于 ``<packages>`` 计数与 ``subagent_search`` 检索，其名称
    不进入固定 prompt。
    """

    name: str
    description: str  # already truncated to 100 chars for prompt compactness
    plugin_name: str | None = None
    marketplace_name: str | None = None
    marketplace_description: str | None = None


@dataclass
class PromptInputs:
    session_id: str
    workspace_id: str
    agent_id: str
    model: str
    tools: Sequence[tuple[str, str]] = ()
    # 仅项目级技能名（用户主动安装、数量有界）；插件技能走 skill_search。
    project_skill_names: tuple[str, ...] = ()
    # 插件包索引：`<packages>` 的技能计数与描述来源
    marketplace_summaries: Sequence[MarketplaceSummary] = ()
    available_subagents: Sequence[AvailableSubagentEntry] = ()
    workspace_path: str | None = None
    now_iso: str | None = None
    identity: str | None = None  # 由 build_identity_text() 从 IDENTITY.md 读取；缺失时为空
    preferences: str | None = None  # 由 build_preferences_text() 显式传入；文件缺失时为空
    subagent_registry_size: int = 0
    handoff_enabled: bool = False
    any_manifest_has_can_handoff_to: bool = False
    max_handoff_chain_length: int = 5
    workspace_mode: str = "work"
    runtime_strategy_notes: Sequence[str] = ()
    task_spec: TaskSpec | None = None
    review_requirements: ReviewRequirements | None = None
    active_skill_name: str | None = None
    allowed_tools_override: Sequence[str] = ()
    skill_is_active: bool = False
    # 区分调用来源: "skill" 或 "command"。命令 dispatch 时设为 "command",
    # 系统提示词据此显示 [Active Command] 而非 [Active Skill]。
    invocation_context: str = "skill"
    invoked_skill_attachments: str | None = None  # 压缩后重新注入的已调用技能片段
    workspace_tag: WorkspaceTag | None = None  # 工作区安全标签，用于领域安全指令注入


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
    plan: Any,  # ContextPackLayout — import deferred to avoid circular / TODO
) -> SystemPromptSectionState:
    static_sections = [_section_record_from_block(block) for block in plan.static_blocks]
    dynamic_sections = [_section_record_from_block(block) for block in plan.dynamic_blocks]
    cache_break_sections = list(
        dict.fromkeys(
            record.name
            for record in [*static_sections, *dynamic_sections]
            if not record.cacheable
        )
    )
    return SystemPromptSectionState(
        lifecycle_phase="active",
        static_sections=static_sections,
        dynamic_sections=dynamic_sections,
        cache_break_sections=cache_break_sections,
    )
