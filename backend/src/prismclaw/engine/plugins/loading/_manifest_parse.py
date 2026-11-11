"""Manifest 解析与校验的纯函数。

从 load_strategy.py 拆分而来，包含清单解析、用户配置解析、
hooks 校验与合并等无副作用的工具函数。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from prismclaw.engine.plugins.manifest import (
    PluginManifest,
    UserConfigOption,
    parse_manifest,
)

logger = logging.getLogger(__name__)


def _coerce_string_list(raw: Any) -> list[str] | None:
    """通用列表归一:非 list/None 返回 None;仅保留非空字符串 strip 后的元素。

    与 skills/parser._coerce_string_list 行为完全对齐,放在此处避免跨模块引
    （parser 引会拉起技能解析大链）。
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        return None
    values = [item.strip() for item in raw if isinstance(item, str) and item.strip()]
    return values or None


def _load_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _resolve_canonical_manifest(install_path: Path) -> PluginManifest:
    """定位并加载 canonical 方言的 plugin.json，立即解析为 pydantic 模型。

    canonical 方言把清单放在 ``.plugin/plugin.json``，兼容根目录
    ``plugin.json``。返回的 ``PluginManifest`` 已经过 schema 校验。
    方言策略（如 ``.trae-plugin``）不经过此函数，而是先把自身清单转换为
    canonical 形态，再经 ``manifest_override`` 注入 ``_build_manifest_and_capability``。
    """
    plugin_json_path = install_path / ".plugin" / "plugin.json"
    if not plugin_json_path.exists():
        plugin_json_path = install_path / "plugin.json"
    raw = _load_json_file(plugin_json_path)
    if not isinstance(raw, dict):
        raise ValueError("plugin.json must be a JSON object")
    return parse_manifest(raw)


def _parse_user_config_schema(manifest: PluginManifest) -> dict[str, UserConfigOption]:
    """提取 user_config schema 的属性声明（有序）。

    user_config 在 PluginManifest 中已被 pydantic 解析为 ``dict[str, UserConfigOption]``。
    本函数仅做 None 安全处理和类型收敛。
    """
    user_config_schema = manifest.user_config
    if not isinstance(user_config_schema, dict):
        return {}
    return {key: spec for key, spec in user_config_schema.items() if isinstance(key, str)}


def _sensitive_user_config_keys(manifest: PluginManifest) -> frozenset[str]:
    """返回 schema 中标记为 sensitive 的 user_config key 集合。

    对标参考实现 ``pluginOptionsStorage`` 的 sensitive 分支：敏感值不得进入
    模型上下文或任何被缓存/渲染的产物，正文替换时以占位符代之。
    """
    return frozenset(
        key for key, spec in _parse_user_config_schema(manifest).items() if spec.sensitive is True
    )


def _resolve_user_config(
    manifest: PluginManifest, plugin_options: dict[str, Any]
) -> dict[str, Any]:
    """解析 user_config，应用默认值。对标参考实现 ``resolveUserConfig``。"""
    properties = _parse_user_config_schema(manifest)
    resolved: dict[str, Any] = {}
    # 先填充 schema 中声明的 key（保留 schema 顺序）
    for key, spec in properties.items():
        if key in plugin_options:
            resolved[key] = plugin_options[key]
        elif spec.default is not None:
            resolved[key] = spec.default
    # 再补充 plugin_options 中存在但 schema 未声明的 key（用户自定义）
    for key, value in plugin_options.items():
        if isinstance(key, str) and key not in resolved:
            resolved[key] = value
    return resolved


def _resolve_relative_paths(base: Path, raw: Any) -> list[Path]:
    """解析 manifest 中相对路径字段。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [base / raw]
    if isinstance(raw, list):
        return [base / item for item in raw if isinstance(item, str)]
    return []


def _dedupe_hook_groups(groups: list[Any]) -> list[Any]:
    """对 hook groups 列表去重。

    先按 matcher 合并同组，再以 (hook_type, command_or_url) 为指纹去重，
    避免 marketplace 级与子插件级 hooks 命中同一事件时重复执行同一安全脚本。
    """
    # Step 1: Merge groups with the same matcher into a single group
    merged_by_matcher: dict[str, dict[str, Any]] = {}
    matcher_order: list[str] = []
    for group in groups:
        if not isinstance(group, dict):
            # Non-dict groups pass through unchanged
            key = f"__raw_{id(group)}"
            merged_by_matcher[key] = group
            matcher_order.append(key)
            continue
        matcher = group.get("matcher", "") or ""
        if matcher not in merged_by_matcher:
            matcher_order.append(matcher)
            merged_by_matcher[matcher] = {**group, "hooks": list(group.get("hooks", []) or [])}
        else:
            existing = merged_by_matcher[matcher]
            existing["hooks"] = [*existing.get("hooks", []), *(group.get("hooks", []) or [])]

    # Step 2: Deduplicate hooks within each merged group
    result: list[Any] = []
    for matcher in matcher_order:
        group = merged_by_matcher[matcher]
        if not isinstance(group, dict):
            result.append(group)
            continue
        nested = group.get("hooks", [])
        if not isinstance(nested, list):
            result.append(group)
            continue
        seen: set[tuple[str, str]] = set()
        deduped_nested: list[Any] = []
        for hook in nested:
            if not isinstance(hook, dict):
                deduped_nested.append(hook)
                continue
            hook_type = str(hook.get("type", "") or "")
            cmd = str(hook.get("command", "") or "")
            url = str(hook.get("url", "") or "")
            fingerprint = (hook_type, cmd or url)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            deduped_nested.append(hook)
        if deduped_nested:
            result.append({**group, "hooks": deduped_nested})
    return result


def _validate_hook_command_paths(hooks_config: dict[str, Any], install_path: Path) -> None:
    """加载期校验 hooks.json 中引用的命令路径是否存在。

    遍历所有 command 类型的 hook，提取 python/shell 脚本路径，
    检查文件是否存在。缺失时记录 WARNING 日志，让开发者能在加载期就发现
    安全脚本缺失问题，而非等到运行时才报错。
    """
    from prismclaw.engine.plugins.manifest.substitution import (
        build_plugin_variables,
        substitute_variables,
    )

    variables = build_plugin_variables(plugin_root=install_path)
    for event_name, groups in hooks_config.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            nested = group.get("hooks", [])
            if not isinstance(nested, list):
                continue
            for hook in nested:
                if not isinstance(hook, dict):
                    continue
                command = hook.get("command")
                if not isinstance(command, str) or not command:
                    continue
                substituted = substitute_variables(command, variables)
                script_path = _extract_script_path_from_command(substituted)
                if script_path is not None:
                    full_path = Path(script_path)
                    if not full_path.is_file():
                        logger.warning(
                            "Hook references missing script: %s "
                            "(resolved from %s, event=%s)",
                            full_path, command, event_name,
                        )


def _extract_script_path_from_command(command: str) -> str | None:
    """从 shell command 中提取脚本路径。

    支持以下模式：
    - python /path/to/script.py --args
    - python3 /path/to/script.py
    - bash /path/to/script.sh
    - /path/to/script.sh
    """
    import re
    patterns = [
        r"(?:python(?:3)?|bash|sh|zsh)\s+(\S+\.(?:py|sh|bash|zsh))",
        r"^(\S+\.(?:py|sh|bash|zsh))",
    ]
    for pattern in patterns:
        match = re.search(pattern, command)
        if match:
            return match.group(1)
    return None


def _merge_hook_config(base: dict[str, Any], addition: dict[str, Any]) -> dict[str, Any]:
    """合并 hooks.json 配置，并对每个事件组内的 hook 条目去重。"""
    result = dict(base)
    for key, value in addition.items():
        if key in result and isinstance(result[key], list) and isinstance(value, list):
            merged = [*result[key], *value]
            result[key] = _dedupe_hook_groups(merged)
        else:
            result[key] = value
    return result
