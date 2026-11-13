from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from prismclaw.infra.config_io import dump_document, load_document

from ._settings import Settings

# 锚点文件（yaml）。
CONFIG_SEARCH_PATHS = [
    "server.yaml",
    "backend/configs/server.yaml",
    "backend/.prismclaw/server.yaml",
    "~/.prismclaw/server.yaml",
]


def _repo_root_from_settings_module() -> Path | None:
    try:
        # 三目录重构后源码嵌套在 backend/src/prismclaw/...，需多上溯一层
        return Path(__file__).resolve().parents[5]
    except Exception:
        return None


def _repo_bundled_skill_candidates(repo_root: Path) -> list[Path]:
    return []


# 模块化配置：锚点文件（server.yaml）同目录下按此顺序加载并深度合并（后者覆盖前者）。
# 每个模块文件以顶层键为根，例如 infra.yaml 直接写 redis/storage 两段。
_MODULE_CONFIG_FILES = (
    "infra.yaml",
    "models.yaml",
    "agent.yaml",
    "mcp.yaml",
    "capabilities.yaml",
    "memory.yaml",
    "security.yaml",
)

# 顶层键 → 归属文件。同一个顶层键只允许一个归属文件，
# 否则 config_file_owner() 无法确定运行时写回落点（会造成配置漂移）。
CONFIG_KEY_OWNERS = {
    "server": "server.yaml",
    "affinity": "server.yaml",
    "runtime": "server.yaml",
    "workspaces": "server.yaml",
    "channels": "server.yaml",
    "redis": "infra.yaml",
    "storage": "infra.yaml",
    "sandbox": "security.yaml",
    "models": "models.yaml",
    "token_counting": "models.yaml",
    "embedding": "models.yaml",
    "vision": "models.yaml",
    "speech": "models.yaml",
    "agent": "agent.yaml",
    "skills": "capabilities.yaml",
    "subagent": "capabilities.yaml",
    "plugins": "capabilities.yaml",
    "builtin_tools": "capabilities.yaml",
    "memory": "memory.yaml",
    "evolution": "memory.yaml",
    "mcp": "mcp.yaml",
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归深度合并 override 到 base：dict 逐层合并，list/标量整体替换。"""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_module_config(name: str, path: Path) -> dict[str, Any] | None:
    """读取单个模块配置文件并做格式规整；解析失败返回 None（跳过不致命）。"""
    logger = logging.getLogger("prismclaw.settings")
    data = load_document(path)
    if data is None:
        logger.warning("module config %s failed to parse; skipping", path)
        return None
    # mcp 文件兼容 Claude Desktop 风格 {"mcpServers": {...}}：
    # 归一化为 {"mcp": {"servers": {...}}}（McpSettings 为 extra="forbid"）。
    if Path(name).stem == "mcp":
        mcp_servers = data.get("mcpServers")
        if mcp_servers is not None:
            mcp = dict(data.get("mcp")) if isinstance(data.get("mcp"), dict) else {}
            if isinstance(mcp_servers, dict):
                servers = dict(mcp.get("servers") or {})
                servers.update(mcp_servers)
                mcp["servers"] = servers
            data = {"mcp": mcp}
    return data


def config_file_owner(top_key: str) -> Path:
    """定位负责某个顶层键的配置文件（运行时写回落点）。

    优先使用显式归属表 ``CONFIG_KEY_OWNERS``（一个顶层键只对应一个文件）；
    归属表未登记时按合并顺序倒序扫描，命中持有该键的模块文件；都找不到时
    回退锚点文件，锚点也不存在时回退 ``~/.prismclaw/server.yaml``。
    """
    config_file = find_config_file()
    module_dir = config_file.parent if config_file is not None else None

    target = CONFIG_KEY_OWNERS.get(top_key)
    if module_dir is not None and target:
        p = module_dir / target
        if p.is_file():
            return p

    if module_dir is not None:
        for name in reversed(_MODULE_CONFIG_FILES):
            p = module_dir / name
            if not p.is_file():
                continue
            data = load_document(p)
            if isinstance(data, dict) and top_key in data:
                return p

    if config_file is not None:
        return config_file
    fallback = Path("~/.prismclaw/server.yaml").expanduser()
    fallback.parent.mkdir(parents=True, exist_ok=True)
    return fallback


def find_config_file() -> Path | None:
    override = (os.getenv("PRISMCLAW_CONFIG_FILE") or os.getenv("PRISMCLAW_CONFIG_PATH") or "").strip()
    if override:
        p = Path(override).expanduser()
        if p.is_file():
            try:
                return p.resolve()
            except Exception:
                return p
        logging.getLogger("prismclaw.settings").warning(
            "config override %r not found; falling back to search paths", override
        )

    repo_root = _repo_root_from_settings_module()
    for candidate in CONFIG_SEARCH_PATHS:
        path = Path(candidate).expanduser()
        if path.is_file():
            try:
                return path.resolve()
            except Exception:
                return path
        if repo_root is not None and not path.is_absolute() and "~" not in candidate:
            alt = (repo_root / candidate).expanduser()
            if alt.is_file():
                try:
                    return alt.resolve()
                except Exception:
                    return alt
    return None


_cached_settings: Settings | None = None


def load_settings(force_reload: bool = False) -> Settings:
    global _cached_settings
    if _cached_settings is not None and not force_reload:
        return _cached_settings

    config_file = find_config_file()
    if config_file is None:
        logger = logging.getLogger("prismclaw.settings")
        repo_root = _repo_root_from_settings_module()
        logger.warning(
            "no config file found. searched=%s cwd=%s repo_root=%s. using defaults",
            CONFIG_SEARCH_PATHS,
            str(Path.cwd()),
            str(repo_root) if repo_root is not None else None,
        )
        _cached_settings = Settings()
        return _cached_settings

    data = load_document(config_file)
    logger = logging.getLogger("prismclaw.settings")
    if data is None:
        logger.warning("config file %s failed to parse; using defaults", str(config_file))
        _cached_settings = Settings()
        return _cached_settings
    module_dir = config_file.parent
    for module_name in _MODULE_CONFIG_FILES:
        module_path = module_dir / module_name
        if not module_path.is_file():
            continue
        module_data = _load_module_config(module_name, module_path)
        if module_data is not None:
            data = _deep_merge(data, module_data)
            logger.info("merged module config: %s", str(module_path))
    logger.info("loaded config file: %s", str(config_file))
    try:
        settings = Settings.model_validate(data)
    except Exception:
        if _cached_settings is not None:
            # 配置校验失败时保留上一份有效配置并告警，不崩回默认——运行时写回
            # （models 管理 / plugins trust）触发 force_reload 会经过这里，
            # 一次坏写回不应击穿整个 settings。
            logger.exception(
                "config validation failed for %s; serving previous valid settings",
                str(config_file),
            )
            return _cached_settings
        raise
    _cached_settings = settings
    return _cached_settings


def persist_plugin_trust(trusted_plugin_ids: list[str]) -> bool:
    """把受信任插件 ID 列表写入配置文件（``plugins.trusted_plugins``）并重载缓存。

    P3-T3：``/plugins trust <id>`` 的持久化落点。键名与
    ``PluginSettings.trusted_plugins`` 一致。写入位置由
    ``config_file_owner("plugins")`` 决定（当前为 capabilities.yaml）。
    YAML 写回走 ruamel round-trip，文件内注释与键顺序会被保留。
    返回是否成功持久化；失败仅记日志、不抛（调用方已先行内存生效）。
    """
    logger = logging.getLogger("prismclaw.settings")
    config_file = config_file_owner("plugins")
    try:
        data = load_document(config_file) or {}
        plugins_obj = data.get("plugins")
        if not isinstance(plugins_obj, dict):
            plugins_obj = {}
        plugins_obj["trusted_plugins"] = list(trusted_plugin_ids)
        data["plugins"] = plugins_obj
        dump_document(config_file, data)
    except Exception:
        logger.warning("persist_plugin_trust: failed to write %s", config_file, exc_info=True)
        return False
    load_settings(force_reload=True)
    return True
