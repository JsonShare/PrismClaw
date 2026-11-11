from __future__ import annotations

import logging
from typing import Any, Literal

from prismclaw.app.gateway.affinity import AffinityRegistry
from prismclaw.app.gateway.forwarder import ForwardPublisher

logger = logging.getLogger(__name__)

RouteResult = Literal["local", "forwarded"]


class GatewayRouter:
    def __init__(self, affinity: AffinityRegistry, forwarder: ForwardPublisher) -> None:
        self._affinity = affinity
        self._forwarder = forwarder

    @property
    def affinity(self) -> AffinityRegistry:
        return self._affinity

    @property
    def worker_id(self) -> str:
        return self._affinity.worker_id

    async def route(self, session_key: str, event_payload: dict[str, Any]) -> RouteResult:
        try:
            return await self._route_inner(session_key, event_payload)
        except Exception:
            # Routing is best-effort. redis.exceptions.ConnectionError does
            # NOT subclass the builtin ConnectionError, so an outage would
            # otherwise escape into send_event and break chat delivery.
            # CancelledError derives from BaseException and still propagates,
            # so shutdown is unaffected.
            logger.warning(
                "gateway router: redis error during routing for session_key=%s; processing locally",
                session_key,
                exc_info=True,
            )
            return "local"

    async def _route_inner(self, session_key: str, event_payload: dict[str, Any]) -> RouteResult:
        owner = await self._affinity.resolve(session_key)

        if self._affinity.is_mine(owner):
            await self._affinity.renew(session_key)
            return "local"

        if owner is None:
            claimed = await self._affinity.claim(session_key)
            if claimed:
                return "local"
            owner = await self._affinity.resolve(session_key)
            if owner is None or self._affinity.is_mine(owner):
                return "local"

        delivered = await self._forwarder.forward(owner, event_payload)
        if not delivered:
            logger.info(
                "gateway: target worker %s not reachable; force-claiming session_key=%s",
                owner,
                session_key,
            )
            await self._affinity.force_claim(session_key)
            return "local"

        return "forwarded"
