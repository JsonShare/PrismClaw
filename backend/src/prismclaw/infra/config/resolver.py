"""配置解析器 — 域配置（defaults + 工作区覆盖）的核心 API。

对外暴露：
  - get_effective_config()        获取域的生效配置（defaults + override 合并）
  - save_override()               保存工作区覆盖
  - reset_override()              重置全部覆盖（删除文件）
  - reset_override_key()          重置单个键路径

模型基建（服务商凭据 / 模型能力 / 全局默认模型）不在这里，见 models_registry.py，
其归属文件是 backend/configs/models.yaml。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from prismclaw.infra.config_io import dump_document, load_document

from .merge import (
    clean_empty,
    deep_merge,
    filter_stale,
    is_only_schema,
    validate_domain_name,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------

def _defaults_config_path(domain: str) -> Path:
    """返回域默认配置文件路径：backend/src/subpackages/{domain}/configs/{domain}.json。

    域配置随域分包自持——新增域只需在自身分包下放 configs/{domain}.json，
    核心引擎零改动。
    """
    validate_domain_name(domain)
    from prismclaw.infra.paths import resolve_project_root
    return resolve_project_root() / "backend" / "src" / "subpackages" / domain / "configs" / f"{domain}.json"


def _workspace_config_dir(
    workspace_id: str,
    user_id: str,
) -> Path:
    """返回工作区配置覆盖目录。

    工作区目录由 ``infra.workspace_paths`` 单一权威源解析（消除本地复刻）：
    {workspace_base}/web_{user_id}/{workspace_id}/.work/configs/
    """
    from prismclaw.infra.workspace_paths import resolve_workspace_dir

    return resolve_workspace_dir(user_id, workspace_id) / ".work" / "configs"


def _override_config_path(
    domain: str,
    workspace_id: str,
    user_id: str,
) -> Path:
    """返回工作区覆盖文件路径。"""
    validate_domain_name(domain)
    return _workspace_config_dir(workspace_id, user_id) / f"{domain}.json"


# ---------------------------------------------------------------------------
# 配置文档读写（YAML/JSON 自适应）
# ---------------------------------------------------------------------------

def _read_config_document(path: Path) -> dict[str, Any] | None:
    """读取配置文件（YAML/JSON 自适应）。解析失败返回 None + warning。"""
    if not path.is_file():
        return None
    data = load_document(path)
    if data is None:
        logger.warning("Config file %s failed to parse", path)
    return data


def _write_config_document(path: Path, data: dict[str, Any]) -> None:
    """原子写回配置文件：YAML 走 ruamel round-trip（保留注释），JSON 保持缩进。"""
    dump_document(path, data)


# ---------------------------------------------------------------------------
# 域配置
# ---------------------------------------------------------------------------

def get_effective_config(
    domain: str,
    workspace_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """获取域的生效配置。

    流程:
      1. 读取 backend/src/subpackages/{domain}/configs/{domain}.json (defaults)
      2. 读取 {workspace}/.work/configs/{domain}.json (override, 可选)
      3. 版本守卫 → deep_merge → 返回
    """
    validate_domain_name(domain)

    # 1. 读取 defaults
    defaults_path = _defaults_config_path(domain)
    defaults = _read_config_document(defaults_path)
    if defaults is None:
        logger.warning("Domain config defaults not found: %s", defaults_path)
        return {}

    # 2. 读取 override（可选）
    if not workspace_id or not user_id:
        return dict(defaults)

    override_path = _override_config_path(domain, workspace_id, user_id)
    override = _read_config_document(override_path)
    if override is None:
        return dict(defaults)

    # 3. 版本守卫
    default_ver = defaults.get("schema_version", "")
    override_ver = override.get("schema_version", "")

    if default_ver != override_ver:
        cleaned, stale_keys = filter_stale(override, defaults)
        if stale_keys:
            logger.warning(
                "Config version mismatch for domain=%s workspace=%s: "
                "default=%s override=%s, stale keys removed: %s",
                domain, workspace_id, default_ver, override_ver, stale_keys,
            )
        override = cleaned

    # 4. deep_merge
    result = deep_merge(defaults, override)
    result = clean_empty(result)
    return result


def save_override(
    domain: str,
    workspace_id: str,
    user_id: str,
    overrides: dict[str, Any],
) -> None:
    """保存工作区覆盖文件。

    自动附加当前 defaults 的 schema_version。
    写入 {workspace}/.work/configs/{domain}.json。
    """
    validate_domain_name(domain)

    # 读取 defaults 的 schema_version 并附加到 overrides
    defaults_path = _defaults_config_path(domain)
    defaults = _read_config_document(defaults_path)
    if defaults:
        overrides["schema_version"] = defaults.get("schema_version", "1.0")

    override_path = _override_config_path(domain, workspace_id, user_id)
    _write_config_document(override_path, overrides)


def reset_override(
    domain: str,
    workspace_id: str,
    user_id: str,
) -> None:
    """删除工作区覆盖文件（恢复全部默认）。"""
    validate_domain_name(domain)
    override_path = _override_config_path(domain, workspace_id, user_id)
    try:
        override_path.unlink(missing_ok=True)
    except Exception:
        logger.warning("Failed to delete override: %s", override_path, exc_info=True)


def reset_override_key(
    domain: str,
    workspace_id: str,
    user_id: str,
    key_path: str,
) -> None:
    """删除覆盖中的单个键路径。

    key_path 格式: "search.cnki" 或 "search.qianfan.api_key"
    删除后若覆盖文件只剩 schema_version，则删除整个文件。
    """
    validate_domain_name(domain)

    override_path = _override_config_path(domain, workspace_id, user_id)
    override = _read_config_document(override_path)
    if override is None:
        return  # 无覆盖文件，无需操作

    # 沿 key_path 定位到叶节点并删除
    keys = key_path.split(".")
    current: Any = override
    parent_stack: list[tuple[dict[str, Any], str]] = []

    for key in keys[:-1]:
        if not isinstance(current, dict) or key not in current:
            return  # 路径不存在，无需操作
        parent_stack.append((current, key))
        current = current[key]

    # 删除叶节点
    leaf_key = keys[-1]
    if isinstance(current, dict) and leaf_key in current:
        del current[leaf_key]

    # 从叶向上清理空字典
    for parent, key in reversed(parent_stack):
        if isinstance(parent[key], dict) and not parent[key]:
            del parent[key]
        else:
            break  # 非空 → 不再向上

    # 检查是否只剩 schema_version → 删除整个文件
    if is_only_schema(override) or not override:
        try:
            override_path.unlink(missing_ok=True)
        except Exception:
            logger.warning("Failed to delete override: %s", override_path, exc_info=True)
    else:
        _write_config_document(override_path, override)
