"""插件安装状态持久化。

对标参考实现 ``installedPluginsManager.ts`` +
``schemas.ts`` L1453 ``InstalledPluginsFileSchemaV1``。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from prismclaw.engine.plugins.registry.identity import PluginID

logger = logging.getLogger(__name__)

_INSTALLED_FILE_VERSION = 1


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class InstalledPlugin(BaseModel):
    """单个插件的安装记录。对标参考实现 ``InstalledPluginsFileSchemaV1`` 条目。"""

    version: str | None = None
    installed_at: str = Field(default_factory=_utc_now_iso)
    last_updated: str | None = None
    install_path: str
    git_commit_sha: str | None = None


class InstalledPluginsFile(BaseModel):
    """安装状态文件。对标参考实现 ``InstalledPluginsFileSchemaV1``。"""

    version: int = _INSTALLED_FILE_VERSION
    plugins: dict[str, InstalledPlugin] = Field(default_factory=dict)


class InstalledPluginsStore:
    """``installed_plugins.json`` 读写。

    参考实现 ``installedPluginsManager.ts``。
    """

    def __init__(self, store_path: Path) -> None:
        self._store_path = store_path
        self._cache: InstalledPluginsFile | None = None

    @property
    def store_path(self) -> Path:
        return self._store_path

    def load(self) -> InstalledPluginsFile:
        if self._cache is not None:
            return self._cache
        if not self._store_path.exists():
            self._cache = InstalledPluginsFile()
            return self._cache
        try:
            raw = json.loads(self._store_path.read_text(encoding="utf-8"))
            self._cache = InstalledPluginsFile.model_validate(raw)
        except Exception as exc:
            logger.warning("Failed to load installed plugins from %s: %s", self._store_path, exc)
            self._cache = InstalledPluginsFile()
        return self._cache

    def save(self) -> None:
        if self._cache is None:
            return
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._store_path.with_suffix(".tmp")
        tmp.write_text(
            self._cache.model_dump_json(indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self._store_path)

    def get(self, plugin_id: PluginID) -> InstalledPlugin | None:
        return self.load().plugins.get(str(plugin_id))

    def upsert(
        self,
        plugin_id: PluginID,
        *,
        install_path: str,
        version: str | None = None,
        git_commit_sha: str | None = None,
    ) -> InstalledPlugin:
        data = self.load()
        existing = data.plugins.get(str(plugin_id))
        now = _utc_now_iso()
        record = InstalledPlugin(
            version=version,
            installed_at=existing.installed_at if existing else now,
            last_updated=now,
            install_path=install_path,
            git_commit_sha=git_commit_sha,
        )
        data.plugins[str(plugin_id)] = record
        self.save()
        return record

    def remove(self, plugin_id: PluginID) -> bool:
        data = self.load()
        key = str(plugin_id)
        if key not in data.plugins:
            return False
        del data.plugins[key]
        self.save()
        return True

    def list_installed(self) -> dict[str, InstalledPlugin]:
        return dict(self.load().plugins)
