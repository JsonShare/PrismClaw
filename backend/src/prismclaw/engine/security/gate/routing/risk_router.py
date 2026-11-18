"""工具安全元数据 + 风险分级基础类型。

ToolSafetyMeta 替代旧的 approval_policy.py 中硬编码的 _BASH_MUTATION_PREFIXES /
_FILE_MUTATION_TOOLS / _WEB_TOOLS 分类。每个工具声明自己的安全属性。

设计原则：
  - 声明式：工具通过 ToolSafetyMeta 声明安全属性，而非在 evaluator 中硬编码
  - Fail-closed：未注册工具走最严分类（EXTERNAL_IRREVERSIBLE + SENSITIVE + IRREVERSIBLE）
  - 无 yolo：安全分级由 SandboxTier 承担，不存在"跳过所有检查"的路径
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from prismclaw.schema.content.workspace import SandboxTier
from prismclaw.engine.toolkit.registry.tool_classification import is_network_read_tool


# ── 安全枚举 ─────────────────────────────────────────────────────────────

_TIER_ORDER: dict[SandboxTier, int] = {
    SandboxTier.STANDARD: 0,
    SandboxTier.TIGHT: 1,
    SandboxTier.STRICT: 2,
}


class SideEffectScope(str, Enum):
    """工具副作用的辐射范围。"""
    LOCAL_READ = "local_read"             # 纯读：read/glob/ls
    LOCAL_WORKSPACE = "local_workspace"   # 读写：write/edit/bash
    INTERNAL_READ = "internal_read"       # 公网只读：联网检索/抓取
    INTERNAL_WRITE = "internal_write"     # 内部写
    EXTERNAL_WRITE = "external_write"     # 外部写
    EXTERNAL_IRREVERSIBLE = "external_irreversible"


class DataClassification(str, Enum):
    """数据敏感度分级。"""
    PUBLIC = "public"
    INTERNAL = "internal"
    SENSITIVE = "sensitive"


class Reversibility(str, Enum):
    """操作可逆性。"""
    REVERSIBLE = "reversible"
    CONDITIONAL = "conditional"
    IRREVERSIBLE = "irreversible"


# ── 工具安全元数据 ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolSafetyMeta:
    """工具安全元数据声明。

    每个工具（内置 + MCP）通过此 dataclass 声明自己的安全属性。
    基于这三个维度做风险分级：
      side_effect_scope × data_classification × reversibility → SandboxTier
    """
    side_effect_scope: SideEffectScope
    data_classification: DataClassification
    reversibility: Reversibility
    requires_workspace_binding: bool = True
    declared_purposes: tuple[str, ...] = ()


# ── fail-closed 默认值 ──────────────────────────────────────────────────

_FAIL_CLOSED_META = ToolSafetyMeta(
    side_effect_scope=SideEffectScope.EXTERNAL_IRREVERSIBLE,
    data_classification=DataClassification.SENSITIVE,
    reversibility=Reversibility.IRREVERSIBLE,
    requires_workspace_binding=True,
)


# ── 内置工具安全元数据声明 ───────────────────────────────────────────────

_BUILTIN_SAFETY_META: dict[str, ToolSafetyMeta] = {
    # 文件操作 — 纯读
    "read":        ToolSafetyMeta(SideEffectScope.LOCAL_READ, DataClassification.INTERNAL, Reversibility.REVERSIBLE,
                                  declared_purposes=("workspace_data_access",)),
    "glob":        ToolSafetyMeta(SideEffectScope.LOCAL_READ, DataClassification.INTERNAL, Reversibility.REVERSIBLE,
                                  declared_purposes=("workspace_data_access",)),
    "ls":          ToolSafetyMeta(SideEffectScope.LOCAL_READ, DataClassification.INTERNAL, Reversibility.REVERSIBLE,
                                  declared_purposes=("workspace_data_access",)),

    # 文件操作 — 读写
    "write":       ToolSafetyMeta(SideEffectScope.LOCAL_WORKSPACE, DataClassification.INTERNAL, Reversibility.REVERSIBLE,
                                  declared_purposes=("workspace_data_access",)),
    "edit":        ToolSafetyMeta(SideEffectScope.LOCAL_WORKSPACE, DataClassification.INTERNAL, Reversibility.REVERSIBLE,
                                  declared_purposes=("workspace_data_access",)),
    # Shell 执行
    "bash":        ToolSafetyMeta(SideEffectScope.LOCAL_WORKSPACE, DataClassification.INTERNAL, Reversibility.CONDITIONAL,
                                  declared_purposes=("workspace_data_access",)),
    "python":      ToolSafetyMeta(SideEffectScope.LOCAL_WORKSPACE, DataClassification.INTERNAL, Reversibility.CONDITIONAL,
                                  declared_purposes=("workspace_data_access",)),

}


def get_tool_safety_meta(tool_name: str) -> ToolSafetyMeta:
    """获取工具安全元数据，未注册工具走 fail-closed。

    LLM API 边界会把 MCP canonical 名 ``{server}:{tool}`` 重写为
    ``{server}__{tool}``；这里统一还原后再查表，使 chat 可见性等
    所有调用方无需关心名字处于哪一层。

    内置表未覆盖但被配置声明为联网检索的工具（服务名是部署配置，
    见 tool_classification）按公网只读推导；其余未注册工具 fail-closed。
    """
    canonical = tool_name.replace("__", ":", 1) if "__" in tool_name else tool_name
    meta = _BUILTIN_SAFETY_META.get(canonical)
    if meta is not None:
        return meta
    if is_network_read_tool(canonical):
        return ToolSafetyMeta(
            SideEffectScope.INTERNAL_READ, DataClassification.PUBLIC, Reversibility.REVERSIBLE
        )
    return _FAIL_CLOSED_META
