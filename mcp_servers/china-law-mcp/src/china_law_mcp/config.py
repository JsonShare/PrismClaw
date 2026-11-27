"""Where the law record lives, and which jurisdictions to serve."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_PACKAGE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CLM_",
        env_file=(_PACKAGE_DIR / ".env", Path.cwd() / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    global_law_root: str = "~/.prismclaw/database/global-law"
    """The global-law clone: ``manifest.json`` at its root, ``china/jurisdiction.json`` inside."""

    acts_root: str = "~/.prismclaw/database/china-law"
    """The china-law clone, linked in as ``global-law/china/national/acts``.

    Empty means a submodule is expected at that path instead.
    """

    jurisdictions: str = "china"
    """Passed to the server as ``GLOBAL_LAW_JURISDICTIONS``; comma-separated, empty serves all."""

    @property
    def global_law_path(self) -> Path:
        return Path(self.global_law_root).expanduser().resolve()

    @property
    def acts_path(self) -> Path | None:
        return Path(self.acts_root).expanduser().resolve() if self.acts_root else None
