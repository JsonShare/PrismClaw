"""插件激活器。

PrismClaw 特有设计（Python 需要 load/activate 分离）。

管理插件生命周期状态机：load → activate → deactivate → unload。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from prismclaw.engine.plugins.registry.identity import PluginID
from prismclaw.engine.plugins.loading.load_result import LoadedPlugin
from prismclaw.engine.plugins.lifecycle.state import (
    IllegalStateTransitionError,
    PluginLifecycleState,
    assert_transition,
    can_transition,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PluginActivationRecord:
    """插件激活记录。"""

    plugin_id: PluginID
    loaded_plugin: LoadedPlugin
    state: PluginLifecycleState = PluginLifecycleState.UNINSTALLED
    activated_capabilities: list[str] = field(default_factory=list)

    @property
    def is_active(self) -> bool:
        return self.state == PluginLifecycleState.ACTIVE


class PluginActivator:
    """插件激活器。管理 load/activate/deactivate/unload 生命周期。

    PrismClaw 特有设计：Python ``importlib`` 在 import 时执行模块级代码，
    需要 load（import 但不注册能力）和 activate（注册能力）分开。
    deactivate 时不需要重新 import。
    """

    def __init__(self) -> None:
        self._records: dict[str, PluginActivationRecord] = {}

    def install(self, plugin_id: PluginID, loaded_plugin: LoadedPlugin) -> None:
        """安装插件（记录到注册表，状态 → installed）。"""
        record = PluginActivationRecord(
            plugin_id=plugin_id,
            loaded_plugin=loaded_plugin,
            state=PluginLifecycleState.INSTALLED,
        )
        self._records[str(plugin_id)] = record

    def load(self, plugin_id: PluginID) -> None:
        """加载插件（状态 installed → loaded）。"""
        record = self._get_record(plugin_id)
        assert_transition(record.state, PluginLifecycleState.LOADED)
        record.state = PluginLifecycleState.LOADED

    def activate(
        self,
        plugin_id: PluginID,
        *,
        register_fn: "Callable[[LoadedPlugin], list[str]] | None" = None,
    ) -> list[str]:
        """激活插件（状态 loaded/inactive → active）。

        ``register_fn`` 注册能力并返回已注册能力名列表。
        """
        record = self._get_record(plugin_id)
        if not can_transition(record.state, PluginLifecycleState.ACTIVE):
            raise IllegalStateTransitionError(
                f"Cannot activate plugin {plugin_id} from state {record.state.value}"
            )
        if record.state == PluginLifecycleState.LOADED and register_fn is not None:
            record.activated_capabilities = register_fn(record.loaded_plugin)
        record.state = PluginLifecycleState.ACTIVE
        return list(record.activated_capabilities)

    def deactivate(
        self,
        plugin_id: PluginID,
        *,
        unregister_fn: "Callable[[LoadedPlugin, list[str]], None] | None" = None,
    ) -> None:
        """停用插件（状态 active → inactive）。"""
        record = self._get_record(plugin_id)
        assert_transition(record.state, PluginLifecycleState.INACTIVE)
        if unregister_fn is not None and record.activated_capabilities:
            unregister_fn(record.loaded_plugin, record.activated_capabilities)
        record.state = PluginLifecycleState.INACTIVE

    def unload(self, plugin_id: PluginID) -> None:
        """卸载插件（状态 active/inactive/loaded → installed）。

        不重新 import，仅清理注册的能力。
        """
        record = self._get_record(plugin_id)
        if record.state == PluginLifecycleState.ACTIVE:
            self.deactivate(plugin_id)
        assert_transition(record.state, PluginLifecycleState.INSTALLED)
        record.activated_capabilities.clear()
        record.state = PluginLifecycleState.INSTALLED

    def uninstall(self, plugin_id: PluginID) -> None:
        """卸载并移除插件（状态 → uninstalled）。"""
        record = self._get_record(plugin_id)
        if record.state in (PluginLifecycleState.ACTIVE, PluginLifecycleState.INACTIVE):
            self.unload(plugin_id)
        assert_transition(record.state, PluginLifecycleState.UNINSTALLED)
        record.state = PluginLifecycleState.UNINSTALLED
        self._records.pop(str(plugin_id), None)

    def get_state(self, plugin_id: PluginID) -> PluginLifecycleState | None:
        record = self._records.get(str(plugin_id))
        return record.state if record else None

    def is_active(self, plugin_id: PluginID) -> bool:
        record = self._records.get(str(plugin_id))
        return record is not None and record.is_active

    def list_active(self) -> list[PluginActivationRecord]:
        return [r for r in self._records.values() if r.is_active]

    def list_all(self) -> list[PluginActivationRecord]:
        return list(self._records.values())

    def _get_record(self, plugin_id: PluginID) -> PluginActivationRecord:
        record = self._records.get(str(plugin_id))
        if record is None:
            raise KeyError(f"Plugin {plugin_id} not installed")
        return record


# 用于类型注解的前向引用
from typing import Callable  # noqa: E402
