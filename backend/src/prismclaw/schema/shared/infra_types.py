"""Infrastructure type contracts.

Protocols for metrics emission and settings access.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class MetricsSinkProtocol(Protocol):
    """指标上报协议；与 infra/metrics._MetricsRegistry 鸭子类型兼容。"""

    def emit(
        self,
        name: str,
        value: float,
        *,
        kind: str = "counter",
        labels: dict[str, str] | None = None,
    ) -> None:
        """Emit a metric data point."""
        ...

    def snapshot(self) -> dict[str, Any]:
        """Return a snapshot of all metric values."""
        ...

    def reset(self) -> None:
        """Reset all metric values."""
        ...


class SettingsReaderProtocol(Protocol):
    """Protocol for settings access — duck-typing compatible with infra/settings.Settings."""

    @property
    def workspace_base(self) -> Path:
        """Base path for workspace files."""
        ...

    @property
    def redis_url(self) -> str:
        """Redis connection URL."""
        ...

    @property
    def database_url(self) -> str:
        """Database connection URL."""
        ...
