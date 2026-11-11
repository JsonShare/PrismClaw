"""医疗域知识库存储 — KB 三表 CRUD + facet 计数。

表集：
- pc_medical_kb_literatures   文献
- pc_medical_kb_guidelines    指南
- pc_medical_kb_drugs         药品

与 MedicalStore 独立，通过 bundle 注册为 'medical_kb' Store。
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import (
    select,
    insert,
    update,
    func,
)
from sqlalchemy.ext.asyncio import AsyncEngine

from ..models.tables import (
    kb_literatures,
    kb_guidelines,
    kb_drugs,
)
from .helpers import row_to_dict

logger = logging.getLogger(__name__)


class KbStore:
    """知识库三表存储 — 列表 + facet + 轻量 options。"""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    # ── 文献 ────────────────────────────────────────────────────

    async def list_literatures(
        self,
        *,
        keyword: str = "",
        category: str = "",
        year: int | None = None,
        page: int = 1,
        page_size: int = 8,
    ) -> dict[str, Any]:
        """文献列表 + facet 计数。"""
        async with self._engine.connect() as conn:
            base = kb_literatures.select().where(kb_literatures.c.is_deleted == 0)
            stmt = base
            if keyword:
                stmt = stmt.where(kb_literatures.c.title.ilike(f"%{keyword}%"))
            if category and category != "全部":
                stmt = stmt.where(kb_literatures.c.category == category)
            if year is not None:
                stmt = stmt.where(kb_literatures.c.year == year)

            total = (await conn.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
            rows = (await conn.execute(
                stmt.order_by(kb_literatures.c.year.desc()).offset((page - 1) * page_size).limit(page_size)
            )).fetchall()
            items = [row_to_dict(r) for r in rows]

            # facet: categories
            cat_rows = (await conn.execute(
                select(kb_literatures.c.category, func.count()).where(kb_literatures.c.is_deleted == 0)
                .group_by(kb_literatures.c.category)
            )).fetchall()
            total_count = sum(c for _, c in cat_rows)
            categories = [{"name": "全部", "count": total_count}] + [
                {"name": cat, "count": cnt} for cat, cnt in cat_rows if cat
            ]

            # facet: years
            yr_rows = (await conn.execute(
                select(kb_literatures.c.year, func.count()).where(kb_literatures.c.is_deleted == 0)
                .group_by(kb_literatures.c.year)
            )).fetchall()
            yr_total = sum(c for _, c in yr_rows)
            years = [{"name": "全部", "count": yr_total}] + [
                {"name": str(yr), "count": cnt} for yr, cnt in yr_rows if yr
            ]

        return {
            "items": items, "total": total, "page": page, "pageSize": page_size,
            "facets": {"categories": categories, "years": years},
        }

    # ── 指南 ────────────────────────────────────────────────────

    async def list_guidelines(
        self,
        *,
        keyword: str = "",
        category: str = "",
        sort: str = "year",
        page: int = 1,
        page_size: int = 8,
    ) -> dict[str, Any]:
        """指南列表 + facet。"""
        async with self._engine.connect() as conn:
            stmt = kb_guidelines.select().where(kb_guidelines.c.is_deleted == 0)
            if keyword:
                stmt = stmt.where(kb_guidelines.c.title.ilike(f"%{keyword}%"))
            if category and category != "全部":
                stmt = stmt.where(kb_guidelines.c.category == category)

            total = (await conn.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
            order_col = kb_guidelines.c.year.desc() if sort == "year" else kb_guidelines.c.title
            rows = (await conn.execute(
                stmt.order_by(order_col).offset((page - 1) * page_size).limit(page_size)
            )).fetchall()
            items = [row_to_dict(r) for r in rows]

            cat_rows = (await conn.execute(
                select(kb_guidelines.c.category, func.count()).where(kb_guidelines.c.is_deleted == 0)
                .group_by(kb_guidelines.c.category)
            )).fetchall()
            total_count = sum(c for _, c in cat_rows)
            categories = [{"name": "全部", "count": total_count}] + [
                {"name": cat, "count": cnt} for cat, cnt in cat_rows if cat
            ]

        return {
            "items": items, "total": total, "page": page, "pageSize": page_size,
            "facets": {"categories": categories},
        }

    # ── 药品 ────────────────────────────────────────────────────

    async def list_drugs(
        self,
        *,
        keyword: str = "",
        category: str = "",
        essential_only: bool = False,
        page: int = 1,
        page_size: int = 8,
    ) -> dict[str, Any]:
        """药品列表 + facet。"""
        async with self._engine.connect() as conn:
            stmt = kb_drugs.select().where(kb_drugs.c.is_deleted == 0)
            if keyword:
                stmt = stmt.where(kb_drugs.c.name.ilike(f"%{keyword}%"))
            if category and category != "全部":
                stmt = stmt.where(kb_drugs.c.category == category)
            if essential_only:
                stmt = stmt.where(kb_drugs.c.is_essential == True)  # noqa: E712

            total = (await conn.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
            rows = (await conn.execute(
                stmt.order_by(kb_drugs.c.name).offset((page - 1) * page_size).limit(page_size)
            )).fetchall()
            items = [row_to_dict(r) for r in rows]

            cat_rows = (await conn.execute(
                select(kb_drugs.c.category, func.count()).where(kb_drugs.c.is_deleted == 0)
                .group_by(kb_drugs.c.category)
            )).fetchall()
            total_count = sum(c for _, c in cat_rows)
            categories = [{"name": "全部", "count": total_count}] + [
                {"name": cat, "count": cnt} for cat, cnt in cat_rows if cat
            ]

            essential_count = (await conn.execute(
                select(func.count()).select_from(kb_drugs).where(
                    (kb_drugs.c.is_deleted == 0) & (kb_drugs.c.is_essential == True)  # noqa: E712
                )
            )).scalar() or 0

        return {
            "items": items, "total": total, "page": page, "pageSize": page_size,
            "facets": {"categories": categories, "essentialCount": essential_count},
        }

    async def list_drug_options(self) -> list[dict[str, Any]]:
        """药品轻量选项（id+name+category），供「相互作用核查」下拉。"""
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                select(kb_drugs.c.id, kb_drugs.c.name, kb_drugs.c.category)
                .where(kb_drugs.c.is_deleted == 0)
                .order_by(kb_drugs.c.name)
            )).fetchall()
        return [{"id": r.id, "name": r.name, "cat": r.category} for r in rows]

    # ── 种子数据 upsert ─────────────────────────────────────────

    async def upsert_literature(self, data: dict[str, Any]) -> None:
        """幂等插入/更新文献。"""
        async with self._engine.begin() as conn:
            existing = await conn.execute(
                select(kb_literatures.c.id).where(kb_literatures.c.id == data["id"])
            )
            if existing.fetchone():
                values = {k: data[k] for k in data if k != "id"}
                await conn.execute(update(kb_literatures).where(kb_literatures.c.id == data["id"]).values(**values))
            else:
                await conn.execute(insert(kb_literatures).values(**data))

    async def upsert_guideline(self, data: dict[str, Any]) -> None:
        """幂等插入/更新指南。"""
        async with self._engine.begin() as conn:
            existing = await conn.execute(
                select(kb_guidelines.c.id).where(kb_guidelines.c.id == data["id"])
            )
            if existing.fetchone():
                values = {k: data[k] for k in data if k != "id"}
                await conn.execute(update(kb_guidelines).where(kb_guidelines.c.id == data["id"]).values(**values))
            else:
                await conn.execute(insert(kb_guidelines).values(**data))

    async def upsert_drug(self, data: dict[str, Any]) -> None:
        """幂等插入/更新药品。"""
        async with self._engine.begin() as conn:
            existing = await conn.execute(
                select(kb_drugs.c.id).where(kb_drugs.c.id == data["id"])
            )
            if existing.fetchone():
                values = {k: data[k] for k in data if k != "id"}
                await conn.execute(update(kb_drugs).where(kb_drugs.c.id == data["id"]).values(**values))
            else:
                await conn.execute(insert(kb_drugs).values(**data))
