"""事件循环停顿观测器。

异步应用最危险的故障不是「某个调用慢」，而是「事件循环被同步阻塞」：
一旦线程被同步 I/O（文件写入、zip 压缩、锁竞争）占住，所有 ``await``
都无法被调度——心跳迟到、leader 锁过期、请求超时、日志监听队列积压，
全是同一个根因的不同表象。

``EventLoopLagMonitor`` 通过测量 ``asyncio.sleep`` 的实际漂移把停顿量化，
让「事件循环被阻塞」在发生时立刻可见，而不是等到下游超时后才倒推。
它只做观测（不修改任何业务路径），成本约为每 ``interval_s`` 一次
``monotonic`` 与一次 sleep。
"""
from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_S = 0.25
_DEFAULT_WARN_MS = 250.0
_DEFAULT_SEVERE_MS = 2000.0
# 同一级别告警的最小间隔，避免一次长时间停顿刷屏（停顿期间产生的记录
# 本身也要走日志队列，刷屏会放大问题）
_WARN_COOLDOWN_S = 10.0
_SUMMARY_INTERVAL_S = 300.0


class EventLoopLagMonitor:
    """周期性采样事件循环漂移并记录分级告警与汇总。"""

    def __init__(
        self,
        *,
        interval_s: float = _DEFAULT_INTERVAL_S,
        warn_ms: float = _DEFAULT_WARN_MS,
        severe_ms: float = _DEFAULT_SEVERE_MS,
        summary_interval_s: float = _SUMMARY_INTERVAL_S,
    ) -> None:
        self._interval_s = max(0.01, interval_s)
        self._warn_ms = warn_ms
        self._severe_ms = max(severe_ms, warn_ms)
        self._summary_interval_s = summary_interval_s
        self._samples = 0
        self._over_warn = 0
        self._over_severe = 0
        self._max_lag_ms = 0.0
        self._last_warn_at = 0.0
        self._next_summary_at = time.monotonic() + summary_interval_s

    @property
    def max_lag_ms(self) -> float:
        return self._max_lag_ms

    async def run(self) -> None:
        """采样主循环。由 TaskManager.spawn 托管，停止靠取消。"""
        logger.info("loop_lag_monitor.start interval_s=%s", self._interval_s)
        try:
            while True:
                started = time.monotonic()
                await asyncio.sleep(self._interval_s)
                lag_ms = (time.monotonic() - started - self._interval_s) * 1000.0
                self._record(max(0.0, lag_ms))
        except asyncio.CancelledError:
            return

    def _record(self, lag_ms: float) -> None:
        self._samples += 1
        if lag_ms > self._max_lag_ms:
            self._max_lag_ms = lag_ms

        if lag_ms >= self._severe_ms:
            self._over_severe += 1
        elif lag_ms >= self._warn_ms:
            self._over_warn += 1

        now = time.monotonic()
        if lag_ms >= self._warn_ms and now - self._last_warn_at >= _WARN_COOLDOWN_S:
            self._last_warn_at = now
            level = logger.warning if lag_ms >= self._severe_ms else logger.info
            level(
                "runtime.event_loop.lag lag_ms=%.0f threshold_ms=%.0f samples=%d",
                lag_ms,
                self._warn_ms,
                self._samples,
            )

        if now >= self._next_summary_at:
            self._next_summary_at = now + self._summary_interval_s
            if self._over_warn or self._over_severe:
                logger.info(
                    "runtime.event_loop.lag_summary samples=%d over_warn=%d "
                    "over_severe=%d max_lag_ms=%.0f",
                    self._samples,
                    self._over_warn,
                    self._over_severe,
                    self._max_lag_ms,
                )
            self._over_warn = 0
            self._over_severe = 0
            self._max_lag_ms = 0.0


__all__ = ["EventLoopLagMonitor"]
