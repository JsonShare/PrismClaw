"""WorkspaceTag 全局默认值。

每个工作区应配置自己的 WorkspaceTag 实例。此模块提供
一个 fail-closed 默认值，用于未配置工作区的兜底。

默认策略：
  - allow_external_write=False — 禁止外部写（fail-closed）
  - require_consent=True — 敏感用途需同意（fail-closed）
  - restrict_chat_sensitive_data=True — chat 模式隐藏敏感工具（fail-closed）
  - four_eyes_required=False — 不强制四人眼（运营选择）
  - sandbox_tier=STANDARD — 标准沙箱
  - template_id="default-v1" — 默认容器模板
"""
from prismclaw.engine.security.gate.policies import WorkspaceTag, DataResidency

DEFAULT_WORKSPACE_TAG = WorkspaceTag(
    workspace_id="",
    owner_id="",
    data_residency=DataResidency.CN,
)
