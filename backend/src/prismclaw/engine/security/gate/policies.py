"""policies — 租户策略、工作区标签、聚合防护、MFA 合并。"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from prismclaw.schema.content.workspace import SandboxTier, ConfirmationKind
from prismclaw.engine.security.gate.types import AggregateStoreProtocol


logger = logging.getLogger(__name__)


# ── workspace_tag ───────────────────────────────────────────────────────────

class DataResidency(str, Enum):
    """数据驻留区域。"""
    CN = "CN"
    EU = "EU"
    US = "US"


@dataclass(frozen=True)
class WorkspaceTag:
    """工作区安全标签。

    每个工作区绑定一个 WorkspaceTag，SafetyGate 基于 tag 的标志位
    + ToolSafetyMeta 做交叉校验。安全属性自描述——通过布尔标志位
    而非域标签（kind）驱动安全路由。

    标志位：
      allow_external_write: 是否允许外部写操作（默认 False，fail-closed）
      require_consent: 敏感用途工具是否需要用户同意（默认 True）
      restrict_chat_sensitive_data: chat 模式是否隐藏敏感数据工具（默认 True）
      four_eyes_required: 强制 4-eyes 审批
    运维字段：
      sandbox_tier: 容器安全等级
      template_id: 容器模板标识（替代 kind 作为资源分配键）
    """
    workspace_id: str
    owner_id: str
    data_residency: DataResidency
    allow_external_write: bool = False
    require_consent: bool = True
    restrict_chat_sensitive_data: bool = True
    four_eyes_required: bool = False
    approver_dept: str = ""
    four_eyes_delegate_ids: list[str] = field(default_factory=list)
    sandbox_tier: SandboxTier = SandboxTier.STANDARD
    template_id: str = "default-v1"


class WorkspaceTagValidator:
    """WorkspaceTag 校验器。"""

    @staticmethod
    def validate(tag: WorkspaceTag) -> list[str]:
        """校验 WorkspaceTag，返回错误列表（空=通过）。"""
        errors: list[str] = []

        if not tag.workspace_id:
            errors.append("workspace_id is required")
        if not tag.owner_id:
            errors.append("owner_id is required")
        if tag.four_eyes_required and not tag.four_eyes_delegate_ids and not tag.approver_dept:
            errors.append("four_eyes_required but no delegate_ids or approver_dept configured")

        return errors

    @staticmethod
    def validate_or_raise(tag: WorkspaceTag) -> None:
        """校验 WorkspaceTag，失败则抛 ValueError。"""
        errors = WorkspaceTagValidator.validate(tag)
        if errors:
            raise ValueError(f"WorkspaceTag validation failed: {'; '.join(errors)}")


# ── aggregate_guard ─────────────────────────────────────────────────────────

class AggregateAction(str, Enum):
    """聚合防护动作。"""
    ALLOW = "allow"         # 未超阈值
    WARN = "warn"           # 接近阈值（80%）
    ESCALATE = "escalate"   # 超过阈值，升档
    BLOCK = "block"         # 超过硬上限，阻断


@dataclass(frozen=True)
class AggregateThreshold:
    """单个计数器的阈值配置。"""
    counter: str
    soft_threshold: int     # 超过此值 → WARN → ESCALATE
    hard_threshold: int     # 超过此值 → BLOCK
    window_seconds: int     # 滑动窗口时长
    escalate_tier: SandboxTier = SandboxTier.TIGHT  # 升档目标


@dataclass(frozen=True)
class AggregateDecision:
    """聚合防护决策。"""
    action: AggregateAction
    counter: str
    current_count: int
    threshold: int
    escalate_tier: SandboxTier | None = None
    reason: str = ""


# ── 默认阈值配置 ──────────────────────────────────────────────────────

DEFAULT_THRESHOLDS: list[AggregateThreshold] = [
    AggregateThreshold(
        counter="restricted_rows_exported_1h",
        soft_threshold=25,
        hard_threshold=50,
        window_seconds=3600,
        escalate_tier=SandboxTier.TIGHT,
    ),
    AggregateThreshold(
        counter="emails_sent_1h",
        soft_threshold=20,
        hard_threshold=40,
        window_seconds=3600,
        escalate_tier=SandboxTier.TIGHT,
    ),
    AggregateThreshold(
        counter="network_bytes_out_1h",
        soft_threshold=50_000_000,   # 50MB
        hard_threshold=100_000_000,  # 100MB
        window_seconds=3600,
        escalate_tier=SandboxTier.TIGHT,
    ),
]


class SessionAggregateGuard:
    """Session 级聚合防护。

    基于 AggregateStoreProtocol 的滑动窗口计数器实现。
    支持 Redis 后端（生产）和 InMemoryAggregateStore（开发/测试）。
    """

    def __init__(
        self,
        store: AggregateStoreProtocol,
        *,
        thresholds: list[AggregateThreshold] | None = None,
    ) -> None:
        self._store = store
        self._thresholds = {t.counter: t for t in (thresholds or DEFAULT_THRESHOLDS)}

    async def check(
        self,
        session_id: str,
        counter: str,
        delta: int = 1,
    ) -> AggregateDecision:
        """检查计数器是否超过阈值。

        原子递增 + 阈值检查，返回 AggregateDecision。
        """
        threshold_config = self._thresholds.get(counter)
        if threshold_config is None:
            # 未知计数器：不限制
            return AggregateDecision(
                action=AggregateAction.ALLOW,
                counter=counter,
                current_count=0,
                threshold=0,
                reason="unknown_counter",
            )

        # Use async check_and_incr if available (Redis), else sync fallback
        if hasattr(self._store, "async_check_and_incr"):
            current_count, exceeded = await self._store.async_check_and_incr(
                key=f"{session_id}:{counter}",
                window_seconds=threshold_config.window_seconds,
                threshold=threshold_config.hard_threshold,
            )
        else:
            current_count, exceeded = self._store.check_and_incr(
                session_id=session_id,
                counter=counter,
                threshold=threshold_config.hard_threshold,
                window_seconds=threshold_config.window_seconds,
                delta=delta,
            )

        # 硬上限阻断
        if current_count > threshold_config.hard_threshold:
            return AggregateDecision(
                action=AggregateAction.BLOCK,
                counter=counter,
                current_count=current_count,
                threshold=threshold_config.hard_threshold,
                reason=f"aggregate_block: {counter}={current_count} > hard_threshold={threshold_config.hard_threshold}",
            )

        # 软阈值升档
        if current_count > threshold_config.soft_threshold:
            return AggregateDecision(
                action=AggregateAction.ESCALATE,
                counter=counter,
                current_count=current_count,
                threshold=threshold_config.soft_threshold,
                escalate_tier=threshold_config.escalate_tier,
                reason=f"aggregate_escalate: {counter}={current_count} > soft_threshold={threshold_config.soft_threshold}",
            )

        # 接近阈值告警（80%）
        warn_threshold = int(threshold_config.soft_threshold * 0.8)
        if current_count >= warn_threshold:
            return AggregateDecision(
                action=AggregateAction.WARN,
                counter=counter,
                current_count=current_count,
                threshold=warn_threshold,
                reason=f"aggregate_warn: {counter}={current_count} approaching soft_threshold={threshold_config.soft_threshold}",
            )

        return AggregateDecision(
            action=AggregateAction.ALLOW,
            counter=counter,
            current_count=current_count,
            threshold=threshold_config.soft_threshold,
        )

    def get_count(self, session_id: str, counter: str) -> int:
        """获取当前计数。"""
        return self._store.get(session_id, counter)

    def reset(self, session_id: str) -> None:
        """重置会话计数器。"""
        self._store.reset(session_id)


class RedisAggregateStore(AggregateStoreProtocol):
    """Redis 分布式聚合存储（多节点 Swarm 安全）。

    使用 Redis Pipeline INCRBY + EXPIRE 实现原子计数器，
    确保多节点部署下的计数一致性。
    """

    def __init__(self, redis_url: str = "redis://localhost:6379/0", *, key_prefix: str = "prismclaw:agg:"):
        self._redis_url = redis_url
        self._key_prefix = key_prefix
        self._redis: Any = None

    async def _get_redis(self):
        if self._redis is None:
            try:
                import redis.asyncio as aioredis
                self._redis = aioredis.from_url(self._redis_url)
            except ImportError:
                raise RuntimeError("redis package not installed; run: pip install redis")
        return self._redis

    # ── Sync AggregateStoreProtocol 接口（fallback，不适用于 Redis）──────

    def increment(self, session_id: str, counter: str, delta: int = 1) -> int:
        raise NotImplementedError("RedisAggregateStore does not support sync increment; use async_check_and_incr")

    def get(self, session_id: str, counter: str) -> int:
        raise NotImplementedError("RedisAggregateStore does not support sync get")

    def reset(self, session_id: str) -> None:
        raise NotImplementedError("RedisAggregateStore does not support sync reset")

    def check_and_incr(self, session_id: str, counter: str, threshold: int, window_seconds: int, delta: int = 1) -> tuple[int, bool]:
        raise NotImplementedError("RedisAggregateStore does not support sync check_and_incr; use async_check_and_incr")

    # ── Async 接口 ──────────────────────────────────────────────────────

    async def async_check_and_incr(self, key: str, window_seconds: int, threshold: int) -> tuple[int, bool]:
        """原子递增 + 阈值检查（Redis Pipeline）。"""
        r = await self._get_redis()
        full_key = f"{self._key_prefix}{key}"
        async with r.pipeline() as pipe:
            count = await pipe.incrby(full_key, 1).expire(full_key, window_seconds).execute()
            current = count[0]
            exceeded = current > threshold
            return current, exceeded

    async def close(self) -> None:
        if self._redis:
            await self._redis.close()


def create_aggregate_store(store_type: str = "memory", *, redis_url: str = "", **kwargs) -> AggregateStoreProtocol:
    """聚合存储工厂。"""
    if store_type == "redis":
        return RedisAggregateStore(redis_url=redis_url, **kwargs)
    from prismclaw.engine.security.gate.types import InMemoryAggregateStore
    return InMemoryAggregateStore(**kwargs)


# ── tenant_policy ───────────────────────────────────────────────────────────

class TenantPolicyOverride(BaseModel):
    """租户级安全策略覆盖。

    覆盖优先级：全局规则 → 租户覆盖 → 最终决策。
    覆盖只能收紧，不能放宽——即 deny 不可被租户覆盖为 allow，
    但 allow 可以被租户覆盖为 deny。
    """
    tenant_id: str

    # 工具可见性
    tool_deny_list: list[str] = Field(default_factory=list)
    # 确认策略覆盖：tool_name → ConfirmationKind（只能升级，不能降级）
    confirm_policy_overrides: dict[str, ConfirmationKind] = Field(default_factory=dict)
    # 网络策略覆盖
    allowed_domains_override: list[str] | None = None   # None = 不覆盖
    denied_domains_extra: list[str] = Field(default_factory=list)
    # SandboxTier 覆盖：tool_name → SandboxTier（只能升级，不能降级）
    sandbox_tier_overrides: dict[str, SandboxTier] = Field(default_factory=dict)

    def effective_tier(self, tool_name: str, base_tier: SandboxTier) -> SandboxTier:
        """计算生效 tier——取 base_tier 和租户覆盖中的更高者。"""
        override = self.sandbox_tier_overrides.get(tool_name)
        if override is None:
            return base_tier
        tier_order = {SandboxTier.STANDARD: 0, SandboxTier.TIGHT: 1, SandboxTier.STRICT: 2}
        if tier_order.get(override, 0) > tier_order.get(base_tier, 0):
            return override
        return base_tier

    def effective_confirmation(
        self, tool_name: str, base_confirmation: ConfirmationKind,
    ) -> ConfirmationKind:
        """计算生效确认级别——取 base 和租户覆盖中的更高者。"""
        override = self.confirm_policy_overrides.get(tool_name)
        if override is None:
            return base_confirmation
        kind_order = {
            ConfirmationKind.NONE: 0,
            ConfirmationKind.SOFT_CONFIRM: 1,
            ConfirmationKind.IRREVERSIBLE: 2,
        }
        if kind_order.get(override, 0) > kind_order.get(base_confirmation, 0):
            return override
        return base_confirmation

    def is_tool_denied(self, tool_name: str) -> bool:
        return tool_name in self.tool_deny_list


class TenantPolicyStore:
    """租户策略存储（带 300 秒 TTL 缓存）。"""

    _CACHE_TTL = 300.0

    def __init__(self, pool: Any) -> None:
        self._pool = pool
        self._cache: dict[str, tuple[float, TenantPolicyOverride | None]] = {}
        import time
        self._time = time.monotonic

    async def get(self, tenant_id: str) -> TenantPolicyOverride | None:
        """获取租户策略，缓存命中时不查 PG。"""
        now = self._time()
        cached = self._cache.get(tenant_id)
        if cached is not None:
            ts, policy = cached
            if now - ts < self._CACHE_TTL:
                return policy

        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT policy_json FROM pc_tenant_policies WHERE tenant_id = $1",
                    tenant_id,
                )
        except Exception:
            # PG 不可用时返回缓存（即使过期）
            if cached is not None:
                return cached[1]
            return None

        if row is None:
            self._cache[tenant_id] = (now, None)
            return None

        import json
        policy = TenantPolicyOverride(tenant_id=tenant_id, **json.loads(row["policy_json"]))
        self._cache[tenant_id] = (now, policy)
        return policy

    def invalidate(self, tenant_id: str) -> None:
        """使缓存失效。"""
        self._cache.pop(tenant_id, None)


# ── mfa ─────────────────────────────────────────────────────────────────────

@runtime_checkable
class MfaProvider(Protocol):
    """MFA 凭据校验协议。"""

    is_production_ready: bool

    def verify_credential(self, *, user_id: str, credential_id: str) -> bool:
        """校验 MFA 凭据合法性。

        返回 True 表示凭据有效且归属该用户。
        """
        ...


class NullMfaProvider:
    """开发/测试用空实现。

    不做任何真实校验，仅记录警告。``is_production_ready=False`` 用于
    让 ConfirmGateway 在 production 下 fail-closed。
    """

    is_production_ready: bool = False

    def verify_credential(self, *, user_id: str, credential_id: str) -> bool:
        logger.warning(
            "NullMfaProvider.verify_credential called for user=%s (dev/test only)",
            user_id,
        )
        return True


class WebAuthnMfaProvider:
    """生产用 WebAuthn/FIDO2 MFA provider。

    通过环境变量配置断言验证端点与共享 HMAC 校验密钥。
    真实部署中，凭据由前端完成 WebAuthn assertion 后随
    ``ConfirmationResponse.mfa_credential_id`` 回传，此处对 assertion
    做服务端验证（签名校验 + 时间戳防重放）。

    配置（环境变量）：
      PRISMCLAW_MFA_VERIFY_URL      断言验证端点（如 /api/mfa/verify）
      PRISMCLAW_MFA_SHARED_SECRET   服务端用于校验 assertion 完整性的密钥

    当前实现为"可注入、可被测试、fail-closed"骨架：验证端点就绪前，
    若未配置 ``PRISMCLAW_MFA_VERIFY_URL`` 则视为未就绪并拒绝（不静默放行）。
    """

    is_production_ready: bool = True

    def __init__(
        self,
        *,
        verify_url: str | None = None,
        shared_secret: str | None = None,
    ) -> None:
        self._verify_url = verify_url or os.getenv("PRISMCLAW_MFA_VERIFY_URL") or ""
        self._shared_secret = shared_secret or os.getenv("PRISMCLAW_MFA_SHARED_SECRET") or ""
        # 未配置验证端点 → 视为未就绪，verify_credential 将拒绝（fail-closed）
        self._ready = bool(self._verify_url and self._shared_secret)

    @property
    def ready(self) -> bool:
        return self._ready

    def verify_credential(self, *, user_id: str, credential_id: str) -> bool:
        if not self._ready:
            logger.error(
                "WebAuthnMfaProvider not configured (missing PRISMCLAW_MFA_VERIFY_URL "
                "or PRISMCLAW_MFA_SHARED_SECRET); refusing MFA verification (fail-closed)"
            )
            return False
        if not credential_id:
            return False
        logger.info(
            "WebAuthnMfaProvider.verify_credential(user=%s) dispatched to %s",
            user_id,
            self._verify_url,
        )
        # TODO: 接入真实的 assertion 校验后再放行 HTTP 调用。
        # 当前返回 False 以强制显式配置，避免误以为已放行。
        return False


def build_mfa_provider() -> MfaProvider:
    """根据部署环境构造 MFA provider。

    生产与未知环境默认返回 WebAuthnMfaProvider（fail-closed）；
    显式 development/test 且无 MFA 配置时返回 NullMfaProvider。
    """
    env = os.getenv("PRISMCLAW_DEPLOYMENT_ENV", "development").lower()
    if env in ("development", "test", "dev"):
        if not (os.getenv("PRISMCLAW_MFA_VERIFY_URL") and os.getenv("PRISMCLAW_MFA_SHARED_SECRET")):
            return NullMfaProvider()
    return WebAuthnMfaProvider()
