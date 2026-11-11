"""Dream dispatch 启动服务 — 从 wiring/bundles/runtime.py 迁出"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, NamedTuple

logger = logging.getLogger(__name__)


class DreamStartResult(NamedTuple):
    dream_dispatch_consumer: Any = None
    dream_dispatch_health_task_id: Any = None


async def start_dream_dispatch_if_available(
    *,
    app: Any,
    runner_deps: Any,
    infra: Any,
    settings: Any,
    dream_health_loop: Callable[..., Awaitable[None]],
    dream_health_interval_s: float,
) -> DreamStartResult:
    """启动 dream dispatch consumer 和 health loop（如果配置启用且前置条件满足）"""
    from prismclaw.engine.dream.dispatch.dispatch import DreamDispatchConsumer

    dream_dispatch_consumer = None
    dream_dispatch_health_task_id = None

    dream_boundary = getattr(runner_deps.dream, "boundary", None) if runner_deps.dream else None
    process_job = getattr(dream_boundary, "run_dispatched_job", None)
    if callable(process_job) and infra.dream_dispatch_queue is not None:
        dream_dispatch_consumer = DreamDispatchConsumer(
            queue=infra.dream_dispatch_queue,
            task_manager=infra.task_manager,
            consumer_name=infra.worker_id,
            process_job=process_job,
            # 回收阈值须远超最长 dream 任务时长（nightly 实测可达小时级）：
            # 任务仅在完成时 ack，阈值过短会把执行中的长任务当闲置消息回收重投。
            # 不再复用 affinity.stale_threshold（90s，语义是判 worker 死亡）。
            reclaim_idle_ms=60 * 60 * 1000,
        )
        infra.task_manager.spawn(
            "dream-dispatch-consumer",
            dream_dispatch_consumer.start(),
            category="dream",
            owner=infra.worker_id,
        )

    dream_ops = getattr(app.state, "dream_ops", None)
    if infra.dream_dispatch_queue is not None and dream_ops is not None:
        dream_dispatch_health_task_id = infra.task_manager.spawn(
            "dream-dispatch-health",
            dream_health_loop(
                dream_ops,
                interval_s=dream_health_interval_s,
            ),
            category="dream",
            owner=infra.worker_id,
        )

    return DreamStartResult(
        dream_dispatch_consumer=dream_dispatch_consumer,
        dream_dispatch_health_task_id=dream_dispatch_health_task_id,
    )
