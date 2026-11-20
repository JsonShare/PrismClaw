"""医疗域知识库存储 — KB 三表列表与种子 upsert。

表集：
- pc_medical_kb_literatures   文献
- pc_medical_kb_guidelines    指南
- pc_medical_kb_drugs         药品

与 MedicalStore 独立，通过 bundle 注册为 'medical_kb' Store。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from ..models.tables import kb_drugs, kb_guidelines, kb_literatures
from .helpers import row_to_dict


class KbStore:
    """知识库三表存储 — 列表查询 + 种子 upsert。"""

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
        """文献列表（按发布时间倒序）。"""
        async with self._engine.connect() as conn:
            base = kb_literatures.select().where(kb_literatures.c.is_deleted == 0)
            stmt = base
            if keyword:
                like = f"%{keyword}%"
                stmt = stmt.where(
                    kb_literatures.c.title.ilike(like)
                    | kb_literatures.c.title_en.ilike(like)
                    | kb_literatures.c.title_cn.ilike(like)
                )
            if category and category != "全部":
                stmt = stmt.where(kb_literatures.c.category == category)
            if year is not None:
                stmt = stmt.where(kb_literatures.c.year == year)

            total = (await conn.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
            rows = (await conn.execute(
                stmt.order_by(
                    kb_literatures.c.published_at.desc().nullslast(),
                    kb_literatures.c.year.desc(),
                ).offset((page - 1) * page_size).limit(page_size)
            )).fetchall()
            items = [row_to_dict(r) for r in rows]

        return {
            "items": items, "total": total, "page": page, "pageSize": page_size,
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
        """指南列表（默认按发布时间倒序）。"""
        async with self._engine.connect() as conn:
            stmt = kb_guidelines.select().where(kb_guidelines.c.is_deleted == 0)
            if keyword:
                like = f"%{keyword}%"
                stmt = stmt.where(
                    kb_guidelines.c.title.ilike(like)
                    | kb_guidelines.c.title_cn.ilike(like)
                    | kb_guidelines.c.authors.ilike(like)
                )
            if category and category != "全部":
                stmt = stmt.where(kb_guidelines.c.category == category)

            total = (await conn.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
            order_col = (
                (kb_guidelines.c.published_at.desc().nullslast(), kb_guidelines.c.year.desc())
                if sort == "year"
                else (kb_guidelines.c.title,)
            )
            rows = (await conn.execute(
                stmt.order_by(*order_col).offset((page - 1) * page_size).limit(page_size)
            )).fetchall()
            items = [row_to_dict(r) for r in rows]

        return {
            "items": items, "total": total, "page": page, "pageSize": page_size,
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
        """药品列表（前端按分类树在内存组目录）。"""
        async with self._engine.connect() as conn:
            stmt = kb_drugs.select().where(kb_drugs.c.is_deleted == 0)
            if keyword:
                # 商品名与通用名都要能命中（如「泰瑞沙」→ 奥希替尼 / Osimertinib）
                like = f"%{keyword}%"
                stmt = stmt.where(
                    kb_drugs.c.name.ilike(like) | kb_drugs.c.generic_name.ilike(like)
                )
            if category and category != "全部":
                stmt = stmt.where(kb_drugs.c.category == category)
            if essential_only:
                stmt = stmt.where(kb_drugs.c.is_essential == True)  # noqa: E712

            total = (await conn.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
            rows = (await conn.execute(
                stmt.order_by(kb_drugs.c.name).offset((page - 1) * page_size).limit(page_size)
            )).fetchall()
            items = [row_to_dict(r) for r in rows]

        return {
            "items": items, "total": total, "page": page, "pageSize": page_size,
        }

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
