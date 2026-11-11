"""医疗域持久化辅助 — 统一 _row_to_dict + Store 访问器。

消除 medical_store.py / kb_store.py 中重复的 _row_to_dict，
消除 routes/ 三文件中重复的 _get_medical_store / _get_kb_store。

通过 AppState 泛型取 store，路由函数签名即文档。
"""
from __future__ import annotations

from typing import Any

from fastapi import Request

from prismclaw.infra.app_state import get_app_state


def row_to_dict(row: Any) -> dict[str, Any]:
    """将 SQLAlchemy Row 转为 dict。"""
    return dict(row._mapping)  # type: ignore[union-attr]


def get_medical_store(request: Request):
    """从 Request 获取 MedicalStore（类型安全）。

    支持两种调用方式：
    1. 位置参数：get_medical_store(http_request)  — 路由函数手动传入
    2. FastAPI Depends：store = Depends(get_medical_store) — 自动注入
    """
    from .medical_store import MedicalStore
    state = get_app_state(request)
    return state.get_domain_store("medical", MedicalStore)


def get_kb_store(request: Request):
    """从 Request 获取 KbStore（类型安全）。"""
    from .kb_store import KbStore
    state = get_app_state(request)
    return state.get_domain_store("medical_kb", KbStore)


def get_brief_store(request: Request):
    """从 Request 获取 BriefStore（类型安全）。"""
    from .brief_store import BriefStore
    state = get_app_state(request)
    return state.get_domain_store("medical_briefs", BriefStore)
