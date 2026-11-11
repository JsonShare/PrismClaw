"""Gateway component factory — called by assembly bundle builders to avoid
assembly→app.gateway upward imports."""
from __future__ import annotations

import logging
from typing import Any

from prismclaw.app.gateway.event_codec import decode_forward_event
from prismclaw.infra.settings import Settings

logger = logging.getLogger(__name__)


def build_gateway_components(
    redis_client: Any,
    worker_id: str,
    settings: Settings,
    *,
    app: Any,
) -> tuple[Any, Any]:
    """Build (gateway_router, forward_consumer) for session affinity.

    ``app`` is only held as a lazy reference: ``web_deps`` (and therefore the
    ``ConnectionRegistry``) is rebuilt on plugin reload, so the local
    dispatcher must resolve it at delivery time rather than capture the
    registry object that exists during startup.
    """
    from prismclaw.app.gateway.affinity import AffinityRegistry
    from prismclaw.app.gateway.forwarder import ForwardConsumer, ForwardPublisher
    from prismclaw.app.gateway.router import GatewayRouter
    from prismclaw.app.web.connection import ConnectionRegistry

    affinity_registry = AffinityRegistry(
        redis_client,
        worker_id=worker_id,
        ttl_seconds=settings.affinity.ttl_seconds,
    )
    forward_publisher = ForwardPublisher(
        redis_client,
        prefix=settings.affinity.forward_prefix,
    )
    gateway_router = GatewayRouter(affinity_registry, forward_publisher)

    def _local_registry() -> ConnectionRegistry | None:
        web_deps = getattr(app.state, "web_deps", None)
        registry = getattr(web_deps, "connection_registry", None)
        return registry if isinstance(registry, ConnectionRegistry) else None

    async def _dispatch_forwarded_event(payload: dict) -> None:
        """Deliver a forwarded frame to this worker's local sockets.

        Sends straight to ``ws.send_json`` — never back through ``send_event``
        — otherwise the receiving worker would route the frame again and the
        two workers would ping-pong it forever.
        """
        decoded = decode_forward_event(payload)
        if decoded is None:
            logger.warning("gateway forward: dropping malformed payload %r", payload)
            return
        user_id, frame = decoded
        registry = _local_registry()
        if registry is None:
            return
        for ws in registry.get_connections(user_id):
            try:
                await ws.send_json(frame)
            except Exception:  # noqa: BLE001 - 单条连接失败不影响其它连接
                logger.warning(
                    "gateway forward: local delivery failed (user_id=%s)",
                    user_id,
                    exc_info=True,
                )

    forward_consumer = ForwardConsumer(
        redis_client,
        worker_id=worker_id,
        handler_fn=_dispatch_forwarded_event,
        prefix=settings.affinity.forward_prefix,
    )
    return gateway_router, forward_consumer


def build_run_ownership_store(
    redis_client: Any,
    settings: Settings,
) -> Any:
    """Build RunOwnershipStore for session affinity ownership tracking."""
    from prismclaw.app.gateway.run_ownership import RunOwnershipStore

    ownership_ttl_seconds = max(
        int(settings.affinity.stale_threshold) * 2,
        int(settings.affinity.heartbeat_interval) * 3,
    )
    return RunOwnershipStore(
        redis_client,
        key_prefix=settings.redis.key_prefix,
        ttl_seconds=ownership_ttl_seconds,
        heartbeat_interval_seconds=max(1, int(settings.affinity.heartbeat_interval)),
    )
