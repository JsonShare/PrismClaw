"""PrismClaw 核心类型集中定义。

Phase 1 类型系统重构 (P1-TYP-01)：替代旧的 PermissionTier 三元组，
引入 WorkspaceMode（chat/work）+ SandboxTier（始终隔离）+ ConfirmationKind。

设计原则：
  - WorkspaceMode 正交于安全策略，仅控制工具可见性
  - 沙箱隔离永远存在，不允许"无沙箱"概念
  - ConfirmationKind 区分"bash 审批"和业务语义最终确认
"""
from __future__ import annotations

from enum import Enum
from typing import Literal


# ── 工作模式（Chat/Work 正交于安全策略，仅控制工具可见性）───────────

from prismclaw.schema.agent.types import WorkspaceMode  # noqa: F401
"""用户工作模式。

- ``chat`` — 只读模式，写类工具不可见（替代旧 PermissionTier "read-only"）
- ``work`` — 读写模式，全部工具可见（替代旧 "approval" 和 "yolo"）

旧 → 新映射：
  "read-only" → "chat"
  "approval"  → "work"
  "yolo"      → "work"（yolo 概念删除，安全分级由 SandboxTier + SafetyGate 承担）
"""


# ── 沙箱档位（隔离永远存在，不允许"无沙箱"概念）───────────────────


class SandboxTier(str, Enum):
    """容器隔离档位。

    每个工具调用都运行在容器内，档位决定文件系统/网络/进程命名空间的隔离强度。
    旧的 ``NoSandboxPolicy`` / ``SandboxSettings.enforce=false`` 不再存在。
    """

    STANDARD = "standard"
    """allowWrite=workspace; allowRead=workspace+$USER_SAFE; allowNet=常用域名白名单"""

    TIGHT = "tight"
    """+ denyRead 敏感用户目录; + denyNet *.corp 内网域名"""

    STRICT = "strict"
    """allowRead=仅 workspace; allowNet=[]; denyPidNS=on — 医疗 PHI / 法律案件"""


# ── 最终确认分类（区分"bash 审批"和业务语义最终确认）─────────────


class ConfirmationKind(str, Enum):
    """SafetyGate 输出的确认分类。

    - NONE: 不确认，直接执行
    - SOFT_CONFIRM: 写操作但非不可逆，可批量确认（SOFT_BATCH）
    - IRREVERSIBLE: 外部副作用不可逆，必须最终确认（HARD_MFA / HARD_4EYES）

    旧的 ``ApprovalDecision = Literal["approve", "deny", "wait"]`` 由 SafetyGate
    内部消化，不再暴露给上层。ConfirmationKind 是 SafetyGate 对外输出的
    唯一确认需求信号。
    """

    NONE = "none"
    SOFT_CONFIRM = "soft_confirm"
    IRREVERSIBLE = "irreversible"


# ── 审批门控决策字面量（SafetyGate 内部审计用）───────────────────

GateApprovalDecision = Literal["approve", "deny", "wait"]
"""SafetyGate 审批门控决策字面量。

与 ``models.audit_types.ApprovalDecision``（审计记录枚举）语义不同：
- 本类型：SafetyGate 的门控决策信号
- ApprovalDecision：审计日志中的工具调用状态记录
"""