"""policies — 工作区标签、聚合防护。"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from prismclaw.schema.content.workspace import SandboxTier
from prismclaw.engine.security.gate.types import AggregateStoreProtocol


# ── workspace_tag ───────────────────────────────────────────────────────────

class DataResidency(str, Enum):
    """数据驻留区域。"""
    CN = "CN"
    EU = "EU"
    US = "US"


@dataclass(frozen=True)
class WorkspaceTag:
    """工作区安全标签。

    每个工作区绑定一个 WorkspaceTag，基于 tag 的标志位
    + ToolSafetyMeta 做交叉校验。安全属性自描述——通过布尔标志位
    而非域标签（kind）驱动安全路由。

    标志位：
      allow_external_write: 是否允许外部写操作（默认 False，fail-closed）
      require_consent: 敏感用途工具是否需要用户同意（默认 True）
      restrict_chat_sensitive_data: 是否隐藏敏感数据工具（默认 True）
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
