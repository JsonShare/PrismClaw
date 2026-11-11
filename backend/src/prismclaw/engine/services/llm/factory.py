"""LLMStreamFnFactory — 按租户创建或获取 LLMStreamFn 实例。"""
from __future__ import annotations

from prismclaw.schema.llm.types import LLMStreamFn

__all__ = ["LLMStreamFnFactory"]


class LLMStreamFnFactory:
    """按租户创建或获取 LLMStreamFn 实例。

    默认实例在构造时注入。特定租户可通过 register_tenant_fn 覆盖。
    """

    def __init__(self, default_fn: LLMStreamFn) -> None:
        self._default = default_fn
        self._tenant_overrides: dict[str, LLMStreamFn] = {}

    def get_for_tenant(self, tenant_id: str | None) -> LLMStreamFn:
        if tenant_id and tenant_id in self._tenant_overrides:
            return self._tenant_overrides[tenant_id]
        return self._default

    def register_tenant_fn(self, tenant_id: str, fn: LLMStreamFn) -> None:
        self._tenant_overrides[tenant_id] = fn
