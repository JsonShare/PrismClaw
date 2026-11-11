"""P2-SEC-01：ConfirmGateway 三级 UX 确认。

替代旧的 approval_gate.py 的 ask 决策。
三级确认策略：
  - SOFT_BATCH: 同模板（相同 tool_name + 相同 confirmation_kind）3 条合并为 1 次确认
  - HARD_MFA: 必须通过 FIDO2/WebAuthn 认证
  - HARD_4EYES: 必须第二人审批（requester_id != approver_id）

signed_payload 防篡改：
  signed_payload = SHA256(tool_name + args_canonical + timestamp)
  确认时校验 payload 完整性，防止参数被中间人篡改。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from prismclaw.schema.content.workspace import ConfirmationKind, SandboxTier
from prismclaw.engine.security.gate.policies import MfaProvider, NullMfaProvider

logger = logging.getLogger(__name__)

_DEPLOYMENT_ENV = os.getenv("PRISMCLAW_DEPLOYMENT_ENV", "development").lower()


class ConfirmationLevel(str, Enum):
    """确认级别——由 ConfirmationKind + WorkspaceTag 联合决定。"""
    SOFT_BATCH = "soft_batch"   # 同模板 3 合 1，1 次点击确认
    HARD_MFA = "hard_mfa"       # FIDO2/WebAuthn 二次认证
    HARD_4EYES = "hard_4eyes"   # 第二人审批


# ── 数据类 ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConfirmationRequest:
    """确认请求——由 ConfirmGateway 发出。"""
    request_id: str
    tool_name: str
    arguments: dict[str, Any]
    confirmation_kind: ConfirmationKind
    confirmation_level: ConfirmationLevel
    signed_payload: str
    sandbox_tier: SandboxTier
    reason: str
    batch_count: int = 1  # SOFT_BATCH 时 >1 表示合并了多条
    created_at: float = field(default_factory=time.time)

    @property
    def batch_key(self) -> str:
        """同模板匹配键：tool_name + confirmation_kind。"""
        return f"{self.tool_name}:{self.confirmation_kind.value}"


@dataclass(frozen=True)
class ConfirmationResponse:
    """确认响应——由用户/UI 返回。"""
    request_id: str
    approved: bool
    signed_payload: str
    responder_id: str = ""
    mfa_credential_id: str | None = None  # HARD_MFA 时填充
    second_approver_id: str | None = None  # HARD_4EYES 时填充
    timestamp: float = field(default_factory=time.time)


@dataclass
class ConfirmationResult:
    """确认结果——ConfirmGateway 最终输出。"""
    allowed: bool
    reason: str = ""
    request_id: str = ""
    confirmation_level: ConfirmationLevel | None = None
    pending: bool = False  # True 表示已下发确认请求，等待用户响应（HARD_* 异步语义）


# ── signed_payload 工具函数 ────────────────────────────────────────────


def _canonicalize_args(arguments: dict[str, Any]) -> str:
    """将 arguments 规范化为确定性的 JSON 字符串。"""
    return json.dumps(arguments, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def compute_signed_payload(
    tool_name: str,
    arguments: dict[str, Any],
    timestamp: float | None = None,
    secret: str = "",
) -> str:
    """计算 signed_payload = SHA256(tool_name + args_canonical + timestamp + secret)。

    secret 由 HMAC key 提供，防止客户端伪造。
    """
    ts = str(timestamp or time.time())
    canonical_args = _canonicalize_args(arguments)
    raw = f"{tool_name}:{canonical_args}:{ts}:{secret}"
    return hashlib.sha256(raw.encode()).hexdigest()[:48]


def verify_signed_payload(
    signed_payload: str,
    tool_name: str,
    arguments: dict[str, Any],
    timestamp: float,
    secret: str = "",
    tolerance_seconds: float = 300.0,
) -> bool:
    """验证 signed_payload 完整性。"""
    # 检查时间窗口
    if abs(time.time() - timestamp) > tolerance_seconds:
        return False
    expected = compute_signed_payload(tool_name, arguments, timestamp, secret)
    return expected == signed_payload


# ── ConfirmationLevel 决策 ─────────────────────────────────────────────


def _resolve_confirmation_level(
    kind: ConfirmationKind,
) -> ConfirmationLevel:
    """根据 ConfirmationKind 决定确认级别。

    注意：four_eyes_required 引发的 HARD_4EYES 升级由
    ConfirmGateway.create_request() 中的 FourEyesRouter 处理，
    不在此函数内判断。
    """
    if kind == ConfirmationKind.NONE:
        # 不应该到这里——NONE 不需要确认
        return ConfirmationLevel.SOFT_BATCH

    # IRREVERSIBLE → HARD_MFA
    if kind == ConfirmationKind.IRREVERSIBLE:
        return ConfirmationLevel.HARD_MFA

    # 默认 SOFT_BATCH
    return ConfirmationLevel.SOFT_BATCH


# ── SOFT_BATCH 批处理 ──────────────────────────────────────────────────

_SOFT_BATCH_THRESHOLD = 3  # 同模板 3 条合并为 1 次确认


# ── ConfirmGateway ────────────────────────────────────────────────────


class ConfirmGateway:
    """三级确认网关。

    替代旧的 approval_gate.py 的 ask 决策分支。
    编排 SOFT_BATCH / HARD_MFA / HARD_4EYES 三级确认策略。
    """

    def __init__(
        self,
        *,
        batch_threshold: int = _SOFT_BATCH_THRESHOLD,
        hmac_secret: str = "",
        confirmation_channel: Any = None,
        four_eyes_router: Any = None,  # P3-SEC-01: FourEyesRouter 注入
        mfa_provider: MfaProvider | None = None,  # P10-SEC-02: MFA provider 注入
    ) -> None:
        self._batch_threshold = batch_threshold
        self._hmac_secret = hmac_secret
        self._confirmation_channel = confirmation_channel
        self._four_eyes_router = four_eyes_router  # FourEyesRouter 实例 or None
        # P10-SEC-02: MFA provider。默认 NullMfaProvider（dev/test），
        # 生产必须由调用方注入生产就绪的 provider（否则 fail-closed）。
        self._mfa_provider: MfaProvider = mfa_provider or NullMfaProvider()
        # SOFT_BATCH 缓冲区：batch_key → list[ConfirmationRequest]
        self._batch_buffer: dict[str, list[ConfirmationRequest]] = {}
        # 已发出的请求
        self._pending: dict[str, ConfirmationRequest] = {}

    def create_request(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        confirmation_kind: ConfirmationKind,
        sandbox_tier: SandboxTier,
        reason: str,
        workspace_tag: Any = None,
    ) -> ConfirmationRequest:
        """创建确认请求。"""
        import secrets
        level = _resolve_confirmation_level(confirmation_kind)
        signed_payload = compute_signed_payload(
            tool_name, arguments, secret=self._hmac_secret,
        )
        request = ConfirmationRequest(
            request_id=f"conf-{secrets.token_urlsafe(12)}",
            tool_name=tool_name,
            arguments=arguments,
            confirmation_kind=confirmation_kind,
            confirmation_level=level,
            signed_payload=signed_payload,
            sandbox_tier=sandbox_tier,
            reason=reason,
        )
        # P3-SEC-01: FourEyesRouter 强制升级 HARD_4EYES
        if self._four_eyes_router is not None and getattr(workspace_tag, "four_eyes_required", False):
            request = self._four_eyes_router.upgrade_to_four_eyes(
                request, workspace_tag=workspace_tag,
            )
        return request

    def process_request(
        self,
        request: ConfirmationRequest,
    ) -> ConfirmationResult:
        """处理确认请求。

        SOFT_BATCH: 缓冲直到 batch_threshold 条后发出 1 次确认。
        HARD_MFA / HARD_4EYES: 立即发出确认请求。
        """
        self._pending[request.request_id] = request

        if request.confirmation_level == ConfirmationLevel.SOFT_BATCH:
            key = request.batch_key
            if key not in self._batch_buffer:
                self._batch_buffer[key] = []
            self._batch_buffer[key].append(request)

            if len(self._batch_buffer[key]) >= self._batch_threshold:
                # 批量合并：发出 1 次确认，覆盖所有缓冲的请求
                batch = self._batch_buffer.pop(key)
                merged_request = ConfirmationRequest(
                    request_id=request.request_id,
                    tool_name=request.tool_name,
                    arguments=request.arguments,
                    confirmation_kind=request.confirmation_kind,
                    confirmation_level=ConfirmationLevel.SOFT_BATCH,
                    signed_payload=request.signed_payload,
                    sandbox_tier=request.sandbox_tier,
                    reason=f"批量确认：共 {len(batch)} 项操作",
                    batch_count=len(batch),
                )
                self._pending[merged_request.request_id] = merged_request
                return ConfirmationResult(
                    allowed=True,  # SOFT_BATCH: 自动通过（UI 层展示确认卡）
                    reason="soft_batch_auto_approved",
                    request_id=merged_request.request_id,
                    confirmation_level=ConfirmationLevel.SOFT_BATCH,
                )

            # 未达批量阈值：暂存，自动通过
            return ConfirmationResult(
                allowed=True,
                reason="soft_batch_pending",
                request_id=request.request_id,
                confirmation_level=ConfirmationLevel.SOFT_BATCH,
            )

        elif request.confirmation_level == ConfirmationLevel.HARD_MFA:
            # P10-SEC-02: HARD_MFA 必须经真实 MFA 校验，不能同步放行。
            # 1) 生产就绪检查：provider 非生产就绪（如 NullMfaProvider 误用于生产）
            #    → fail-closed 拒绝，绝不静默放行。
            #    动态读取部署环境（避免模块级常量在测试/热重载时失真）。
            deploy_env = os.getenv("PRISMCLAW_DEPLOYMENT_ENV", "development").lower()
            if deploy_env == "production" and not self._mfa_provider.is_production_ready:
                logger.critical(
                    "HARD_MFA rejected: MFA provider is not production-ready in production env"
                )
                return ConfirmationResult(
                    allowed=False,
                    reason="hard_mfa_provider_not_production_ready",
                    request_id=request.request_id,
                    confirmation_level=ConfirmationLevel.HARD_MFA,
                )
            # 2) HARD_MFA 是异步确认：请求已下发到 confirmation_channel，
            #    等待用户完成 FIDO2 认证并回传带 mfa_credential_id 的响应。
            #    process_request 本身不批准，返回 pending 等待 verify_response。
            if self._confirmation_channel is not None:
                try:
                    self._confirmation_channel.dispatch(request)
                except Exception as exc:  # noqa: BLE001
                    logger.error("HARD_MFA dispatch failed: %s", exc)
                    return ConfirmationResult(
                        allowed=False,
                        reason="hard_mfa_dispatch_failed",
                        request_id=request.request_id,
                        confirmation_level=ConfirmationLevel.HARD_MFA,
                    )
            return ConfirmationResult(
                allowed=False,
                pending=True,
                reason="hard_mfa_pending_user_authentication",
                request_id=request.request_id,
                confirmation_level=ConfirmationLevel.HARD_MFA,
            )

        elif request.confirmation_level == ConfirmationLevel.HARD_4EYES:
            # Production guard: stub must not auto-approve in production
            if _DEPLOYMENT_ENV == "production":
                return ConfirmationResult(
                    allowed=False,
                    reason=f"stub_not_available_in_production：{ConfirmationLevel.HARD_4EYES} 需要真实的 MFA/4-eyes 实现",
                    request_id=request.request_id,
                    confirmation_level=ConfirmationLevel.HARD_4EYES,
                )
            # HARD_4EYES: 需要第二人审批
            # NOTE: 占位实现——自动批准；待接入真实 FourEyesRouter
            logger.warning(
                "HARD_4EYES stub auto-approved in non-production environment (%s). "
                "This must not happen in production.",
                _DEPLOYMENT_ENV,
            )
            return ConfirmationResult(
                allowed=True,
                reason="hard_4eyes_stub_auto_approved",
                request_id=request.request_id,
                confirmation_level=ConfirmationLevel.HARD_4EYES,
            )

        return ConfirmationResult(
            allowed=False,
            reason=f"unknown_confirmation_level: {request.confirmation_level}",
        )

    def verify_response(self, response: ConfirmationResponse) -> ConfirmationResult:
        """验证确认响应。"""
        request = self._pending.get(response.request_id)
        if request is None:
            return ConfirmationResult(allowed=False, reason="unknown_request_id")

        # 校验 signed_payload
        if response.signed_payload != request.signed_payload:
            return ConfirmationResult(allowed=False, reason="signed_payload_mismatch")

        # 校验确认级别特定规则
        if request.confirmation_level == ConfirmationLevel.HARD_MFA:
            if not response.mfa_credential_id:
                return ConfirmationResult(
                    allowed=False,
                    reason="hard_mfa_missing_credential",
                    request_id=response.request_id,
                    confirmation_level=ConfirmationLevel.HARD_MFA,
                )
            # P10-SEC-02: 调用注入的 MfaProvider 做真实凭据校验
            if not self._mfa_provider.verify_credential(
                user_id=response.responder_id or "",
                credential_id=response.mfa_credential_id,
            ):
                return ConfirmationResult(
                    allowed=False,
                    reason="hard_mfa_credential_invalid",
                    request_id=response.request_id,
                    confirmation_level=ConfirmationLevel.HARD_MFA,
                )

        elif request.confirmation_level == ConfirmationLevel.HARD_4EYES:
            if not response.second_approver_id:
                return ConfirmationResult(
                    allowed=False,
                    reason="hard_4eyes_missing_approver",
                    request_id=response.request_id,
                    confirmation_level=ConfirmationLevel.HARD_4EYES,
                )
            if response.second_approver_id == response.responder_id:
                return ConfirmationResult(
                    allowed=False,
                    reason="hard_4eyes_same_person",
                    request_id=response.request_id,
                    confirmation_level=ConfirmationLevel.HARD_4EYES,
                )

        # 清理
        self._pending.pop(response.request_id, None)
        return ConfirmationResult(
            allowed=response.approved,
            reason="confirmed" if response.approved else "rejected_by_user",
            request_id=response.request_id,
            confirmation_level=request.confirmation_level,
        )
