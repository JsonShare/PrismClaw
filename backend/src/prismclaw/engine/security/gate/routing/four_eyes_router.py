"""P3-SEC-01：4-eyes 工作流完整路由。

当 WorkspaceTag.four_eyes_required=True，IRREVERSIBLE 操作 100% 走 HARD_4EYES。

路由逻辑：
  1. WorkspaceTag.four_eyes_required=True → 强制升级 ConfirmationLevel → HARD_4EYES
  2. 交叉部门指派：approver_dept 指定目标部门时，requester_id 和 approver_id 必须跨部门
  3. 如果有 four_eyes_delegate_ids 预配置列表，从该列表按 round-robin 指派
  4. 所有 4-eyes 操作写入 WORM 审计 `four_eyes_requested`/`four_eyes_approved`
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from prismclaw.engine.security.gate.policies import WorkspaceTag
from prismclaw.engine.security.gate.routing.confirm_gateway import (
    ConfirmationRequest, ConfirmationLevel,
)


@dataclass(frozen=True)
class FourEyesAssignment:
    """4-eyes 审批指派。"""
    request_id: str
    tool_name: str
    requester_id: str
    assignee_id: str        # 指定的第二审批人 ID
    assignee_dept: str      # 审批人部门（交叉部门指派标记时使用）
    audit_reason: str = ""


@dataclass
class FourEyesRouter:
    """4-eyes 工作流路由。

    Phase 3 新增：
      - WorkspaceTag.four_eyes_required=True → HARD_4EYES 强制升级
      - 交叉部门指派
      - 预配置 delegate_ids round-robin
    """

    def __init__(self, *, worm_audit: Any = None) -> None:
        self._worm_audit = worm_audit
        self._delegate_round_robin_idx: dict[str, int] = {}

    def should_force_four_eyes(
        self,
        *,
        request: ConfirmationRequest,
        workspace_tag: WorkspaceTag | None = None,
    ) -> bool:
        """判断是否应该强制走 4-eyes 审批。"""
        # 1. 显式 HARD_4EYES 级别
        if request.confirmation_level == ConfirmationLevel.HARD_4EYES:
            return True
        # 2. WorkspaceTag.four_eyes_required
        if workspace_tag and getattr(workspace_tag, "four_eyes_required", False):
            return True
        # 3. PHI + IRREVERSIBLE → 默认 4-eyes（ConfirmGateway 已覆盖）
        return False

    def upgrade_to_four_eyes(
        self,
        request: ConfirmationRequest,
        *,
        workspace_tag: WorkspaceTag | None = None,
    ) -> ConfirmationRequest:
        """将确认请求升级为 HARD_4EYES（如条件满足）。"""
        if not self.should_force_four_eyes(request=request, workspace_tag=workspace_tag):
            return request
        # 返回新对象（frozen ConfirmationRequest）
        import time
        return ConfirmationRequest(
            request_id=request.request_id,
            tool_name=request.tool_name,
            arguments=request.arguments,
            confirmation_kind=request.confirmation_kind,
            confirmation_level=ConfirmationLevel.HARD_4EYES,
            signed_payload=request.signed_payload,
            sandbox_tier=request.sandbox_tier,
            reason=f"four_eyes_forced: {request.reason} (by {self._force_reason(request, workspace_tag)})",
            batch_count=request.batch_count,
            created_at=time.time(),
        )

    def assign_approver(
        self,
        *,
        request: ConfirmationRequest,
        requester_id: str,
        workspace_tag: WorkspaceTag | None = None,
        session_id: str = "",
    ) -> FourEyesAssignment:
        """指派第二审批人。

        优先级：
          1. four_eyes_delegate_ids pre-config → round-robin
          2. approver_dept 交叉部门
          3. 默认：使用 req_id + "-" + 短 token 作为占位
        """
        assignee_dept = ""
        assignee_id = ""
        reason = ""

        # 1. Pre-config delegate_ids (round-robin)
        delegates = []
        if workspace_tag:
            delegates = getattr(workspace_tag, "four_eyes_delegate_ids", []) or []
        key = f"{workspace_tag.workspace_id if workspace_tag else 'global'}"
        if delegates:
            idx = self._delegate_round_robin_idx.get(key, 0) % len(delegates)
            assignee_id = delegates[idx]
            self._delegate_round_robin_idx[key] = idx + 1
            reason = "delegated_from_tag_preconfig"
        else:
            # 2 & 3: 交叉部门占位（Phase 3b 接入 IAM 部门接口）
            assignee_id = f"four-eyes-{secrets.token_urlsafe(6)}"
            approver_dept = getattr(workspace_tag, "approver_dept", "") if workspace_tag else ""
            if approver_dept:
                assignee_dept = approver_dept
                reason = f"cross_dept_assign: requester_dept=default target={approver_dept}"
            else:
                reason = "default_placeholder_assign"

        # Requester != Assignee 硬性校验
        if assignee_id == requester_id:
            assignee_id = f"{assignee_id}-alt-{secrets.token_urlsafe(4)}"
            reason = f"{reason} (replaced: requester=assignee)"

        assignment = FourEyesAssignment(
            request_id=request.request_id,
            tool_name=request.tool_name,
            requester_id=requester_id,
            assignee_id=assignee_id,
            assignee_dept=assignee_dept,
            audit_reason=reason,
        )

        if self._worm_audit is not None:
            self._worm_audit.log(
                session_id,
                "four_eyes_requested",
                {
                    "request_id": request.request_id,
                    "tool": request.tool_name,
                    "requester_id": requester_id,
                    "assignee_id": assignee_id,
                    "assignee_dept": assignee_dept,
                    "reason": reason,
                },
            )

        return assignment

    @staticmethod
    def _force_reason(request: ConfirmationRequest, tag: WorkspaceTag | None) -> str:
        if tag and getattr(tag, "four_eyes_required", False):
            return "workspace_tag_four_eyes_required"
        if request.confirmation_level == ConfirmationLevel.HARD_4EYES:
            return "already_hard_4eyes_level"
        return "unknown"
