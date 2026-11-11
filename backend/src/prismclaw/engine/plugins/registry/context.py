"""插件上下文：draft transform API。

借鉴 opencode-dev ``plugin/host.ts`` L40 ``PluginHost``。
插件通过 transform 方法修改运行时注册表，而非直接操作 ExtensionManager 内部状态。
"""
from __future__ import annotations

from typing import Any, Callable, Protocol


class AgentDraft(Protocol):
    """Agent 注册表 draft。对标 opencode-dev ``agent.transform`` draft。"""

    def list(self) -> list[Any]: ...
    def get(self, agent_id: str) -> Any | None: ...
    def update(self, agent_id: str, update: Any) -> None: ...
    def remove(self, agent_id: str) -> None: ...


class SkillDraft(Protocol):
    """Skill 注册表 draft。"""

    def list(self) -> list[Any]: ...
    def get(self, skill_id: str) -> Any | None: ...
    def update(self, skill_id: str, update: Any) -> None: ...
    def remove(self, skill_id: str) -> None: ...


class CommandDraft(Protocol):
    """Command 注册表 draft。"""

    def list(self) -> list[Any]: ...
    def get(self, command_name: str) -> Any | None: ...
    def update(self, command_name: str, update: Any) -> None: ...
    def remove(self, command_name: str) -> None: ...


class PluginContext:
    """插件上下文。借鉴 opencode-dev ``PluginHost`` (host.ts L40)。

    插件通过 transform 方法修改运行时注册表。
    每个 transform 回调在一个 draft 上下文中执行，支持原子修改。
    """

    def __init__(
        self,
        *,
        agent_registry: Any | None = None,
        skill_registry: Any | None = None,
        command_registry: Any | None = None,
        hook_dispatcher: Any | None = None,
        mcp_manager: Any | None = None,
    ) -> None:
        self._agent_registry = agent_registry
        self._skill_registry = skill_registry
        self._command_registry = command_registry
        self._hook_dispatcher = hook_dispatcher
        self._mcp_manager = mcp_manager

    def agent_transform(self, callback: Callable[[AgentDraft], None]) -> None:
        """修改 agent 注册表。对标 opencode-dev ``agent.transform``。"""
        if self._agent_registry is None:
            return
        draft = _RegistryDraft(self._agent_registry)
        callback(draft)

    def skill_transform(self, callback: Callable[[SkillDraft], None]) -> None:
        """修改 skill 注册表。"""
        if self._skill_registry is None:
            return
        draft = _RegistryDraft(self._skill_registry)
        callback(draft)

    def command_transform(self, callback: Callable[[CommandDraft], None]) -> None:
        """修改 command 注册表。"""
        if self._command_registry is None:
            return
        draft = _RegistryDraft(self._command_registry)
        callback(draft)

    def hook_register(self, event: str, handler: Any) -> None:
        """注册 hook。"""
        if self._hook_dispatcher is None:
            return
        register_named = getattr(self._hook_dispatcher, "register_named", None)
        if callable(register_named):
            register_named(event, handler)

    def hook_unregister(self, event: str, handler: Any) -> None:
        """注销 hook。"""
        if self._hook_dispatcher is None:
            return
        unregister = getattr(self._hook_dispatcher, "unregister", None)
        if callable(unregister):
            unregister(event, handler)


class _RegistryDraft:
    """通用注册表 draft 适配器。"""

    def __init__(self, registry: Any) -> None:
        self._registry = registry

    def list(self) -> list[Any]:
        get_all = getattr(self._registry, "get_all", None)
        if callable(get_all):
            result = get_all()
            if isinstance(result, dict):
                return list(result.values())
            return list(result)
        return []

    def get(self, item_id: str) -> Any | None:
        getters = ["get_plugin", "get_skill", "get_command", "get_agent", "get"]
        for attr in getters:
            getter = getattr(self._registry, attr, None)
            if callable(getter):
                result = getter(item_id)
                if result is not None:
                    return result
        return None

    def update(self, item_id: str, update: Any) -> None:
        register = getattr(self._registry, "register", None)
        if callable(register):
            register(item_id, update)

    def remove(self, item_id: str) -> None:
        unregister = getattr(self._registry, "unregister", None)
        if callable(unregister):
            unregister(item_id)
