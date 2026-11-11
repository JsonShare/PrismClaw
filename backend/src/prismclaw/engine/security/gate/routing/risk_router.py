"""P1-SEC-01：工具安全元数据 + L1 RiskRouter 核心类型。

ToolSafetyMeta 替代旧的 approval_policy.py 中硬编码的 _BASH_MUTATION_PREFIXES /
_FILE_MUTATION_TOOLS / _WEB_TOOLS 分类。每个工具声明自己的安全属性，
RiskRouter 基于这些属性做风险分级。

设计原则：
  - 声明式：工具通过 ToolSafetyMeta 声明安全属性，而非在 evaluator 中硬编码
  - Fail-closed：未注册工具走最严分类（EXTERNAL_IRREVERSIBLE + SENSITIVE + IRREVERSIBLE）
  - 无 yolo：安全分级由 SandboxTier + SafetyGate 承担，不存在"跳过所有检查"的路径
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from prismclaw.schema.content.workspace import SandboxTier, ConfirmationKind
from prismclaw.engine.toolkit.registry.tool_classification import is_network_read_tool


# ── 安全枚举 ─────────────────────────────────────────────────────────────

_TIER_ORDER: dict[SandboxTier, int] = {
    SandboxTier.STANDARD: 0,
    SandboxTier.TIGHT: 1,
    SandboxTier.STRICT: 2,
}


class SideEffectScope(str, Enum):
    """工具副作用的辐射范围。"""
    LOCAL_READ = "local_read"             # 纯读：read/glob/ls — chat 模式允许
    LOCAL_WORKSPACE = "local_workspace"   # 读写：write/edit/bash — chat 模式拒绝
    INTERNAL_READ = "internal_read"       # 公网只读：联网检索/抓取 — chat 模式允许
    INTERNAL_WRITE = "internal_write"     # 内部写 — chat 模式拒绝
    EXTERNAL_WRITE = "external_write"     # 外部写 — chat 模式拒绝
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
    RiskRouter 基于这三个维度做风险分级：
      side_effect_scope × data_classification × reversibility → SandboxTier + ConfirmationKind
    """
    side_effect_scope: SideEffectScope
    data_classification: DataClassification
    reversibility: Reversibility
    requires_workspace_binding: bool = True
    four_eyes_required: bool = False
    declared_purposes: tuple[str, ...] = ()


# ── fail-closed 默认值 ──────────────────────────────────────────────────

_FAIL_CLOSED_META = ToolSafetyMeta(
    side_effect_scope=SideEffectScope.EXTERNAL_IRREVERSIBLE,
    data_classification=DataClassification.SENSITIVE,
    reversibility=Reversibility.IRREVERSIBLE,
    requires_workspace_binding=True,
    four_eyes_required=True,
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

    # 联网检索/抓取由独立 MCP 服务提供，服务名是部署配置（见
    # tool_classification.get_tool_safety_meta 的动态推导），不在内置表写死。
}


def get_tool_safety_meta(tool_name: str) -> ToolSafetyMeta:
    """获取工具安全元数据，未注册工具走 fail-closed。

    LLM API 边界会把 MCP canonical 名 ``{server}:{tool}`` 重写为
    ``{server}__{tool}``；这里统一还原后再查表，使 gate / chat 可见性等
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


# ── L1 RiskRouter ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RiskAssessment:
    """RiskRouter 评估结果。"""
    sandbox_tier: SandboxTier
    confirmation: ConfirmationKind
    meta: ToolSafetyMeta
    reason: str = ""


class RiskRouter:
    """L1 风险分级路由。

    基于 ToolSafetyMeta 的三个维度（side_effect_scope × data_classification × reversibility）
    决定 SandboxTier 和 ConfirmationKind。

    快速路径：已知工具通过 dict 查找 O(1)。
    LLM hint 路径：bash 命令解析后，危险命令（rm/格式化）升级 tier。
    """

    def assess(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        llm_risk_hint: str | None = None,
    ) -> RiskAssessment:
        """评估工具调用的风险等级。"""
        meta = get_tool_safety_meta(tool_name)
        tier = self._derive_tier(meta)
        confirmation = self._derive_confirmation(meta)

        # LLM hint 路径：bash 危险命令升级
        if tool_name == "bash" and arguments:
            cmd = arguments.get("command", "")
            if isinstance(cmd, str) and self._is_dangerous_bash(cmd):
                tier = SandboxTier.TIGHT
                confirmation = ConfirmationKind.IRREVERSIBLE

        # LLM risk hint 覆盖
        if llm_risk_hint == "high":
            tier = max(tier, SandboxTier.TIGHT, key=lambda t: _TIER_ORDER[t])
            confirmation = ConfirmationKind.IRREVERSIBLE

        return RiskAssessment(
            sandbox_tier=tier,
            confirmation=confirmation,
            meta=meta,
            reason=f"{meta.side_effect_scope.value}/{meta.data_classification.value}/{meta.reversibility.value}",
        )

    @staticmethod
    def _derive_tier(meta: ToolSafetyMeta) -> SandboxTier:
        """从 ToolSafetyMeta 推导 SandboxTier。"""
        scope = meta.side_effect_scope

        if scope == SideEffectScope.LOCAL_WORKSPACE:
            return SandboxTier.STANDARD
        if scope == SideEffectScope.INTERNAL_READ:
            return SandboxTier.STANDARD
        if scope == SideEffectScope.INTERNAL_WRITE:
            return SandboxTier.TIGHT
        if scope == SideEffectScope.EXTERNAL_WRITE:
            return SandboxTier.TIGHT
        # EXTERNAL_IRREVERSIBLE
        return SandboxTier.STRICT

    @staticmethod
    def _derive_confirmation(meta: ToolSafetyMeta) -> ConfirmationKind:
        """从 ToolSafetyMeta 推导 ConfirmationKind。"""
        if meta.reversibility == Reversibility.IRREVERSIBLE:
            return ConfirmationKind.IRREVERSIBLE
        if meta.four_eyes_required:
            return ConfirmationKind.IRREVERSIBLE
        # 写操作但非不可逆 → SOFT_CONFIRM（可批量确认）
        if meta.side_effect_scope in (
            SideEffectScope.LOCAL_WORKSPACE,
            SideEffectScope.INTERNAL_WRITE,
            SideEffectScope.EXTERNAL_WRITE,
        ):
            return ConfirmationKind.SOFT_CONFIRM
        return ConfirmationKind.NONE

    @staticmethod
    def _is_dangerous_bash(cmd: str) -> bool:
        """判断 bash 命令是否为危险操作。"""
        dangerous_prefixes = ("rm ", "rm -", "mkfs.", "dd ", "shred ", "format ")
        cmd_stripped = cmd.strip().lower()
        return any(cmd_stripped.startswith(p) for p in dangerous_prefixes)
