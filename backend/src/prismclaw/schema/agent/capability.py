from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Union

# Python 3.10 兼容:module-level 类型别名不能使用 | 语法(执行期非注解位置不支持 type.__or__)
# 后续升级到 Python 3.11+ 可改为 type X = ... 语法。
JsonScalar = Union[str, int, float, bool, None]
if TYPE_CHECKING:
    JsonValue = Union[JsonScalar, dict[str, "JsonValue"], list["JsonValue"]]
else:
    JsonValue = Union[JsonScalar, dict, list]


class EffectClass(str, Enum):
    """工具执行的效果分类——schema 层定义，被 execution/checkpoint 和 toolkit 共同引用。"""
    PURE_READ = "pure_read"
    LOCAL_MUTATION = "local_mutation"
    EXTERNAL_MUTATION = "external_mutation"
    UNKNOWN_EFFECT = "unknown_effect"


class CapabilityKind(str, Enum):
    COMMAND = "command"
    TOOL = "tool"
    AGENT = "agent"
    HOOK = "hook"
    MCP_SERVER = "mcp_server"
    MCP_TOOL = "mcp_tool"
    OUTPUT_STYLE = "output_style"
    SKILL = "skill"


class CapabilityStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    SHADOWED = "shadowed"
    REJECTED = "rejected"
    DEGRADED = "degraded"


class SourceKind(str, Enum):
    BUILTIN = "builtin"
    LOCAL_PLUGIN = "local_plugin"
    MANAGED_PLUGIN = "managed_plugin"
    SESSION_PLUGIN = "session_plugin"
    MCP = "mcp"
    WORKSPACE = "workspace"


class TrustLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class ScopeKind(str, Enum):
    GLOBAL = "global"
    WORKSPACE = "workspace"
    SESSION = "session"
    USER = "user"


@dataclass(frozen=True, slots=True)
class PluginSourceRef:
    source_id: str
    source_kind: SourceKind
    trust_level: TrustLevel
    scope_kind: ScopeKind
    scope_id: str | None = None
    version: str | None = None
    origin_path: str | None = None
    managed_ref: str | None = None


@dataclass(frozen=True, slots=True)
class CapabilityNode:
    canonical_id: str
    kind: CapabilityKind
    display_name: str
    source_ref: PluginSourceRef
    status: CapabilityStatus
    version: str | None = None
    priority: int = 0
    policy_tags: tuple[str, ...] = ()
    projection_targets: tuple[str, ...] = ()
    metadata: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConflictDecision:
    winner_id: str
    loser_id: str
    reason_code: str
    human_readable_reason: str


@dataclass(frozen=True, slots=True)
class CapabilityGraph:
    graph_id: str
    run_id: str
    source_fingerprint: str
    nodes: tuple[CapabilityNode, ...]
    conflict_decisions: tuple[ConflictDecision, ...]
    built_at_epoch_ms: int
    policy_fingerprint: str

    def enabled_nodes(self) -> tuple[CapabilityNode, ...]:
        return tuple(node for node in self.nodes if node.status == CapabilityStatus.ENABLED)

    def nodes_by_kind(self, kind: CapabilityKind) -> tuple[CapabilityNode, ...]:
        return tuple(node for node in self.nodes if node.kind == kind)

    def get(self, canonical_id: str) -> CapabilityNode | None:
        for node in self.nodes:
            if node.canonical_id == canonical_id:
                return node
        return None

    def find_all(self, canonical_id: str) -> tuple[CapabilityNode, ...]:
        """返回同 canonical_id 下所有 ENABLED 节点(多 plugin 重名时返回每个 plugin 的版本)。"""
        return tuple(
            n
            for n in self.nodes
            if n.canonical_id == canonical_id and n.status == CapabilityStatus.ENABLED
        )

    def get_with_plugin(
        self, canonical_id: str, plugin_name: str | None
    ) -> CapabilityNode | None:
        """按 plugin_name 优先匹配 ENABLED 节点,匹配不到则 fallback 到首个。"""
        enabled = [
            n
            for n in self.nodes
            if n.canonical_id == canonical_id and n.status == CapabilityStatus.ENABLED
        ]
        if not enabled:
            return None
        if plugin_name:
            for n in enabled:
                if n.metadata.get("plugin_name") == plugin_name:
                    return n
        return enabled[0]


@dataclass(frozen=True, slots=True)
class RuntimeCapabilities:
    graph_id: str
    commands: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    agents: tuple[str, ...] = ()
    hooks: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    mcp_tools: tuple[str, ...] = ()
    output_styles: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CapabilityAssemblerResult:
    graph: CapabilityGraph
    runtime: RuntimeCapabilities


@dataclass(slots=True)
class PluginCommandCapability:
    name: str
    description: str
    body: str
    metadata: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(slots=True)
class PluginCapabilityGraph:
    plugin_name: str
    graph: CapabilityGraph | None = None
    runtime_capabilities: RuntimeCapabilities | None = None
    command_capabilities: list[PluginCommandCapability] = field(default_factory=list)
    skill_capabilities: list[Any] = field(default_factory=list)
    agent_capabilities: list[Any] = field(default_factory=list)
    hook_capabilities: dict[str, Any] = field(default_factory=dict)
    mcp_capabilities: dict[str, Any] = field(default_factory=dict)
    output_style_capabilities: dict[str, str] = field(default_factory=dict)

    @property
    def graph_id(self) -> str | None:
        return self.graph.graph_id if self.graph is not None else None
