"""Load plugin operating profiles from the filesystem.

Moved from contracts.services.skills to break the contracts→engine
dependency cycle. This is an I/O function (reads the filesystem) and
belongs in the engine layer.
"""
from __future__ import annotations

from pathlib import Path as _Path


def _join_plugins_dir(root: _Path, plugins_dir: str) -> _Path:
    """拼接 plugins 根路径。

    plugins_dir 可能是相对（拼到 project_root 上）
    或绝对（``~/.prismclaw/assets/plugins``，直接用 expanduser 结果）。统一 expanduser
    再判断 is_absolute。
    """
    expanded = _Path(plugins_dir).expanduser()
    if expanded.is_absolute():
        return expanded.resolve() if expanded.exists() else expanded
    return (root / expanded).resolve()


def _load_plugin_profile(
    plugin_name: str | None,
    *,
    project_root_hint: _Path | None = None,
) -> str | None:
    """读插件下的 PRISMCLAW.md（回退 README.md）。

    启动时 sync_prismclaw_home 已把 backend/.prismclaw 同步到
    ``~/.prismclaw/assets``，默认插件根 ``~/.prismclaw/assets/plugins/<plugin>/PRISMCLAW.md``
    直接命中；project_root 只作为部署期兜底。
    """
    if not plugin_name:
        return None

    # 1) 拿 plugins_dir 配置
    plugins_dir = "~/.prismclaw/assets/plugins"
    try:
        from prismclaw.infra.settings._plugins import PluginSettings

        _configured = PluginSettings().asset_plugins_dir
        if isinstance(_configured, str) and _configured.strip():
            plugins_dir = _configured.strip()
    except Exception:
        pass

    # 2) 候选根列表
    roots: list[_Path] = []
    if project_root_hint is not None:
        roots.append(_Path(project_root_hint).expanduser())
    try:
        from prismclaw.infra.paths import resolve_project_root

        resolved = resolve_project_root(hint=None)
        if resolved is not None:
            resolved_p = _Path(resolved).expanduser()
            roots.append(resolved_p / "backend")
            roots.append(resolved_p)
    except Exception:
        pass

    seen: set[_Path] = set()
    for root in roots:
        try:
            root_resolved = root.resolve() if root.is_dir() else root
        except Exception:
            root_resolved = root
        if root_resolved in seen:
            continue
        seen.add(root_resolved)

        plugins_root = _join_plugins_dir(root_resolved, plugins_dir)
        plugin_root = plugins_root / plugin_name

        for fname in ("PRISMCLAW.md", "prismclaw.md", "README.md"):
            candidate = plugin_root / fname
            if not candidate.is_file():
                continue
            try:
                text = candidate.read_text(encoding="utf-8").strip()
            except Exception:
                continue
            if text:
                return text
    return None
