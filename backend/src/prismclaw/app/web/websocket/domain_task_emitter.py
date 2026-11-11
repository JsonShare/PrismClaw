"""DomainTaskEmitter 端口的 web 层实现 — 复用 broadcast_to_user 的用户级广播。"""
from __future__ import annotations

from typing import Any

from prismclaw.app.web.websocket.websocket import broadcast_to_user


class WsDomainTaskEmitter:
    """经 broadcast_to_user 向用户全部 WS 连接推送域任务事件帧。

    帧信封由 broadcast_to_user 统一构造：{type, conversation_id: null, seq: 0, ts, data}。
    无状态：每次广播时从 app.state.web_deps 现取 connection_registry，
    因此 web_deps 重建（插件热重载）不影响已接线的 emitter 实例。
    """

    def __init__(self, app: Any) -> None:
        self._app = app

    async def broadcast(self, user_id: str, event_type: str, data: dict[str, Any]) -> None:
        await broadcast_to_user(self._app, user_id, event_type, data)
