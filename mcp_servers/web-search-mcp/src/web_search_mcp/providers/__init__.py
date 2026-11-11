"""Search provider adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ProviderOutcome:
    results: list[dict[str, Any]] = field(default_factory=list)
    status: str = "no_results"
    answer: str | None = None
