from __future__ import annotations

from typing import Literal

RequestMode = Literal["cold_start", "follow_up", "fast_path"]

__all__ = ["RequestMode"]
