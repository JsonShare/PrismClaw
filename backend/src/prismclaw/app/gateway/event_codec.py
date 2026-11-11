"""Wire format for events forwarded between gateway workers.

``ForwardPublisher`` publishes a plain JSON dict to a per-worker Redis channel
and ``ForwardConsumer`` hands the decoded dict to the local handler. This
module owns that shape so the producer (``connection.send_event`` /
``broadcast_to_user``) and the consumer (``factory``'s local dispatcher)
cannot drift apart.
"""
from __future__ import annotations

from typing import Any

WEB_SESSION_PREFIX = "web:"


def web_session_key(user_id: str) -> str:
    return f"{WEB_SESSION_PREFIX}{user_id}"


def encode_forward_event(user_id: str, frame: dict[str, Any]) -> dict[str, Any]:
    """Wrap a client-facing envelope for delivery on the affinity owner."""
    return {
        "session_key": web_session_key(user_id),
        "user_id": user_id,
        "frame": frame,
    }


def decode_forward_event(payload: Any) -> tuple[str, dict[str, Any]] | None:
    """Return ``(user_id, frame)`` or ``None`` for a malformed payload.

    A bad message must be dropped, never turned into an event with empty
    fields; ``ForwardConsumer`` logs the decoding failure upstream.
    """
    if not isinstance(payload, dict):
        return None
    user_id = payload.get("user_id")
    frame = payload.get("frame")
    if not isinstance(user_id, str) or not user_id or not isinstance(frame, dict):
        return None
    return user_id, frame
