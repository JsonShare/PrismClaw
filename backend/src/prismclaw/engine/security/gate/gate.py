"""P1-SEC-05 + P2-SEC-01：SafetyGate 编排器 + ConfirmGateway 集成。

SafetyGate 是新安全管线的核心入口，替代旧的 approval_gate.py。

编排顺序：
  1. RuleGate (L0) — 静态规则检查，deny 或 pass
  2. RiskRouter (L1) — 风险分级，SandboxTier + ConfirmationKind
  3. WorkspaceMode 检查 — chat 模式下写类工具 deny
  4. ConfirmGateway — ConfirmationKind.IRREVERSIBLE 需要最终确认

输出 GateDecision：
  - allowed + sandbox_tier + confirmation → 执行
  - denied + reason → 拒绝（等价于旧管线的 auto-deny）
  - needs_confirmation → 需要用户最终确认（等价于旧管线的 ask）

关键语义变化（vs 旧审批管线）：
  - 旧：allow/deny/ask 三元决策，ask = 等用户审批
  - 新：allow/deny/needs_confirmation，confirmation 是语义信号（不是审批请求）
  - 旧：yolo 模式跳过所有检查
  - 新：无 yolo，SafetyGate 始终执行完整检查
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any

from prismclaw.schema.content.workspace import WorkspaceMode, SandboxTier, ConfirmationKind
from prismclaw.engine.security.gate.routing.rule_gate import RuleGate
from prismclaw.engine.security.gate.routing.risk_router import (
    RiskRouter, RiskAssessment, SideEffectScope, get_tool_safety_meta,
)
from prismclaw.engine.security.gate.policies import WorkspaceTag
from prismclaw.engine.security.gate.routing.confirm_gateway import (
    ConfirmGateway, ConfirmationRequest,
)
from prismclaw.engine.security.gate.routing.four_eyes_router import FourEyesRouter
from prismclaw.engine.security.gate.policies import MfaProvider, build_mfa_provider
from prismclaw.engine.security.consent.consent_gate import ConsentGate
from prismclaw.engine.security.consent.redis_consent_store import RedisConsentStore


# ── chat 模式下允许的 side_effect_scope ────────────────────────────────
# chat（只读）模式下，只有纯读和内部读工具允许执行
_CHAT_ALLOWED_SCOPES = frozenset({
    SideEffectScope.LOCAL_READ,     # read/glob/ls 等纯读工具
    SideEffectScope.INTERNAL_READ,  # 联网检索/抓取等公网只读工具
})


@dataclass(frozen=True)
class GateDecision:
    """SafetyGate 最终决策。"""
    allowed: bool
    sandbox_tier: SandboxTier = SandboxTier.STANDARD
    confirmation: ConfirmationKind = ConfirmationKind.NONE
    confirmation_request: ConfirmationRequest | None = None
    reason: str = ""
    risk_assessment: RiskAssessment | None = None


class SafetyGate:
    """SafetyGate 编排器。

    替代旧的 approval_gate.py + approval_policy.py + ToolApprovalHook。
    """

    def __init__(
        self,
        *,
        confirm_gateway: ConfirmGateway | None = None,
        four_eyes_router: FourEyesRouter | None = None,
        mfa_provider: MfaProvider | None = None,
        consent_gate: ConsentGate | None = None,
        consent_store: Any | None = None,
        tenant_policy_store: Any = None,
    ) -> None:
        self._rule_gate = RuleGate()
        self._risk_router = RiskRouter()
        self._four_eyes_router = four_eyes_router or FourEyesRouter()
        self._mfa_provider = mfa_provider or build_mfa_provider()
        self._confirm_gateway = confirm_gateway or ConfirmGateway(
            four_eyes_router=self._four_eyes_router,
            mfa_provider=self._mfa_provider,
        )
        # P10-SEC-03: ConsentGate 接入。默认使用 RedisConsentStore（生产就绪）；
        # 未配置 redis_url 时 RedisConsentStore 会 fail-closed（has_consent 返回 False）。
        self._consent_gate = consent_gate or ConsentGate(
            store=consent_store or RedisConsentStore(),
        )
        self._tenant_policy_store = tenant_policy_store

    async def evaluate(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        mode: WorkspaceMode = "work",
        workspace_tag: WorkspaceTag | None = None,
        workspace_root: str | None = None,
        llm_risk_hint: str | None = None,
        session_id: str = "",
        user_id: str = "",
        tenant_id: str = "",
    ) -> GateDecision:
        """评估工具调用安全性。

        编排 RuleGate → RiskRouter → WorkspaceMode 检查 → ConsentGate → ConfirmGateway。
        """
        # Step 0: 获取工具安全元数据
        meta = get_tool_safety_meta(tool_name)

        # Step 0.5: 租户级策略检查（只收紧、不放松）
        tenant_policy = None
        if self._tenant_policy_store and tenant_id:
            try:
                from prismclaw.engine.security.gate.policies import TenantPolicyOverride
                tenant_policy = await self._tenant_policy_store.get(tenant_id)
                if tenant_policy is not None:
                    if tenant_policy.is_tool_denied(tool_name):
                        return GateDecision(
                            allowed=False,
                            reason=f"租户策略禁止使用工具：{tool_name}",
                        )
            except Exception:
                pass  # 租户策略查询失败时不阻断主流程

        # Step 1: RuleGate (L0 静态规则)
        rule_result = self._rule_gate.check(
            tool_name=tool_name,
            arguments=arguments,
            meta=meta,
            workspace_tag=workspace_tag,
            workspace_root=workspace_root,
        )
        if not rule_result.allowed:
            return GateDecision(allowed=False, reason=rule_result.reason)

        # Step 2: RiskRouter (L1 风险分级)
        assessment = self._risk_router.assess(
            tool_name=tool_name,
            arguments=arguments,
            llm_risk_hint=llm_risk_hint,
        )

        # RuleGate tier_override 优先
        effective_tier = rule_result.tier_override or assessment.sandbox_tier

        # Step 3: WorkspaceMode 检查 — chat 模式下写类工具 deny
        if mode == "chat" and meta.side_effect_scope not in _CHAT_ALLOWED_SCOPES:
            return GateDecision(
                allowed=False,
                reason=f"chat_mode_deny：{tool_name} 的 side_effect_scope={meta.side_effect_scope.value}，在 chat 模式下不允许执行",
            )

        # Step 3.5: ConsentGate — require_consent 工作区 + 工具声明用途时需有效同意
        #  触发条件：workspace_tag.require_consent=True 且 工具声明了 declared_purposes。
        #  非敏感工作区或工具无声明用途 → 跳过（不引入额外摩擦）。
        if workspace_tag is not None and workspace_tag.require_consent and meta.declared_purposes:
            consent_result = self._consent_gate.check(
                session_id=session_id,
                user_id=user_id,
                tool_name=tool_name,
                declared_purposes=list(meta.declared_purposes),
                workspace_id=workspace_tag.workspace_id or "",
            )
            if not consent_result.allowed:
                return GateDecision(
                    allowed=False,
                    sandbox_tier=effective_tier,
                    reason=f"consent_required: {consent_result.reason}"
                    + (f" (missing: {', '.join(consent_result.missing_purposes)})"
                       if consent_result.missing_purposes else ""),
                    risk_assessment=assessment,
                )

        # Step 4: ConfirmGateway — ConfirmationKind.IRREVERSIBLE 需要最终确认
        if assessment.confirmation != ConfirmationKind.NONE:
            request = self._confirm_gateway.create_request(
                tool_name=tool_name,
                arguments=arguments,
                confirmation_kind=assessment.confirmation,
                sandbox_tier=effective_tier,
                reason=assessment.reason,
                workspace_tag=workspace_tag,
            )
            confirm_result = self._confirm_gateway.process_request(request)
            result = GateDecision(
                allowed=confirm_result.allowed,
                sandbox_tier=effective_tier,
                confirmation=assessment.confirmation,
                confirmation_request=request,
                reason=confirm_result.reason or assessment.reason,
                risk_assessment=assessment,
            )
        else:
            result = GateDecision(
                allowed=True,
                sandbox_tier=effective_tier,
                confirmation=ConfirmationKind.NONE,
                reason=assessment.reason,
                risk_assessment=assessment,
            )

        # Step 4.5: 租户级 tier/confirmation 覆盖（只收紧、不放松）
        if tenant_policy is not None:
            result_sandbox_tier = tenant_policy.effective_tier(tool_name, result.sandbox_tier.value if isinstance(result.sandbox_tier, str) else result.sandbox_tier.value)
            result_confirm = tenant_policy.effective_confirmation(tool_name, result.confirmation.value if isinstance(result.confirmation, str) else result.confirmation.value)
            if result_sandbox_tier != result.sandbox_tier.value:
                result = dataclasses.replace(result, sandbox_tier=SandboxTier(result_sandbox_tier))
            if result_confirm != result.confirmation.value:
                result = dataclasses.replace(result, confirmation=ConfirmationKind(result_confirm))

        return result
