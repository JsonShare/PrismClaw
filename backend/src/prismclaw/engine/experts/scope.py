"""会话级能力装配：由 ``header.expert_id`` + 部署状态推导出的唯一运行期作用域。

设计约束（expert-refactor-3.0 §4.3）：

- 意图事实源是 ``SessionHeader.expert_id``；环境事实源是 ``EnvironmentCatalogSnapshot``。
  本模块只做纯函数派生，不写状态、不落库。
- 纯函数构建，每个 run 一次；同一份对象同时供人格、工具面、技能、MCP 消费。
- 只携带路径与集合，不携带人格正文（避免 200KB 文本随对象复制）。

三态（``NoExpertScope`` / ``ActiveScope`` / ``RejectedScope``）：

- 未选专家与已选专家都持有工具面；被拒态**没有** ``tool_surface`` 字段。
  「有 expert_id 却 restricted=False」在类型上不可构造，消费点无需再判。
- 被拒态仍可读 ``tool_owners``（空集）/``team_name``（None）等公共属性，
  归属门控与团队逻辑不必为它单独分支。

与方案文本的有意偏差（减法）：方案列出的 ``skill_ids`` / ``agent_ids`` / ``allows_skill``
在本期没有真实消费点——技能与子代理的授权口径都是「包级」的（``allowed_tool_owners``
按 ``expert_name`` 判定），逐技能/逐代理集合只会成为第二个事实源。因此本对象先只保留
``tool_owners``，待出现逐项消费点再引入，避免新增无用属性。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Self

from prismclaw.engine.execution.tools.expert_mode import (
    DEFERRED_TEAM_TOOLS,
    EXPERT_MODE_TOOLS,
    TEAM_LEAD_TOOLS,
)
from prismclaw.engine.experts.codes import ExpertError, ExpertErrorCode
from prismclaw.engine.experts.loading.load_result import ExpertPackage
from prismclaw.engine.experts.registry.expert_registry import ExpertRegistry
from prismclaw.infra.config.capability_policy import CapabilityPolicySnapshot
from prismclaw.infra.environment_catalog import EnvironmentCatalogSnapshot
from prismclaw.schema.agent.session import SessionHeader
from prismclaw.schema.services.skills import qualified_skill_id

# 专家只能由用户显式选择；模型/场景/模板等隐式激活通道已删除。
_ACTIVATION_SOURCES = frozenset({"user"})


@dataclass(frozen=True, slots=True)
class ToolSurface:
    """专家模式的工具面；MCP 工具不参与，按专家包作用域单独治理。"""

    active: frozenset[str]
    deferred: frozenset[str]
    member: frozenset[str]
    restricted: bool

    def allows(
        self,
        tool_name: str,
        *,
        member: bool = False,
        include_deferred: bool = False,
    ) -> bool:
        if ":" in tool_name or not self.restricted:
            return True
        allowed = self.member if member else self.deferred if include_deferred else self.active
        return tool_name in allowed


# 用户未选专家：全量工具面。``restricted=False`` 让 governance 直接放行，
# 集合内容只用于诊断输出，不参与判定。
UNRESTRICTED_SURFACE = ToolSurface(
    active=EXPERT_MODE_TOOLS,
    deferred=EXPERT_MODE_TOOLS,
    member=EXPERT_MODE_TOOLS,
    restricted=False,
)

# 被拒作用域没有工具面；需要「全拒」语义的少数消费点（工具面渲染）用它兜底。
# 这是安全边界而非降级：governance 见到 restricted 后只放行白名单，空白名单即全拒。
_EMPTY_SURFACE = ToolSurface(
    active=frozenset(),
    deferred=frozenset(),
    member=frozenset(),
    restricted=True,
)


class _ScopeBase:
    """三态共有属性：公共读取面，避免消费点为「被拒」单独分支。

    ``tool_owners`` / ``member`` 只做类型声明，具体由各态以字段提供——
    若在基类写成 property，dataclass 子类同名字段会把 property 当成默认值。
    """

    if TYPE_CHECKING:
        tool_owners: frozenset[str]
        member: bool

    @property
    def team_name(self) -> str | None:
        """专家包名；无 expert 时为 None。"""
        return None

    @property
    def lead_agent(self) -> str | None:
        """团队 lead agent；非团队为 None。"""
        return None

    def with_member(self, *, tool_surface: ToolSurface | None = None) -> Self:
        """标记为成员子代理作用域；默认沿用当前工具面。

        子代理的最终工具面由 ``_settle_tool_surface`` 统一固定为
        ``SUBAGENT_TOOL_SURFACE``，此处只负责 ``member`` 标记与
        「恢复既有面」的场景。被拒作用域没有工具面，打标记不会让它变可用。
        """
        return self


@dataclass(frozen=True, slots=True)
class _GrantedScope(_ScopeBase):
    """可执行作用域（未选 / 已选）：持有工具面。"""

    tool_surface: ToolSurface
    tool_owners: frozenset[str]
    member: bool

    def with_member(self, *, tool_surface: ToolSurface | None = None) -> Self:
        return replace(
            self,
            member=True,
            tool_surface=tool_surface if tool_surface is not None else self.tool_surface,
        )


@dataclass(frozen=True, slots=True)
class NoExpertScope(_GrantedScope):
    """用户未选专家。不是降级：工具面全量，但技能归属仍收窄。"""


@dataclass(frozen=True, slots=True)
class ActiveScope(_GrantedScope):
    """专家已激活：工具面受限，人格源已解析。"""

    expert: ExpertPackage
    persona_source: Path | None

    @property
    def is_team(self) -> bool:
        manifest = self.expert.manifest
        return manifest is not None and manifest.expert_type == "team"

    @property
    def team_name(self) -> str | None:
        """团队包名；单个 agent 专家返回 None（调用方据此判定「是否团队会话」）。"""
        return self.expert.name if self.is_team else None

    @property
    def lead_agent(self) -> str | None:
        """团队 lead agent；非团队为 None。

        团队包的 ``manifest.agentName`` 即 ``teamInfo.leadAgent``（加载期已校验二者相等），
        因此无需读取磁盘上的 teamInfo。
        """
        if not self.is_team or self.expert.manifest is None:
            return None
        return self.expert.manifest.agent_name


@dataclass(frozen=True, slots=True)
class RejectedScope(_ScopeBase):
    """选了但不可用。不持有 ``tool_surface`` —— 字段缺失是类型级强制。"""

    code: ExpertErrorCode
    expert_id: str
    kind: Literal["expert", "team"] = "expert"
    reason: str | None = None
    # 被拒作用域不授权任何包、也不是成员：归属门控因此天然全拒。
    tool_owners: frozenset[str] = frozenset()
    member: bool = False


type Scope = NoExpertScope | ActiveScope | RejectedScope


def build_session_scope(
    header: SessionHeader,
    catalog: EnvironmentCatalogSnapshot,
) -> Scope:
    """唯一权威判定：三态之一，且每态字段自洽。"""
    expert_registry = catalog.expert_registry
    policy_snapshot = catalog.capability_policy

    rejection = _activation_rejection(header)
    expert_package: ExpertPackage | None = None
    if rejection is None:
        try:
            expert_package = _find_expert(header, expert_registry)
        except ExpertError as exc:
            rejection = RejectedScope(
                code=exc.code,
                expert_id=header.expert_id or "",
                reason=exc.message,
            )
    if expert_package is not None:
        expert_kind = _expert_kind(expert_package)
        state = policy_snapshot.states.get((expert_kind, expert_package.name))
        if state is not None and not state.enabled:
            rejection = RejectedScope(
                code=ExpertErrorCode.MODULE_DISABLED,
                expert_id=expert_package.name,
                kind=expert_kind,
                reason=state.reason,
            )
            expert_package = None
    elif header.expert_id and rejection is None:
        rejection = RejectedScope(
            code=ExpertErrorCode.EXPERT_UNAVAILABLE,
            expert_id=header.expert_id,
            reason="expert is not deployed or loaded",
        )

    if rejection is not None:
        return rejection

    tool_owners: set[str] = set()
    if expert_package is not None:
        tool_owners.add(expert_package.name)
    tool_owners.update(_selected_skill_owners(header, expert_registry, policy_snapshot))

    if expert_package is None:
        # 用户未选专家才是全量，这不是降级路径。
        return NoExpertScope(
            tool_surface=UNRESTRICTED_SURFACE,
            tool_owners=frozenset(tool_owners),
            member=False,
        )
    return ActiveScope(
        tool_surface=tool_surface_for_expert(expert_package),
        tool_owners=frozenset(tool_owners),
        member=False,
        expert=expert_package,
        persona_source=_persona_source(expert_package),
    )


def tool_surface_of(scope: Scope | None) -> ToolSurface | None:
    """可见工具面。``None`` = 无作用域（headless），不做限制。

    被拒作用域返回空面而不是 ``None``：``None`` 在 governance 里是
    「放行全部」，用它表示被拒会构成「少给变多给」。
    """
    if scope is None:
        return None
    if isinstance(scope, RejectedScope):
        return _EMPTY_SURFACE
    return scope.tool_surface


def _activation_rejection(header: SessionHeader) -> RejectedScope | None:
    if not header.expert_id:
        return None
    source = header.expert_activation_source
    if source is not None and source not in _ACTIVATION_SOURCES:
        return RejectedScope(
            code=ExpertErrorCode.EXPERT_UNAVAILABLE,
            expert_id=header.expert_id,
            reason=f"unsupported expert activation source: {source}",
        )
    return None


def _find_expert(header: SessionHeader, registry: ExpertRegistry) -> ExpertPackage | None:
    if not header.expert_id:
        return None
    return registry.get(header.expert_id)


def _expert_kind(expert: ExpertPackage) -> Literal["expert", "team"]:
    manifest = expert.manifest
    return "team" if manifest is not None and manifest.expert_type == "team" else "expert"


def _selected_skill_owners(
    header: SessionHeader,
    registry: ExpertRegistry,
    snapshot: CapabilityPolicySnapshot,
) -> set[str]:
    """用户显式勾选的技能所属包；技能本体的可用性仍由各自包门控。"""
    selected = set(header.selected_skill_ids)
    if not selected:
        return set()
    owners: set[str] = set()
    for expert in registry.list_experts():
        state = snapshot.states.get((_expert_kind(expert), expert.name))
        if state is not None and not state.enabled:
            continue
        declared = set(_qualified_skill_ids(expert))
        if selected & declared:
            owners.add(expert.name)
    return owners


def _qualified_skill_ids(expert: ExpertPackage) -> tuple[str, ...]:
    result: list[str] = []
    for skill in expert.skills:
        name = getattr(skill, "name", None)
        if not isinstance(name, str) or not name:
            continue
        result.append(qualified_skill_id(expert.name, name))
    return tuple(result)


def tool_surface_for_expert(expert: ExpertPackage) -> ToolSurface:
    """已激活专家的工具面。恒为受限面：入参不接受 None，避免「无专家」被写成受限面。"""
    is_team = _expert_kind(expert) == "team"
    active = EXPERT_MODE_TOOLS | TEAM_LEAD_TOOLS if is_team else EXPERT_MODE_TOOLS
    deferred = active | DEFERRED_TEAM_TOOLS if is_team else active
    return ToolSurface(
        active=active,
        deferred=deferred,
        member=EXPERT_MODE_TOOLS,
        restricted=True,
    )


def _persona_source(expert: ExpertPackage) -> Path | None:
    if expert.manifest is None or expert.root_path is None:
        return None
    agent_name = expert.manifest.agent_name
    if not agent_name:
        return None
    path = expert.root_path / "agents" / f"{agent_name}.md"
    return path if path.is_file() else None


__all__ = [
    "UNRESTRICTED_SURFACE",
    "ActiveScope",
    "NoExpertScope",
    "RejectedScope",
    "Scope",
    "ToolSurface",
    "build_session_scope",
    "tool_surface_for_expert",
    "tool_surface_of",
]
