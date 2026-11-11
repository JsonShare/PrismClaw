"""Event bus protocol — for publishing/subscribing to domain events."""
from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class BusProtocol(Protocol):
    """Protocol for the event bus used for domain event propagation."""
    def publish(self, event: Any) -> None: ...
    def subscribe(self, event_type: type, handler: Callable) -> None: ...
