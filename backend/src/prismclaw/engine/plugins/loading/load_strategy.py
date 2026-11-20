"""插件加载策略接口。

直接构建 ``LoadedPlugin`` 的结构化字段。
v5 渐进式披露：技能加载时只解析 frontmatter（body=""），
content pipeline 参数暂存到 ``pipeline_context``，
body 按 ``SkillRuntime._ensure_body_loaded()`` 按需加载。
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from prismclaw.engine.plugins.loading._declarative_load import _load_declarative_plugin
from prismclaw.engine.plugins.loading.agent_loader import PluginAgentLoader
from prismclaw.engine.plugins.loading.load_result import LoadedPlugin, PluginError
from prismclaw.engine.plugins.paths import MANIFEST_REL_PATH
from prismclaw.engine.plugins.registry.identity import PluginID

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
        市场级 plugin.json 的 ``porting.remap`` 通用解析后传入；独立插件为 None。
        """


class DeclarativePluginLoadStrategy(PluginLoadStrategy):
    """加载声明式插件（plugin.json + markdown）。

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
        return (install_path / MANIFEST_REL_PATH).exists()

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


def default_strategies(
    *,
    agent_loader: PluginAgentLoader,
    data_base_dir: Path | None = None,
    skill_scanner: Any | None = None,
) -> list[PluginLoadStrategy]:
    return [
        NativePythonPluginLoadStrategy(),
        DeclarativePluginLoadStrategy(
            agent_loader=agent_loader,
            data_base_dir=data_base_dir,
            skill_scanner=skill_scanner,
        ),
    ]