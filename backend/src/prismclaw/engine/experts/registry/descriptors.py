"""专家包描述符。

- ``ExpertDescriptor`` 直接持有 ``expert_id`` 作为身份
- v5 渐进式披露：``SkillDescriptor`` 持有 ``pipeline_context`` 和 ``expert_name``
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from prismclaw.engine.experts.lifecycle.state import ExpertLifecycleState
from prismclaw.engine.experts.registry.identity import ExpertID
from prismclaw.schema.agent.capability import ExpertCommandCapability

if TYPE_CHECKING:
    # Skill/Agent manifest 类型只用于 descriptor 字段注解,不做运行时引用
    from prismclaw.engine.subagent.definition.manifest import AgentManifest
    from prismclaw.schema.services.skills import SkillManifest

DescriptorKind = Literal["expert", "skill", "command", "agent"]


@dataclass(slots=True)
class BaseDescriptor:
    kind: DescriptorKind = field(init=False)
    canonical_id: str
    expert_name: str | None = None
    origin: str | None = None


@dataclass(slots=True)
class ExpertDescriptor(BaseDescriptor):
    """专家包描述符。v4：以 ExpertID 为身份，无 legacy_payload。"""

    kind: Literal["expert"] = field(default="expert", init=False)
    expert_name: str = ""
    namespace: str = ""
    root_path: Path | None = None
    skills: list[SkillManifest] = field(default_factory=list)
    agents: list[AgentManifest] = field(default_factory=list)
    commands: list[ExpertCommandCapability] = field(default_factory=list)
    output_styles: dict[str, str] = field(default_factory=dict)
    # 身份与生命周期（v4 核心字段）
    expert_id: ExpertID | None = None
    lifecycle_state: ExpertLifecycleState = ExpertLifecycleState.UNINSTALLED
    explicitly_configured: bool = False
    expert_description: str | None = None


@dataclass(slots=True)
class SkillDescriptor(BaseDescriptor):
    kind: Literal["skill"] = field(default="skill", init=False)
    manifest: SkillManifest | None = None
    # v5 渐进式披露:延迟 content pipeline 参数
    # 从 SkillManifest.pipeline_context 复制，供 body 按需加载时使用。
    pipeline_context: dict[str, Any] = field(default_factory=dict)
    expert_description: str | None = None


@dataclass(slots=True)
class CommandDescriptor(BaseDescriptor):
    kind: Literal["command"] = field(default="command", init=False)
    capability: ExpertCommandCapability | None = None
    expert_description: str | None = None


@dataclass(slots=True)
class AgentDescriptor(BaseDescriptor):
    kind: Literal["agent"] = field(default="agent", init=False)
    manifest: AgentManifest | None = None
    expert_description: str | None = None

