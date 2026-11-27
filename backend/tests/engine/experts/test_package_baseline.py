"""Shipped expert packages must all validate against the current contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from prismclaw.engine.experts.manifest.expert_schema import validate_expert_package

BACKEND_ROOT = Path(__file__).resolve().parents[3]
EXPERTS_ROOT = BACKEND_ROOT / ".prismclaw" / "experts"


def test_all_shipped_packages_validate() -> None:
    assert EXPERTS_ROOT.is_dir(), f"expert packages missing at {EXPERTS_ROOT}"

    packages = sorted(path for path in EXPERTS_ROOT.iterdir() if path.is_dir())
    validated: list[str] = []
    for package in packages:
        raw: dict[str, Any] = json.loads(
            (package / ".plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        validate_expert_package(package, raw)
        validated.append(package.name)

    assert validated == [package.name for package in packages]
