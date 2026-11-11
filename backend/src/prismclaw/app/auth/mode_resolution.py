"""工作模式解析（替代旧 tier_resolution.py）。

P1-TYP-02：将 PermissionTier 三元组 (read-only/approval/yolo)
替换为 WorkspaceMode 二元组 (chat/work)。

关键语义变化：
  - mode 无等级排序（旧的 RANK={"read-only":2, "approval":1, "yolo":0} 删除）
  - "yolo" 不再存在——映射到 "work"，安全分级由 SandboxTier + SafetyGate 承担
  - "read-only" → "chat"，"approval" → "work"
"""
from __future__ import annotations

from prismclaw.schema.agent.types import WorkspaceMode


def resolve_effective_mode(
    *,
    message_mode: WorkspaceMode | None,
    deployment_default_mode: WorkspaceMode,
) -> WorkspaceMode:
    """解析当前轮次的有效工作模式。

    优先级（从高到低，第一个非 None 胜出）：
    1. ``message_mode`` — 单条消息级别覆盖（来自通道处理器）
    2. ``deployment_default_mode`` — 部署级别默认值（始终非 None）
    """
    if message_mode is not None:
        return message_mode
    return deployment_default_mode
