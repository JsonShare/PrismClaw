"""类型化的应用状态容器 — 替代裸 app.state 属性访问。

提供泛型 get_domain_store(name, type[T]) -> T，
让路由函数签名自文档化，消除裸字符串 + Any 类型。
"""
from __future__ import annotations

from typing import Any, TypeVar

from fastapi import Request

T = TypeVar("T")


class AppState:
    """类型化的应用状态容器，替代裸 app.state 属性访问。"""

    def __init__(self) -> None:
        self._domain_stores: dict[str, Any] = {}

    # -- domain stores --------------------------------------------------

    def register_domain_store(self, name: str, store: Any) -> None:
        self._domain_stores[name] = store

    def get_domain_store(self, name: str, store_type: type[T]) -> T:
        """按名称取域 store，运行时校验类型。

        Raises:
            ValueError: store 未注册
            TypeError: store 类型不匹配
        """
        store = self._domain_stores.get(name)
        if store is None:
            raise ValueError(f"Domain store '{name}' 未注册")
        if not isinstance(store, store_type):
            raise TypeError(
                f"Domain store '{name}' 类型不匹配："
                f"期望 {store_type.__name__}，实际 {type(store).__name__}"
            )
        return store

    def has_domain_store(self, name: str) -> bool:
        return name in self._domain_stores


def get_app_state(request: Request) -> AppState:
    """FastAPI 依赖：从 app.state 获取类型化的 AppState。

    Raises:
        RuntimeError: AppState 未在 lifespan 中初始化
    """
    state = getattr(request.app.state, "_app_state", None)
    if state is None:
        raise RuntimeError("AppState 未初始化，请检查 lifespan 配置")
    return state
