"""Hook Circuit Breaker - Hook 超时熔断机制

为 Hook 执行提供超时保护和熔断能力，防止：
1. 单个 Hook 长时间阻塞 Agent 主流程
2. Hook 失败后无限重试
3. 多个 Hook 同时超时导致雪崩

熔断状态机：
- CLOSED (正常) → OPEN (熔断) → HALF_OPEN (尝试恢复) → CLOSED
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    """熔断状态。"""

    CLOSED = "closed"          # 正常通行
    OPEN = "open"              # 熔断，拒绝所有请求
    HALF_OPEN = "half_open"    # 尝试恢复，允许少量请求


@dataclass
class CircuitBreakerConfig:
    """熔断器配置。"""

    failure_threshold: int = 5           # 连续失败次数阈值
    recovery_timeout: float = 30.0       # 熔断恢复时间（秒）
    half_open_max_requests: int = 3      # HALF_OPEN 状态允许的最大请求数
    request_timeout: float = 30.0        # 单个请求超时时间（秒）
    success_threshold: int = 3           # HALF_OPEN 状态连续成功次数恢复


class HookCircuitBreaker:
    """Hook 执行熔断器。

    保护 Hook 系统免受慢请求和故障级联影响。
    当某个 Hook 连续失败达到阈值时，自动熔断并拒绝后续请求。
    """

    def __init__(self, config: CircuitBreakerConfig | None = None) -> None:
        self._config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float | None = None
        self._half_open_requests = 0

    @property
    def state(self) -> CircuitState:
        """获取当前熔断状态。"""
        self._check_state_transition()
        return self._state

    def _check_state_transition(self) -> None:
        """检查并执行状态转换。"""
        if self._state == CircuitState.OPEN:
            if self._last_failure_time is not None:
                elapsed = time.time() - self._last_failure_time
                if elapsed >= self._config.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_requests = 0
                    self._success_count = 0
                    logger.info(
                        "Circuit breaker transitioning to HALF_OPEN state"
                    )

    def can_execute(self) -> bool:
        """检查是否允许执行新请求。"""
        self._check_state_transition()

        if self._state == CircuitState.CLOSED:
            return True

        if self._state == CircuitState.HALF_OPEN:
            return self._half_open_requests < self._config.half_open_max_requests

        return False

    def record_success(self) -> None:
        """记录一次成功执行。"""
        if self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            self._half_open_requests += 1

            if self._success_count >= self._config.success_threshold:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._success_count = 0
                self._half_open_requests = 0
                logger.info(
                    "Circuit breaker recovered to CLOSED state after %d successes",
                    self._config.success_threshold,
                )
        elif self._state == CircuitState.CLOSED:
            self._failure_count = 0

    def record_failure(self) -> None:
        """记录一次失败执行。"""
        self._failure_count += 1
        self._last_failure_time = time.time()

        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            logger.warning(
                "Circuit breaker OPEN: failure during HALF_OPEN state"
            )
        elif self._state == CircuitState.CLOSED:
            if self._failure_count >= self._config.failure_threshold:
                self._state = CircuitState.OPEN
                logger.warning(
                    "Circuit breaker OPEN: %d consecutive failures",
                    self._failure_count,
                )

    def reset(self) -> None:
        """手动重置熔断器。"""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = None
        self._half_open_requests = 0
        logger.info("Circuit breaker manually reset to CLOSED state")

    async def execute_with_protection(
        self,
        coro: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> tuple[Any, bool]:
        """带熔断保护执行协程。

        Returns:
            (result, was_executed) - was_executed 为 False 表示被熔断拒绝
        """
        if not self.can_execute():
            logger.warning("Circuit breaker is OPEN, rejecting request")
            return None, False

        if self._state == CircuitState.HALF_OPEN:
            self._half_open_requests += 1

        try:
            result = await asyncio.wait_for(
                coro(*args, **kwargs),
                timeout=self._config.request_timeout,
            )
            self.record_success()
            return result, True
        except asyncio.TimeoutError:
            logger.warning("Request timed out after %.1fs", self._config.request_timeout)
            self.record_failure()
            raise
        except Exception as exc:
            logger.error("Request failed: %s", exc)
            self.record_failure()
            raise

    def get_status(self) -> dict[str, Any]:
        """获取熔断器状态信息。"""
        return {
            "state": self._state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "last_failure_time": (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._last_failure_time))
                if self._last_failure_time
                else None
            ),
            "half_open_requests": self._half_open_requests,
            "config": {
                "failure_threshold": self._config.failure_threshold,
                "recovery_timeout": self._config.recovery_timeout,
                "request_timeout": self._config.request_timeout,
            },
        }


class HookCircuitBreakerManager:
    """多个 Hook 的熔断器管理器。

    为每个 (plugin_id, event_name, hook_id) 组合维护独立的熔断器。
    """

    def __init__(self, default_config: CircuitBreakerConfig | None = None) -> None:
        self._default_config = default_config or CircuitBreakerConfig()
        self._breakers: dict[str, HookCircuitBreaker] = {}

    def get_or_create(
        self,
        plugin_id: str,
        event_name: str,
        hook_id: str,
        config: CircuitBreakerConfig | None = None,
    ) -> HookCircuitBreaker:
        """获取或创建熔断器。"""
        key = f"{plugin_id}:{event_name}:{hook_id}"
        if key not in self._breakers:
            self._breakers[key] = HookCircuitBreaker(config or self._default_config)
        return self._breakers[key]

    def get_all_status(self) -> dict[str, dict[str, Any]]:
        """获取所有熔断器状态。"""
        return {
            key: cb.get_status()
            for key, cb in self._breakers.items()
        }

    def reset_all(self) -> None:
        """重置所有熔断器。"""
        for cb in self._breakers.values():
            cb.reset()

    def reset(self, plugin_id: str, event_name: str, hook_id: str) -> None:
        """重置特定熔断器。"""
        key = f"{plugin_id}:{event_name}:{hook_id}"
        if key in self._breakers:
            self._breakers[key].reset()
