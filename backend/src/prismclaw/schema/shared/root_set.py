from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RootSet:
    roots: tuple[Path, ...]

    @classmethod
    def from_primary_and_additional(
        cls,
        *,
        primary: str | Path | None,
        additional: list[str | Path] | None = None,
    ) -> "RootSet":
        out: list[Path] = []
        seen: set[Path] = set()
        for raw in [primary, *(additional or [])]:
            if raw is None:
                continue
            try:
                resolved = Path(raw).expanduser().resolve()
            except Exception:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            out.append(resolved)
        return cls(tuple(out))
