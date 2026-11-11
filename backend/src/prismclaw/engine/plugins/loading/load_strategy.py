"""插件加载策略接口。

直接构建 ``LoadedPlugin`` 的结构化字段。
v5 渐进式披露：技能加载时只解析 frontmatter（body=""），
content pipeline 参数暂存到 ``pipeline_context``，
body 按 ``SkillRuntime._ensure_body_loaded()`` 按需加载。

对标参考实现 ``createPluginFromPath`` (L1340) 的分离。
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from prismclaw.engine.plugins.loading.agent_loader import PluginAgentLoader
from prismclaw.engine.plugins.registry.identity import PluginID
from prismclaw.engine.plugins.loading.load_result import LoadedPlugin, PluginError
from prismclaw.engine.plugins.manifest import (
    parse_manifest,
)

# 从拆分子模块重新导出，保持外部引用兼容
from prismclaw.engine.plugins.loading._manifest_parse import (  # noqa: F401
    _coerce_string_list,
    _load_json_file,
    _resolve_canonical_manifest,
    _parse_user_config_schema,
    _sensitive_user_config_keys,
    _resolve_user_config,
    _resolve_relative_paths,
    _dedupe_hook_groups,
    _validate_hook_command_paths,
    _extract_script_path_from_command,
    _merge_hook_config,
)
from prismclaw.engine.plugins.loading._declarative_load import (  # noqa: F401
    _load_declared_commands,
    _load_output_styles,
    _apply_content_pipeline,
    _load_mcp_servers,
    _substitute_mcp_server_configs,
    _load_plugin_instructions,
    _build_manifest_and_capability,
    _load_declarative_plugin,
)

logger = logging.getLogger(__name__)


class PluginLoadStrategy(ABC):
    """插件加载策略接口。"""

    @abstractmethod
    def can_handle(self, install_path: Path) -> bool:
        """是否能处理该安装路径。"""

    @abstractmethod
    async def load(
        self,
        plugin_id: PluginID,
        install_path: Path,
        *,
        enabled: bool = True,
        plugin_configs: dict[str, dict[str, Any]] | None = None,
        data_base_dir: Path | None = None,
        porting_context: dict[str, Any] | None = None,
    ) -> tuple[LoadedPlugin | None, list[PluginError]]:
        """加载插件，返回 (LoadedPlugin | None, errors)。

        v4：LoadedPlugin 直接持有 manifest_model + capability_graph 等结构化字段。
        ``data_base_dir`` 是插件持久数据目录的基目录（``${PRISMCLAW_PLUGIN_DATA}``），
        None 时取全局默认 ``~/.prismclaw/plugins-data``。
        ``porting_context`` 是 marketplace 级 porting 上下文（``{"remap_rules":
        list[PathRemapRule], "marketplace_name": str}``），由发现引擎从
        marketplace.json 的 ``porting.remap`` 通用解析后传入；独立插件为 None。
        """


class DeclarativePluginLoadStrategy(PluginLoadStrategy):
    """加载声明式插件（plugin.json + markdown）。

    对标参考实现 ``createPluginFromPath`` (L1340)。
    v4：直接构建 manifest_model + capability_graph，不通过 PluginBundleLoader。
    """

    def __init__(
        self,
        *,
        agent_loader: PluginAgentLoader,
        data_base_dir: Path | None = None,
        skill_scanner: Any | None = None,
    ) -> None:
        self._agent_loader = agent_loader
        self._data_base_dir = data_base_dir
        self._skill_scanner = skill_scanner

    def can_handle(self, install_path: Path) -> bool:
        if not install_path.is_dir():
            return False
        return (install_path / ".plugin" / "plugin.json").exists() or (
            install_path / "plugin.json"
        ).exists()

    async def load(
        self,
        plugin_id: PluginID,
        install_path: Path,
        *,
        enabled: bool = True,
        plugin_configs: dict[str, dict[str, Any]] | None = None,
        data_base_dir: Path | None = None,
        porting_context: dict[str, Any] | None = None,
    ) -> tuple[LoadedPlugin | None, list[PluginError]]:
        effective_data_base_dir = (
            data_base_dir if data_base_dir is not None else self._data_base_dir
        )
        return await _load_declarative_plugin(
            plugin_id,
            install_path,
            enabled=enabled,
            plugin_options=(plugin_configs or {}).get(plugin_id.name, {}),
            agent_loader=self._agent_loader,
            data_base_dir=effective_data_base_dir,
            porting_context=porting_context,
            dialect="declarative",
            skill_scanner=self._skill_scanner,
        )


class NativePythonPluginLoadStrategy(PluginLoadStrategy):
    """加载 Python 原生插件（main.py + plugin_sdk）。

    v4：直接产出 LoadedPlugin.native_module + native_tools + native_hooks，
    不再通过 dict payload。

    注意：load 必须留在事件循环线程执行（不得 to_thread offload）——
    它读写 ``sdk._CURRENT_TOOLS`` / ``sdk._CURRENT_HOOKS`` 全局状态，
    且函数体内无 await 点，事件循环上天然原子；offload 到线程会引入竞态。
    """

    def can_handle(self, install_path: Path) -> bool:
        return install_path.is_dir() and (install_path / "main.py").exists()

    async def load(
        self,
        plugin_id: PluginID,
        install_path: Path,
        *,
        enabled: bool = True,
        plugin_configs: dict[str, dict[str, Any]] | None = None,
        data_base_dir: Path | None = None,
        porting_context: dict[str, Any]
        | None = None,  # 接口对齐用；原生插件无散文正文，不参与 remap
    ) -> tuple[LoadedPlugin | None, list[PluginError]]:
        import prismclaw.engine.plugins.sdk as sdk

        errors: list[PluginError] = []
        main_py = install_path / "main.py"
        module_name = f"pluginclaw_plugin_{plugin_id.name}"

        # 清空 SDK 全局状态
        sdk._CURRENT_TOOLS.clear()
        sdk._CURRENT_HOOKS.clear()

        try:
            spec = importlib.util.spec_from_file_location(module_name, main_py)
            if spec is None or spec.loader is None:
                raise ImportError(f"Could not load spec for {main_py}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except Exception as exc:
            logger.warning("Failed to load native plugin %s: %s", plugin_id, exc)
            errors.append(
                PluginError(
                    type="generic_error",
                    source=plugin_id.source,
                    plugin=plugin_id.name,
                    message=str(exc),
                )
            )
            sdk._CURRENT_TOOLS.clear()
            sdk._CURRENT_HOOKS.clear()
            return None, errors

        native_tools = list(sdk._CURRENT_TOOLS)
        native_hooks = list(sdk._CURRENT_HOOKS)
        sdk._CURRENT_TOOLS.clear()
        sdk._CURRENT_HOOKS.clear()

        loaded = LoadedPlugin(
            name=plugin_id.name,
            source=plugin_id.source,
            path=str(install_path),
            enabled=enabled,
            manifest=None,
            native_module=module,
            native_tools=native_tools,
            native_hooks=native_hooks,
            root_path=install_path,
            install_path=str(install_path),
        )
        return loaded, errors


class TraePluginLoadStrategy(PluginLoadStrategy):
    """加载 ``.trae-plugin`` 方言插件（加载期内存适配，Route B）。

    Trae 方言把清单放在 ``.trae-plugin/plugin.json``，并多出一个仅供市场展示
    的 ``interface`` 块。本策略在加载期把它**内存转换**为 canonical 形态后交给
    与 ``DeclarativePluginLoadStrategy`` 共享的 ``_load_declarative_plugin``——
    不产生磁盘副本、不需任何手工步骤，插件直接从源目录零手改加载。转换失败
    （如缺 ``name``）经共享核心的异常路径产出 ``manifest_parse_error``。
    """

    def __init__(
        self,
        *,
        agent_loader: PluginAgentLoader,
        data_base_dir: Path | None = None,
        skill_scanner: Any | None = None,
    ) -> None:
        self._agent_loader = agent_loader
        self._data_base_dir = data_base_dir
        self._skill_scanner = skill_scanner

    def can_handle(self, install_path: Path) -> bool:
        if not install_path.is_dir():
            return False
        return (install_path / ".trae-plugin" / "plugin.json").exists()

    async def load(
        self,
        plugin_id: PluginID,
        install_path: Path,
        *,
        enabled: bool = True,
        plugin_configs: dict[str, dict[str, Any]] | None = None,
        data_base_dir: Path | None = None,
        porting_context: dict[str, Any] | None = None,
    ) -> tuple[LoadedPlugin | None, list[PluginError]]:
        from prismclaw.engine.plugins.ingest.trae_convert import (
            convert_trae_manifest,
        )

        effective_data_base_dir = (
            data_base_dir if data_base_dir is not None else self._data_base_dir
        )
        trae_manifest = _load_json_file(install_path / ".trae-plugin" / "plugin.json")
        if not isinstance(trae_manifest, dict):
            raise ValueError(".trae-plugin/plugin.json must be a JSON object")
        canonical_manifest = parse_manifest(convert_trae_manifest(trae_manifest))
        return await _load_declarative_plugin(
            plugin_id,
            install_path,
            enabled=enabled,
            plugin_options=(plugin_configs or {}).get(plugin_id.name, {}),
            agent_loader=self._agent_loader,
            data_base_dir=effective_data_base_dir,
            manifest_override=canonical_manifest,
            porting_context=porting_context,
            dialect="trae",
            skill_scanner=self._skill_scanner,
        )


def default_strategies(
    *,
    agent_loader: PluginAgentLoader,
    data_base_dir: Path | None = None,
    skill_scanner: Any | None = None,
) -> list[PluginLoadStrategy]:
    return [
        NativePythonPluginLoadStrategy(),
        DeclarativePluginLoadStrategy(agent_loader=agent_loader, data_base_dir=data_base_dir, skill_scanner=skill_scanner),
        TraePluginLoadStrategy(agent_loader=agent_loader, data_base_dir=data_base_dir, skill_scanner=skill_scanner),
    ]