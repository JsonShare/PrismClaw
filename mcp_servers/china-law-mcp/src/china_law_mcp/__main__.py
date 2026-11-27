"""stdio entry: ``python -m china_law_mcp`` hands this process to the vendored official server."""

from __future__ import annotations

from .config import Settings
from .official import launch


def main() -> None:
    settings = Settings()
    launch(settings.global_law_path, settings.jurisdictions, settings.acts_path)


if __name__ == "__main__":
    main()
