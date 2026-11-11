"""P4-OPS-03：PreWarmPool 动态伸缩。

预热池管理：提前创建容器，减少冷启动延迟。

动态伸缩策略：
  1. 日均启动量预测：基于历史 7 天启动量的滑动平均
  2. 周末/月末峰值因子：月末最后一个工作日 × 1.5，周末 × 0.5
  3. 工作日 9:00 峰值扩容：9:00-11:00 目标池 × 1.5
  4. 夜间自动缩容：22:00-07:00 目标池 × 0.3
  5. 资源利用率 ≥ 60%：过低则缩容，过高则扩容

资源利用率 = 过去 1h 实际启动量 / (目标池大小 × 1h)
"""
from __future__ import annotations

import calendar
import logging
import math
import time
from collections import deque
from dataclasses import dataclass

from typing import Any

from prismclaw.engine.runtime.resource_tiers import get_resource_tier

logger = logging.getLogger(__name__)


# ── 数据类 ──────────────────────────────────────────────────────────


@dataclass
class PoolMetrics:
    """预热池指标。"""
    target_size: int               # 目标池大小
    current_size: int              # 当前池大小
    startup_count_1h: int          # 过去 1h 启动量
    startup_count_24h: int         # 过去 24h 启动量
    utilization_rate: float        # 资源利用率
    peak_factor: float             # 峰值因子（1.0=基准）
    last_adjusted_at: float = 0.0


@dataclass
class StartupRecord:
    """启动记录（用于历史统计）。"""
    timestamp: float
    template_id: str


# ── PreWarmPool ────────────────────────────────────────────────────


class PreWarmPool:
    """预热池动态伸缩。

    目标池大小 = base_prewarm_count × peak_factor × time_factor
    其中：
      - base_prewarm_count: ResourceTier.prewarm_count
      - peak_factor: 月末/工作日峰值
      - time_factor: 时段因子（9:00 峰值 / 夜间缩容）
    """

    def __init__(
        self,
        *,
        registry: Any = None,
        history_window_hours: int = 168,  # 7 天历史窗口
        min_pool_size: int = 1,
        max_pool_size: int = 20,
        utilization_target: float = 0.6,  # 60% 资源利用率
        maxlen: int = 10000,
    ) -> None:
        self._registry = registry
        self._history_window = history_window_hours * 3600
        self._min_size = min_pool_size
        self._max_size = max_pool_size
        self._util_target = utilization_target

        # 启动历史
        self._startup_history: deque[StartupRecord] = deque(maxlen=maxlen)
        # 当前池大小（按 template_id）
        self._current_sizes: dict[str, int] = {}
        # 最后调整时间
        self._last_adjusted: dict[str, float] = {}

    def record_startup(self, template_id: str) -> None:
        """记录一次工作区启动。"""
        self._startup_history.append(
            StartupRecord(timestamp=time.time(), template_id=template_id)
        )
        # 清理过期历史
        cutoff = time.time() - self._history_window
        while self._startup_history and self._startup_history[0].timestamp < cutoff:
            self._startup_history.popleft()

    def compute_target_size(self, template_id: str) -> int:
        """计算目标池大小。"""
        tier = get_resource_tier(template_id)
        base = tier.prewarm_count

        # 峰值因子
        peak = self._compute_peak_factor()

        # 时段因子
        time_factor = self._compute_time_factor()

        # 利用率调整
        util_factor = self._compute_utilization_factor(template_id)

        # 日均启动量下限
        daily_avg = self._daily_average(template_id)
        min_from_demand = max(1, int(daily_avg / 8))  # 8h 工作日，每小时需求

        target = max(
            min_from_demand,
            int(math.ceil(base * peak * time_factor * util_factor)),
        )

        return max(self._min_size, min(self._max_size, target))

    def get_metrics(self, template_id: str) -> PoolMetrics:
        """获取预热池指标。"""
        now = time.time()

        # 统计过去 1h / 24h 启动量
        count_1h = sum(
            1 for r in self._startup_history
            if r.template_id == template_id and r.timestamp >= now - 3600
        )
        count_24h = sum(
            1 for r in self._startup_history
            if r.template_id == template_id and r.timestamp >= now - 86400
        )

        current = self._current_sizes.get(template_id, get_resource_tier(template_id).prewarm_count)
        target = self.compute_target_size(template_id)

        # 利用率 = 过去 1h 启动量 / (目标池大小)
        utilization = count_1h / max(1, current) if current > 0 else 0.0

        return PoolMetrics(
            target_size=target,
            current_size=current,
            startup_count_1h=count_1h,
            startup_count_24h=count_24h,
            utilization_rate=round(utilization, 4),
            peak_factor=self._compute_peak_factor(),
            last_adjusted_at=self._last_adjusted.get(template_id, 0.0),
        )

    def adjust_pool(self, template_id: str) -> int:
        """调整预热池大小，返回新的目标大小。"""
        target = self.compute_target_size(template_id)
        current = self._current_sizes.get(template_id, get_resource_tier(template_id).prewarm_count)

        if target != current:
            logger.info(
                "PreWarmPool: adjusting %s from %d to %d (peak=%.1f, time=%.1f)",
                template_id, current, target,
                self._compute_peak_factor(), self._compute_time_factor(),
            )
            self._current_sizes[template_id] = target

        self._last_adjusted[template_id] = time.time()
        return target

    def get_image_for_template(self, template_id: str, tenant_id: str | None = None) -> str:
        """通过 TemplateRegistry 获取 template_id 对应的 image_tag。

        如果 tenant_id 提供且注册表中存在对应的机构定制模板，优先使用机构模板；
        否则回退到基础模板。

        fail-closed：如果 registry 未注入、template_id 未找到、
        或 image_tag 为 None，均抛出 RuntimeError。
        """
        if self._registry is None:
            raise RuntimeError(
                f"PreWarmPool: TemplateRegistry not injected, "
                f"cannot resolve image for template_id={template_id}"
            )

        # 如果提供了 tenant_id，优先查找机构定制模板
        if tenant_id is not None:
            tenant_template_id = self._registry.tenant_template_id(
                template_id, tenant_id
            )
            if tenant_template_id is not None:
                tenant_image_tag = self._registry.get_image_tag(tenant_template_id)
                if tenant_image_tag is not None:
                    return tenant_image_tag

        image_tag = self._registry.get_image_tag(template_id)
        if image_tag is None:
            raise RuntimeError(
                f"PreWarmPool: no image_tag found for template_id={template_id}"
            )

        return image_tag

    # ── 内部计算 ──────────────────────────────────────────────────────

    def _compute_peak_factor(self) -> float:
        """计算峰值因子。月末最后一个工作日 × 1.5。"""
        now = time.time()
        local_time = time.localtime(now)
        last_day = calendar.monthrange(local_time.tm_year, local_time.tm_mon)[1]

        # 月末最后一个工作日
        if local_time.tm_mday >= last_day - 2 and local_time.tm_wday < 5:
            return 1.5

        # 周末
        if local_time.tm_wday >= 5:
            return 0.5

        return 1.0

    def _compute_time_factor(self) -> float:
        """计算时段因子。"""
        local_time = time.localtime()
        hour = local_time.tm_hour

        # 工作日 9:00-11:00 峰值
        if 9 <= hour < 11 and local_time.tm_wday < 5:
            return 1.5

        # 午间 12:00-14:00 中等
        if 12 <= hour < 14:
            return 1.0

        # 夜间 22:00-07:00 缩容
        if hour >= 22 or hour < 7:
            return 0.3

        return 1.0

    def _compute_utilization_factor(self, template_id: str) -> float:
        """根据资源利用率调整因子。"""
        now = time.time()
        count_1h = sum(
            1 for r in self._startup_history
            if r.template_id == template_id and r.timestamp >= now - 3600
        )
        current = self._current_sizes.get(template_id, get_resource_tier(template_id).prewarm_count)

        if current == 0:
            return 1.5  # 空池，紧急扩容

        utilization = count_1h / current

        # 利用率过低 → 缩容
        if utilization < self._util_target * 0.5:
            return 0.7

        # 利用率过高 → 扩容
        if utilization > self._util_target * 1.5:
            return 1.3

        return 1.0

    def _daily_average(self, template_id: str) -> float:
        """计算日均启动量（7 天滑动平均）。"""
        now = time.time()

        # 统计 7 天内该 template_id 的启动量
        count_7d = sum(
            1 for r in self._startup_history
            if r.template_id == template_id and r.timestamp >= now - 7 * 86400
        )

        # 至少需要 1 天的数据才有意义
        if count_7d == 0:
            return float(get_resource_tier(template_id).prewarm_count)

        return count_7d / 7.0
