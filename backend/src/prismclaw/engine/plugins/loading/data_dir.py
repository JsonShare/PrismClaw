"""Plugin persistent data directories.

A ported plugin may reference ``${PRISMCLAW_PLUGIN_DATA}`` in its content to reach
a stable, per-plugin directory that survives reinstalls and plugin upgrades
(unlike the install tree, which is replaced on update). This module is the
single source of truth for *where* that directory lives and for turning a
plugin name into a filesystem-safe directory name.

Layout (default, global — consistent with the install store and memory base,
which also live under ``~/.prismclaw``)::

    ~/.prismclaw/plugins-data/<sanitized-plugin-name>/

The base directory is overridable so tests and alternative deployments can
redirect it without touching call sites.
"""
from __future__ import annotations

import re
from pathlib import Path

__all__ = [
    "DEFAULT_DATA_BASE_DIRNAME",
    "resolve_data_base_dir",
    "get_plugin_data_dir",
]

DEFAULT_DATA_BASE_DIRNAME = "plugins-data"

# A plugin name may contain characters that are unsafe or meaningful on a
# filesystem (most importantly ``/`` in ``marketplace/plugin`` ids). Replace any
# run of non ``[A-Za-z0-9._-]`` characters with a single underscore.
_UNSAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9._-]+")


def resolve_data_base_dir(data_base_dir: Path | str | None = None) -> Path:
    """Return the base directory under which per-plugin data dirs live.

    ``None`` resolves to the global default ``~/.prismclaw/plugins-data``.
    The directory is not created here; creation is demand-driven at load time.
    """
    if data_base_dir is not None:
        return Path(data_base_dir)
    return Path.home() / ".prismclaw" / DEFAULT_DATA_BASE_DIRNAME


def get_plugin_data_dir(
    data_base_dir: Path | str | None, plugin_name: str
) -> Path:
    """Return the persistent data directory for ``plugin_name``.

    The plugin name is sanitized into a single filesystem-safe segment (path
    separators collapsed to ``_``), so ``marketplace/plugin`` and ``plugin``
    never escape the base directory. The directory is not created here.
    """
    sanitized = _UNSAFE_CHARS_RE.sub("_", plugin_name).strip("._") or "plugin"
    return resolve_data_base_dir(data_base_dir) / sanitized
