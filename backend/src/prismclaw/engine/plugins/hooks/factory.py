"""Hook 配置工厂：把 hooks.json 声明转换为可执行 ``HookHandler``。

这是声明式插件 hook 从"配置"到"可执行"的唯一转换层。修复 P3-T1 审计发现的
断裂：此前 ``extension_manager`` 把 hooks.json 的原始 config dict 直接当 handler
注册，被下游 ``isinstance(HookHandler)`` 门控全部丢弃，``ShellCommandHookHandler``
等执行器从未被实例化——插件 hook 零触发。本模块在**加载期**（上下文最完整处）
把配置转换为 handler 实例，下游只做注册与分发。

对标参考实现 hooks.json 结构::

    {
      "hooks": {
        "PreToolUse": [
          {"matcher": "Bash", "hooks": [{"type": "command", "command": "...", "timeout": 30}]},
          {"hooks": [{"type": "http", "url": "https://..."}]}
        ]
      }
    }

归一化语义（:func:`normalize_hooks_config`）：
    * 顶层 ``{"hooks": {...}}`` 包装被解开为 ``{event_name: [group, ...]}``；
    * 兼容已解开的扁平事件映射（值为 list 即视为事件条目）；
    * 非法结构退化为空映射（不抛异常，加载不因 hook 配置笔误中断）。

handler 构造（:func:`build_hook_handlers`）：
    * 组（含 ``hooks`` 列表，可选 ``matcher``）与裸 handler dict 都接受；
    * ``type: command`` / 含 ``command`` → :class:`ShellCommandHookHandler`；
    * ``type: http`` / 含 ``url`` → :class:`HttpHookHandler`；
    * callable → :class:`CallbackHookHandler`；
    * 有 matcher 的组，其 handler 以 :class:`MatcherFilteredHandler` 包装。
"""
from __future__ import annotations

import logging
from typing import Any

from prismclaw.engine.plugins.hooks.handlers import (
    CallbackHookHandler,
    HookHandler,
    HttpHookHandler,
    MatcherFilteredHandler,
    ShellCommandHookHandler,
)

logger = logging.getLogger(__name__)

__all__ = [
    "normalize_hooks_config",
    "build_hook_handlers",
]


def normalize_hooks_config(raw: Any) -> dict[str, list[Any]]:
    """把 hooks.json 原始内容归一化为 ``{event_name: [group, ...]}``。

    解开参考实现的顶层 ``{"hooks": {...}}`` 包装；已是扁平事件映射（键值为 list）
    则原样保留。非 dict / 非 list 值等非法结构被丢弃，返回干净的事件映射。
    """
    if not isinstance(raw, dict):
        return {}
    # 解开 {"hooks": {...}} 包装（参考实现格式）。"hooks" 键存在即权威：值非 dict
    # 视为非法配置 → 空映射（不回退把 raw 当事件映射，否则会误建名为 "hooks" 的事件）。
    # 无 "hooks" 键时才把 raw 当作已解开的扁平事件映射。
    if "hooks" in raw:
        inner = raw["hooks"]
        event_map: Any = inner if isinstance(inner, dict) else {}
    else:
        event_map = raw
    result: dict[str, list[Any]] = {}
    for event_name, groups in event_map.items():
        if not isinstance(event_name, str) or not event_name:
            continue
        if not isinstance(groups, list):
            # 容忍单组直接作为值（{"PreToolUse": {...}}）→ 包成单元素列表。
            if isinstance(groups, dict):
                groups = [groups]
            else:
                continue
        result[event_name] = groups
    return result


def _iter_group_handlers(group: Any) -> tuple[str | None, list[Any]]:
    """从一个组条目提取 ``(matcher, handler_specs)``。

    组形如 ``{"matcher": "Bash", "hooks": [spec, ...]}``；也容忍裸 handler dict
    （无 ``hooks`` 键）视为 matcher=None 的单 handler 组。
    """
    if not isinstance(group, dict):
        return None, []
    nested = group.get("hooks")
    if isinstance(nested, list):
        matcher = group.get("matcher")
        return (matcher if isinstance(matcher, str) else None), nested
    # 裸 handler dict（含 command/url/type/callable 特征），无 matcher。
    return None, [group]


def _build_one_handler(
    spec: Any,
    *,
    plugin_root: str | None,
    plugin_data_dir: str | None,
    user_config: dict[str, Any],
) -> HookHandler | None:
    """把单个 handler spec 构造为 ``HookHandler``；无法识别返回 None。"""
    if callable(spec):
        return CallbackHookHandler(spec)
    if not isinstance(spec, dict):
        return None

    hook_type = spec.get("type")
    command = spec.get("command")
    url = spec.get("url")

    def _timeout(default: float) -> float:
        value = spec.get("timeout")
        try:
            return float(value) if value is not None else default
        except (TypeError, ValueError):
            return default

    if hook_type == "command" or (hook_type is None and isinstance(command, str) and command):
        if not isinstance(command, str) or not command:
            return None
        return ShellCommandHookHandler(
            command,
            timeout=_timeout(30.0),
            plugin_root=plugin_root,
            plugin_data_dir=plugin_data_dir,
            user_config=user_config,
        )
    if hook_type == "http" or (hook_type is None and isinstance(url, str) and url):
        if not isinstance(url, str) or not url:
            return None
        headers = spec.get("headers")
        return HttpHookHandler(
            url,
            headers=headers if isinstance(headers, dict) else None,
            timeout=_timeout(30.0),
        )
    return None


def build_hook_handlers(
    hooks_config: Any,
    *,
    plugin_root: str | None = None,
    plugin_data_dir: str | None = None,
    user_config: dict[str, Any] | None = None,
) -> dict[str, list[HookHandler]]:
    """把 hooks.json 配置转换为 ``{event_name: [HookHandler, ...]}``。

    加载期调用（此处插件 root / 数据目录 / 解析后的 user_config 均已知）。返回的
    handler 已是可执行实例，下游（extension_manager）直接注册，无需再判断类型。
    无法识别的 handler spec 被跳过并记 debug 日志；整体不抛异常。
    """
    resolved_user_config = user_config or {}
    event_map = normalize_hooks_config(hooks_config)
    result: dict[str, list[HookHandler]] = {}
    for event_name, groups in event_map.items():
        handlers: list[HookHandler] = []
        for group in groups:
            matcher, specs = _iter_group_handlers(group)
            for spec in specs:
                handler = _build_one_handler(
                    spec,
                    plugin_root=plugin_root,
                    plugin_data_dir=plugin_data_dir,
                    user_config=resolved_user_config,
                )
                if handler is None:
                    logger.debug(
                        "Skipping unrecognized hook handler spec for event %s: %r",
                        event_name,
                        spec,
                    )
                    continue
                if matcher:
                    handler = MatcherFilteredHandler(handler, matcher)
                handlers.append(handler)
        if handlers:
            result[event_name] = handlers
    return result
