"""医疗域 API 路由 — 统一导出所有 router。"""

from .patients import router as patients_router
from .records import router as records_router
from .kb import router as kb_router

__all__ = [
    "patients_router",
    "records_router",
    "kb_router",
]
