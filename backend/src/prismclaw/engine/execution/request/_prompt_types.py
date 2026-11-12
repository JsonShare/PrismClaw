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


@dataclass
class AvailableSkillEntry:
    name: str
    # 契约式渲染：构建期经 _subagent_compact_description 截断（100 字符），
    # skills_section 逐技能行输出 "- `name` — description"。
    description: str = ""
    category: str | None = None
    plugin_name: str | None = None
    priority: int = 0
    is_active: bool = False
    # v5 渐进式披露：所属 marketplace 名称（如 medical-healthcare）
    marketplace_name: str | None = None
    # v5 渐进式披露：marketplace / 插件级描述（来自市场级 plugin.json 的 description）
    marketplace_description: str | None = None
    # 技能准入策略：auto(双通道)/user_only(仅用户指定)/model_only(仅模型)/disabled(禁用)
    invocation_policy: str = "auto"


@dataclass(frozen=True)
class MarketplaceSummary:
    """marketplace 包索引行——独立于技能行预算，永不截断。

    §4.10 静态层：组行的职责是"让模型知道存在某个能力域"（命名空间目录），
    其渲染不依赖组内技能行是否被预算截断，因此从截断前的全量条目聚合，
    与 AvailableSkillEntry 列表解耦。skill_count 含被截断的技能。
    """

    name: str
    description: str = ""  # 构建期已截断
    skill_count: int = 0


@dataclass
class AvailableSubagentEntry:
    """Single authoritative sub-agent listing surfaced in the frozen prompt.

    Single catalog source: CapabilityCatalogService.list_agent_entries().
    No manifest-drift risk because the catalog is built from the same registry
    used at dispatch time. refreshed_at is stamped when the catalog snapshot
    is taken so the model can detect stale advice vs current state.
    """

    name: str
    description: str  # already truncated to 100 chars for prompt compactness
    can_handoff_to: tuple[str, ...] = ()
    source: str | None = None
    # v5 渐进式披露：所属 marketplace 名称与描述
    marketplace_name: str | None = None
    marketplace_description: str | None = None


@dataclass
class PromptInputs:
    session_id: str
    workspace_id: str
    agent_id: str
    model: str
    tools: Sequence[tuple[str, str]] = ()
    available_skills: Sequence[AvailableSkillEntry] = ()
    available_skills_note: str = ""
    # §4.10 静态层：marketplace 包索引（永不截断），与 available_skills 解耦
    marketplace_summaries: Sequence[MarketplaceSummary] = ()
    available_subagents: Sequence[AvailableSubagentEntry] = ()
    available_subagents_refreshed_at: str = ""  # ISO-8601 UTC; empty means registry empty
    available_subagents_total: int = 0  # SDD D11：目录总数（>len 时渲染 truncation trailer）
    workspace_path: str | None = None
    repo_map: str | None = None
    now_iso: str | None = None
    identity: str | None = None  # 由 build_identity_text(mode) 从 IDENTITY.md 读取；缺失时为空
    preferences: str | None = None  # 由 build_preferences_text() 显式传入；文件缺失时为空
    subagent_registry_size: int = 0
    handoff_enabled: bool = False
    any_manifest_has_can_handoff_to: bool = False
    max_handoff_chain_length: int = 5
    request_mode: str | None = None
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
    # 从配置推导（agent.context.budget.window_tokens / per-model contextWindow），
    # 由构造方显式传入；未传时技能目录预算退化为固定上限。
    max_context_tokens: int | None = None


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
