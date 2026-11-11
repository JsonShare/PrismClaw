"""研究简报持久化 — 基于 SQLAlchemy Core 的 CRUD。

pc_medical_briefs 表的读写操作，供 analysis_pipeline.py / api/briefs.py 使用。

方法签名与 MedicalStore 对齐：
- 写入方法要求 user_id 参数
- 查询方法支持分页与过滤
- row_to_dict 统一转换
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import (
    select,
    insert,
    update,
    func,
)
from sqlalchemy.ext.asyncio import AsyncEngine

from ..models.tables import briefs, BRIEF_COLUMNS, patient_reports
from .helpers import row_to_dict

logger = logging.getLogger(__name__)


class BriefStore:
    """研究简报存储 — pc_medical_briefs CRUD。

    写入方法（create_brief / update_brief / delete_brief）
    全部要求传入 user_id 参数。
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    # ── 创建 ────────────────────────────────────────────────

    async def create_brief(
        self,
        brief_id: str,
        patient_id: str,
        report_ids: list[str],
        *,
        user_id: str = "system",
    ) -> None:
        """创建一条研究简报记录（状态=分析中）。"""
        now = datetime.utcnow()
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(briefs).values(
                    id=brief_id,
                    patient_id=patient_id,
                    report_ids_json=report_ids,
                    status="分析中",
                    version=1,
                    started_at=now,
                    create_at=now,
                    create_by=user_id,
                    update_at=now,
                    update_by=user_id,
                    is_deleted=0,
                )
            )
        logger.info("简报创建成功: %s (patient=%s, reports=%d)", brief_id, patient_id, len(report_ids))

    # ── 查询 ────────────────────────────────────────────────

    async def get_brief(self, brief_id: str) -> dict[str, Any] | None:
        """按 ID 查询简报（逻辑删除不可见）。"""
        async with self._engine.connect() as conn:
            result = await conn.execute(
                select(briefs).where(
                    (briefs.c.id == brief_id) & (briefs.c.is_deleted == 0)
                )
            )
            row = result.fetchone()
            return dict(row_to_dict(row)) if row else None

    async def list_briefs(
        self,
        patient_id: str = "",
        status: str = "",
        create_by: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """分页查询简报列表。返回 (items, total)。"""
        async with self._engine.connect() as conn:
            base_where = briefs.c.is_deleted == 0
            if patient_id:
                base_where = base_where & (briefs.c.patient_id == patient_id)
            if status:
                base_where = base_where & (briefs.c.status == status)
            if create_by:
                base_where = base_where & (briefs.c.create_by == create_by)

            # 计数
            total = (
                await conn.execute(
                    select(func.count()).select_from(briefs).where(base_where)
                )
            ).scalar() or 0

            # 分页
            stmt = (
                select(briefs)
                .where(base_where)
                .order_by(briefs.c.update_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            result = await conn.execute(stmt)
            items = [dict(row_to_dict(row)) for row in result.fetchall()]
            return items, total

    async def has_active_brief(self, patient_id: str) -> bool:
        """检查该患者是否有正在分析中的简报（防重复创建）。"""
        async with self._engine.connect() as conn:
            result = await conn.execute(
                select(func.count()).select_from(briefs).where(
                    (briefs.c.patient_id == patient_id)
                    & (briefs.c.is_deleted == 0)
                    & (briefs.c.status == "分析中")
                )
            )
            return (result.scalar() or 0) > 0

    async def list_briefs_by_patient(
        self, patient_id: str, limit: int = 100,
    ) -> list[dict[str, Any]]:
        """某患者的简报列表（不分行页，用于患者详情页右栏 Tab）。"""
        async with self._engine.connect() as conn:
            stmt = (
                select(briefs)
                .where(
                    (briefs.c.patient_id == patient_id)
                    & (briefs.c.is_deleted == 0)
                )
                .order_by(briefs.c.update_at.desc())
                .limit(limit)
            )
            result = await conn.execute(stmt)
            return [dict(row_to_dict(row)) for row in result.fetchall()]

    async def list_unanalyzed_reports(self, patient_id: str) -> list[str]:
        """查询该患者已解析但尚未被任何非废弃简报覆盖的报告 ID。

        逻辑：取患者所有已解析报告 → 排除已在活跃简报（非废弃、非删除）report_ids_json 中的。
        """
        async with self._engine.connect() as conn:
            # 已解析报告
            parsed_result = await conn.execute(
                select(patient_reports.c.id).where(
                    (patient_reports.c.patient_id == patient_id)
                    & (patient_reports.c.is_deleted == 0)
                    & (patient_reports.c.status == "已解析")
                )
            )
            all_parsed = {row[0] for row in parsed_result.fetchall()}

            if not all_parsed:
                return []

            # 活跃简报已覆盖的报告 ID
            active_result = await conn.execute(
                select(briefs.c.report_ids_json).where(
                    (briefs.c.patient_id == patient_id)
                    & (briefs.c.is_deleted == 0)
                    & (briefs.c.status != "已废弃")
                )
            )
            covered: set[str] = set()
            for row in active_result.fetchall():
                ids = row[0] if isinstance(row[0], list) else []
                covered.update(str(i) for i in ids)

            unanalyzed = all_parsed - covered
            return sorted(unanalyzed)

    # ── 更新 ────────────────────────────────────────────────

    async def update_brief(
        self, brief_id: str, data: dict[str, Any], *, user_id: str,
    ) -> None:
        """更新简报记录。user_id: 写入 update_by。"""
        now = datetime.utcnow()
        values: dict[str, Any] = {"update_at": now, "update_by": user_id}
        for key in BRIEF_COLUMNS:
            if key in data:
                values[key] = data[key]
        async with self._engine.begin() as conn:
            await conn.execute(
                update(briefs).where(briefs.c.id == brief_id).values(**values)
            )
        logger.info("简报更新成功: %s", brief_id)

    async def update_brief_status(
        self,
        brief_id: str,
        status: str,
        *,
        error_message: str = "",
        user_id: str = "system",
    ) -> None:
        """更新简报状态（便捷方法）。"""
        data: dict[str, Any] = {"status": status}
        if error_message:
            data["error_message"] = error_message
        if status in ("已通过", "待修正", "已废弃"):
            data["finished_at"] = datetime.utcnow()
        await self.update_brief(brief_id, data, user_id=user_id)

    async def increment_version(self, brief_id: str, *, user_id: str) -> int:
        """简报版本递增，返回新版本号。用于 reanalyze 端点。"""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                select(briefs.c.version).where(briefs.c.id == brief_id)
            )
            row = result.fetchone()
            old_version = row[0] if row else 1
            new_version = old_version + 1
            now = datetime.utcnow()
            await conn.execute(
                update(briefs)
                .where(briefs.c.id == brief_id)
                .values(
                    version=new_version,
                    status="分析中",
                    error_message="",
                    started_at=now,
                    finished_at=None,
                    update_at=now,
                    update_by=user_id,
                )
            )
        logger.info("简报版本递增: %s → v%d", brief_id, new_version)
        return new_version

    # ── 删除 ────────────────────────────────────────────────

    async def delete_brief(self, brief_id: str, *, user_id: str) -> bool:
        """逻辑删除简报（is_deleted = 1）。"""
        now = datetime.utcnow()
        async with self._engine.begin() as conn:
            result = await conn.execute(
                update(briefs)
                .where((briefs.c.id == brief_id) & (briefs.c.is_deleted == 0))
                .values(is_deleted=1, update_at=now, update_by=user_id)
            )
        deleted = result.rowcount > 0
        if deleted:
            logger.info("简报删除成功: %s", brief_id)
        return deleted
