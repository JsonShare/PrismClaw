"""知识库路由 — 文献/指南/药品三页列表 + facet + 药品 options。

知识库为只读共享数据，仅加 Depends(get_current_user) 鉴权，不按 create_by 过滤。
"""
from __future__ import annotations


from fastapi import APIRouter, Depends, Request, Query

from prismclaw.app.auth.auth import get_current_user

from ..models.view_models import literature_to_vm, guideline_to_vm, drug_to_vm, drug_option_to_vm
from ..persistence.helpers import get_kb_store

router = APIRouter(tags=["medical-kb"])


@router.get("/literature")
async def list_literature(
    keyword: str = Query("", description="搜索关键词"),
    category: str = Query("", description="分类筛选"),
    year: int | None = Query(None, description="年份筛选"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(8, ge=1, le=100),
    http_request: Request = None,
    user_id: str = Depends(get_current_user),
) -> dict:
    """文献列表 + facets。"""
    store = get_kb_store(http_request)
    result = await store.list_literatures(
        keyword=keyword, category=category, year=year, page=page, page_size=pageSize,
    )
    result["items"] = [literature_to_vm(r) for r in result["items"]]
    return result


@router.get("/guidelines")
async def list_guidelines(
    keyword: str = Query("", description="搜索关键词"),
    category: str = Query("", description="分类筛选"),
    sort: str = Query("year", description="排序: year|name"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(8, ge=1, le=100),
    http_request: Request = None,
    user_id: str = Depends(get_current_user),
) -> dict:
    """指南列表 + facets。"""
    store = get_kb_store(http_request)
    result = await store.list_guidelines(
        keyword=keyword, category=category, sort=sort, page=page, page_size=pageSize,
    )
    result["items"] = [guideline_to_vm(r) for r in result["items"]]
    return result


@router.get("/drugs")
async def list_drugs(
    keyword: str = Query("", description="搜索关键词"),
    category: str = Query("", description="分类筛选"),
    essentialOnly: bool = Query(False, description="仅基药"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(8, ge=1, le=100),
    http_request: Request = None,
    user_id: str = Depends(get_current_user),
) -> dict:
    """药品列表 + facets。"""
    store = get_kb_store(http_request)
    result = await store.list_drugs(
        keyword=keyword, category=category, essential_only=essentialOnly,
        page=page, page_size=pageSize,
    )
    result["items"] = [drug_to_vm(r) for r in result["items"]]
    return result


@router.get("/drugs/options")
async def drug_options(
    http_request: Request,
    user_id: str = Depends(get_current_user),
) -> list[dict]:
    """药品轻量选项。"""
    store = get_kb_store(http_request)
    rows = await store.list_drug_options()
    return [drug_option_to_vm(r) for r in rows]
