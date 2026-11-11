"""域任务状态事件 → 平台广播端口的通用适配器。

把 StatusEmitterMixin 发出的事件 dict 转成 DomainTaskEmitter.broadcast 调用。
域命名空间由 kind 参数化（legal / medical / 自建 agent），
workspace → user 映射由 store._build_status_event 在事件里携带 workspace_id，
adapter 直接用它当 user_id（resolve_user_workspace 默认恒等映射）。

registry 自动接线时会传 kind=bundle.name，域 bundle 手写接线时也可直接用。
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from prismclaw.schema.services.domain import DomainTaskEmitter

logger = logging.getLogger(__name__)

DOMAIN_TASK_EVENT_TYPE = "domain_task.updated"


def make_status_listener(
    emitter: DomainTaskEmitter,
    *,
    kind: str = "generic",
) -> Callable[[dict[str, Any]], Awaitable[None]]:
    """把 store 状态事件转成 WS 帧广播。

    Args:
        emitter: DomainTaskEmitter 协议实现
        kind: 域命名空间标识（bundle.name 值，如 legal / medical / my_agent）
    """

    async def on_status_event(event: dict[str, Any]) -> None:
        await emitter.broadcast(
            event.get("workspace_id", event.get("user_id")),
            DOMAIN_TASK_EVENT_TYPE,
            {
                "task_id": event["task_id"],
                "kind": kind,
                "workspace_id": event.get("workspace_id"),
                "status": event["status"],
                "stage": event.get("stage"),
                "progress": event.get("progress"),
                "updated_at": event.get("updated_at"),
            },
        )

    return on_status_event
