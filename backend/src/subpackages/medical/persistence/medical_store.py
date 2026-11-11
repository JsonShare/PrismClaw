"""医疗域存储 — 基于 SQLAlchemy Core 的持久化。

使用 AsyncEngine 执行，不使用 ORM。

表集（与 schema.py 一致，6 表 + 1 视图）：
- pc_medical_patients          患者档案 + 画像（合并原 patients + patients_extended）
- pc_medical_patient_reports   统一临床文档表（合并原 patient_reports + processing_tasks + records）
- pc_medical_consultations     咨询会话登记（域侧记录，核心不感知）
- v_patient_timeline           派生视图（从 patient_reports 聚合，不存表；get_timeline 直接查询 patient_reports）

KB 三表由独立的 KbStore 承载（见 kb_store.py）。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, date
from typing import Any

from sqlalchemy import (
    select,
    insert,
    update,
    func,
    or_,
    String,
)
from sqlalchemy.ext.asyncio import AsyncEngine

from ..models.tables import (
    patients,
    patient_reports,
    consultations,
    PATIENT_COLUMNS,
    REPORT_COLUMNS,
)
from .helpers import row_to_dict

logger = logging.getLogger(__name__)


def mask_id_number(raw: str) -> str:
    """证件号脱敏：保留前 6 位 + 后 4 位，中间用 8 个 * 遮蔽。

    与种子数据约定一致（18 位身份证 → 110105********2214）。
    过短（≤10 位）无法安全遮蔽的，仅保留首字符、其余打码。
    前端只回显脱敏值，原值不落库。
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    if len(raw) <= 10:
        return raw[0] + "*" * (len(raw) - 1)
    return raw[:6] + "*" * 8 + raw[-4:]


class MedicalStore:
    """医疗域存储 — 基于 SQLAlchemy Core。

    患者与统一临床文档表的 CRUD。
    写入方法（upsert_patient / create_report / update_report / delete_report）
    全部要求传入 user_id 参数，不再硬编码 'default'。
    查询方法支持 create_by 行级过滤。
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    # ── 患者操作 ─────────────────────────────────────────────

    async def upsert_patient(
        self, patient_id: str, data: dict[str, Any], *, user_id: str,
    ) -> None:
        """创建或更新患者信息（合并档案 + 画像字段）。

        user_id: 当前操作者 ID，写入 create_by / update_by。
        """
        now = datetime.utcnow()
        values = {k: data[k] for k in PATIENT_COLUMNS if k in data}
        async with self._engine.begin() as conn:
            existing = await conn.execute(
                select(patients.c.id).where(patients.c.id == patient_id)
            )
            if existing.fetchone():
                values["update_at"] = now
                values["update_by"] = user_id
                await conn.execute(
                    update(patients).where(patients.c.id == patient_id).values(**values)
                )
            else:
                await conn.execute(
                    insert(patients).values(
                        id=patient_id,
                        create_at=now,
                        create_by=user_id,
                        update_at=now,
                        update_by=user_id,
                        is_deleted=0,
                        **values,
                    )
                )
        logger.info("患者 upsert 成功: %s", patient_id)

    async def create_patient(
        self,
        *,
        name: str,
        id_type: str,
        id_number: str,
        user_id: str,
    ) -> str:
        """新建患者档案（生成 uuid id，脱敏证件号入库）。

        name 必填；id_type / id_number 可选，传入则脱敏后存入 id_number_masked。
        返回新建患者 id。create_by / update_by 写入 user_id 实现行级归属。
        """
        patient_id = str(uuid.uuid4())
        now = datetime.utcnow()
        masked = mask_id_number(id_number)
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(patients).values(
                    id=patient_id,
                    name=name.strip(),
                    id_type=(id_type or "").strip(),
                    id_number_masked=masked,
                    create_at=now,
                    create_by=user_id,
                    update_at=now,
                    update_by=user_id,
                    is_deleted=0,
                )
            )
        logger.info("患者创建成功: %s (name=%s)", patient_id, name)
        return patient_id

    async def get_patient(self, patient_id: str) -> dict[str, Any] | None:
        """按 ID 查询患者信息（逻辑删除的记录不可见）。"""
        async with self._engine.connect() as conn:
            result = await conn.execute(
                select(patients).where(
                    (patients.c.id == patient_id) & (patients.c.is_deleted == 0)
                )
            )
            row = result.fetchone()
            return dict(row_to_dict(row)) if row else None

    async def list_patients(
        self,
        search: str = "",
        tags: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
        *,
        create_by: str | None = None,
    ) -> list[dict[str, Any]]:
        """搜索患者（按 name/primary_diagnosis 模糊匹配，按 tags 过滤）。

        limit/offset: 服务端分页。默认 limit=20 仅为兼容旧调用方，
        分页场景务必由调用方显式传入（配合 count_patients 取总数）。
        create_by: 行级过滤，仅返回该用户创建的患者档案。None 不过滤。
        """
        async with self._engine.connect() as conn:
            stmt = (
                select(patients)
                .where(patients.c.is_deleted == 0)
                .order_by(patients.c.update_at.desc())
                .limit(limit)
                .offset(offset)
            )
            if create_by:
                stmt = stmt.where(patients.c.create_by == create_by)
            if search:
                stmt = stmt.where(
                    or_(
                        patients.c.name.ilike(f"%{search}%"),
                        patients.c.primary_diagnosis.ilike(f"%{search}%"),
                    )
                )
            if tags:
                for tag in tags:
                    stmt = stmt.where(patients.c.tags_json.cast(String).ilike(f'%"{tag}"%'))
            result = await conn.execute(stmt)
            return [dict(row_to_dict(row)) for row in result.fetchall()]

    async def list_patient_candidates(
        self,
        *,
        create_by: str,
    ) -> list[dict[str, Any]]:
        """归属匹配的候选全集：只取判定需要的四个字段。

        不分页——患者档案是几百条量级的个人数据，全量取回在内存里做姓名匹配，
        比让数据库逐次 ilike 更简单也更可控（归一规则在 Python 侧）。
        """
        async with self._engine.connect() as conn:
            result = await conn.execute(
                select(
                    patients.c.id,
                    patients.c.name,
                    patients.c.gender,
                    patients.c.birth_date,
                )
                .where(patients.c.is_deleted == 0)
                .where(patients.c.create_by == create_by)
            )
            return [dict(row_to_dict(row)) for row in result.fetchall()]

    async def count_patients(
        self,
        search: str = "",
        tags: list[str] | None = None,
        *,
        create_by: str | None = None,
    ) -> int:
        """患者总数（与 list_patients 同一套过滤条件），供分页算总页数。"""
        async with self._engine.connect() as conn:
            stmt = select(func.count()).select_from(patients).where(
                patients.c.is_deleted == 0
            )
            if create_by:
                stmt = stmt.where(patients.c.create_by == create_by)
            if search:
                stmt = stmt.where(
                    or_(
                        patients.c.name.ilike(f"%{search}%"),
                        patients.c.primary_diagnosis.ilike(f"%{search}%"),
                    )
                )
            if tags:
                for tag in tags:
                    stmt = stmt.where(patients.c.tags_json.cast(String).ilike(f'%"{tag}"%'))
            return (await conn.execute(stmt)).scalar() or 0

    async def count_reports_by_patients(
        self, patient_ids: list[str]
    ) -> dict[str, int]:
        """按患者 ID 批量统计临床文档数（逻辑删除的不计入）。

        返回 {patient_id: count}，未出现在结果中的患者视为 0。
        """
        if not patient_ids:
            return {}
        async with self._engine.connect() as conn:
            result = await conn.execute(
                select(
                    patient_reports.c.patient_id,
                    func.count().label("cnt"),
                )
                .where(
                    (patient_reports.c.is_deleted == 0)
                    & patient_reports.c.patient_id.in_(patient_ids)
                )
                .group_by(patient_reports.c.patient_id)
            )
            return {row[0]: int(row[1]) for row in result.fetchall()}

    async def get_stats(self, *, create_by: str | None = None) -> dict[str, Any]:
        """统计条指标。create_by: 行级过滤，仅统计该用户的患者。"""
        async with self._engine.connect() as conn:
            patient_where = (patients.c.is_deleted == 0)
            if create_by:
                patient_where = patient_where & (patients.c.create_by == create_by)

            total_patients = (
                await conn.execute(
                    select(func.count()).select_from(patients).where(patient_where)
                )
            ).scalar() or 0
            high_risk = (
                await conn.execute(
                    select(func.count()).select_from(patients).where(
                        patient_where & (patients.c.risk_level == "高危")
                    )
                )
            ).scalar() or 0

            report_where = (patient_reports.c.is_deleted == 0)
            if create_by:
                report_where = report_where & (patient_reports.c.create_by == create_by)

            reports_total = (
                await conn.execute(
                    select(func.count()).select_from(patient_reports).where(report_where)
                )
            ).scalar() or 0
            parsed = (
                await conn.execute(
                    select(func.count()).select_from(patient_reports).where(
                        report_where & (patient_reports.c.status == "已解析")
                    )
                )
            ).scalar() or 0
            parsing = (
                await conn.execute(
                    select(func.count()).select_from(patient_reports).where(
                        report_where
                        & (patient_reports.c.status.in_(["排队中", "OCR识别", "结构化提取", "语音转写"]))
                    )
                )
            ).scalar() or 0
        return {
            "archivedPatients": total_patients,
            "archivedMonthlyDelta": 0,
            "highRisk": high_risk,
            "reportsToday": reports_total,
            "reportsParsed": parsed,
            "reportsParsing": parsing,
        }

    # ── 统一临床文档操作 ─────────────────────────────────────────────

    async def create_report(
        self,
        report_id: str,
        patient_id: str | None,
        data: dict[str, Any],
        *,
        user_id: str,
    ) -> None:
        """创建一条临床文档记录（报告元数据 + 解析状态 + AI 抽取结果）。

        user_id: 当前操作者 ID，写入 create_by / update_by。
        """
        now = datetime.utcnow()
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(patient_reports).values(
                    id=report_id,
                    patient_id=patient_id,
                    report_type=data.get("report_type", "outpatient"),
                    file_name=data.get("file_name", ""),
                    file_type=data.get("file_type", "text"),
                    file_path=data.get("file_path", ""),
                    mime_type=data.get("mime_type", ""),
                    attachments_json=data.get("attachments_json", []),
                    hospital_name=data.get("hospital_name", ""),
                    doctor_name=data.get("doctor_name", ""),
                    patient_name_raw=data.get("patient_name_raw", ""),
                    match_reason=data.get("match_reason", ""),
                    status=data.get("status", "排队中"),
                    progress=data.get("progress", 0),
                    retry_count=data.get("retry_count", 0),
                    error_message=data.get("error_message", ""),
                    raw_text=data.get("raw_text", ""),
                    visit_date=data.get("visit_date"),
                    department=data.get("department", ""),
                    diagnosis=data.get("diagnosis", ""),
                    narrative_json=data.get("narrative_json", {}),
                    labs_json=data.get("labs_json", []),
                    medications_json=data.get("medications_json", []),
                    tags_json=data.get("tags_json", []),
                    summary=data.get("summary", ""),
                    started_at=data.get("started_at"),
                    finished_at=data.get("finished_at"),
                    create_at=now,
                    create_by=user_id,
                    update_at=now,
                    update_by=user_id,
                    is_deleted=0,
                )
            )
        logger.info("临床文档创建成功: %s", report_id)

    async def get_report(self, report_id: str) -> dict[str, Any] | None:
        """按 ID 查询文档（逻辑删除不可见）。"""
        async with self._engine.connect() as conn:
            result = await conn.execute(
                select(patient_reports).where(
                    (patient_reports.c.id == report_id) & (patient_reports.c.is_deleted == 0)
                )
            )
            row = result.fetchone()
            return dict(row_to_dict(row)) if row else None

    async def list_reports(
        self,
        keyword: str = "",
        page: int = 1,
        page_size: int = 6,
        *,
        create_by: str | None = None,
        unassigned: bool = False,
        report_type: str = "",
        status: str = "",
    ) -> tuple[list[dict[str, Any]], int]:
        """分页查询全部临床文档（病历页列表 / 报告检索用）。

        keyword 命中 patients.name + patient_reports.diagnosis + summary + file_name。
        始终 LEFT JOIN patients 以获取 patient_name 供前端显示。
        create_by: 行级过滤，仅返回该用户创建的报告。None 不过滤。
        unassigned: 只返回尚未归属患者的文档（patient_id IS NULL）——「待整理」队列用。
        report_type / status: Phase 1 报告检索的元数据过滤（精确匹配原始列值）。
        返回 (items, total)。
        """
        async with self._engine.connect() as conn:
            # LEFT JOIN 必须以 patient_reports 为主表：
            # 反过来的话 patient_id 为 NULL 的报告（刚上传、未归属）根本不会出现，
            # 「待整理」队列会永远为空。
            joins = patient_reports.join(
                patients,
                patients.c.id == patient_reports.c.patient_id,
                isouter=True,
            )
            base_where = patient_reports.c.is_deleted == 0

            if create_by:
                base_where = base_where & (patient_reports.c.create_by == create_by)

            if unassigned:
                base_where = base_where & patient_reports.c.patient_id.is_(None)

            if keyword:
                # 关键词命中患者名 / 诊断 / 报告摘要 / 文件名
                base_where = base_where & or_(
                    patient_reports.c.diagnosis.ilike(f"%{keyword}%"),
                    patients.c.name.ilike(f"%{keyword}%"),
                    patient_reports.c.summary.ilike(f"%{keyword}%"),
                    patient_reports.c.file_name.ilike(f"%{keyword}%"),
                )

            if report_type:
                base_where = base_where & (patient_reports.c.report_type == report_type)

            if status:
                base_where = base_where & (patient_reports.c.status == status)

            # 计数
            count_stmt = select(func.count()).select_from(joins).where(base_where)
            total = (await conn.execute(count_stmt)).scalar() or 0

            # 分页查询 — SELECT reports.*, patients.name AS patient_name
            stmt = (
                select(
                    patient_reports,
                    patients.c.name.label("patient_name"),
                )
                .select_from(joins)
                .where(base_where)
                .order_by(patient_reports.c.create_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )

            result = await conn.execute(stmt)
            items = [dict(row_to_dict(row)) for row in result.fetchall()]
            return items, total

    async def get_queue(
        self, *, create_by: str | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """获取处理队列（非终态文档）+ 汇总。

        create_by: 行级过滤。返回 (tasks, summary)，summary 含 doneToday/failed/retried。
        """
        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        async with self._engine.connect() as conn:
            report_where = (patient_reports.c.is_deleted == 0)
            if create_by:
                report_where = report_where & (patient_reports.c.create_by == create_by)

            # 非终态文档
            active_statuses = ["排队中", "OCR识别", "结构化提取", "语音转写"]
            active_stmt = (
                select(patient_reports)
                .where(
                    report_where
                    & patient_reports.c.status.in_(active_statuses)
                )
                .order_by(patient_reports.c.create_at.desc())
            )
            active_result = await conn.execute(active_stmt)
            tasks = [dict(row_to_dict(row)) for row in active_result.fetchall()]

            # 今日汇总
            done_today = (
                await conn.execute(
                    select(func.count()).select_from(patient_reports).where(
                        report_where
                        & (patient_reports.c.status == "已解析")
                        & (patient_reports.c.finished_at >= today_start)
                    )
                )
            ).scalar() or 0

            failed = (
                await conn.execute(
                    select(func.count()).select_from(patient_reports).where(
                        report_where
                        & (patient_reports.c.status == "失败")
                    )
                )
            ).scalar() or 0

            retried = (
                await conn.execute(
                    select(func.count()).select_from(patient_reports).where(
                        report_where
                        & (patient_reports.c.retry_count > 0)
                    )
                )
            ).scalar() or 0

            # 待归档：已解析但未归属到任何患者
            unassigned = (
                await conn.execute(
                    select(func.count()).select_from(patient_reports).where(
                        report_where
                        & (patient_reports.c.status == "已解析")
                        & (patient_reports.c.patient_id.is_(None))
                    )
                )
            ).scalar() or 0

        summary = {"doneToday": done_today, "failed": failed, "retried": retried, "unassigned": unassigned}
        return tasks, summary

    async def search_reports(
        self, criteria: dict[str, Any], limit: int = 20, offset: int = 0,
    ) -> list[dict[str, Any]]:
        """条件查询文档列表（支持 patient_id / diagnosis / report_type）。"""
        async with self._engine.connect() as conn:
            # 同 list_reports：以报告为主表，未归属的也能查到
            joins = patient_reports.join(
                patients,
                patients.c.id == patient_reports.c.patient_id,
                isouter=True,
            )
            stmt = (
                select(
                    patient_reports,
                    patients.c.name.label("patient_name"),
                )
                .select_from(joins)
                .where(patient_reports.c.is_deleted == 0)
                # 按就诊日期倒序（无日期的沉底，再用创建时间兜底）——
                # 必须在 SQL 层排序，前端分页内再排会导致跨页乱序
                .order_by(
                    patient_reports.c.visit_date.desc().nulls_last(),
                    patient_reports.c.create_at.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
            if "patient_id" in criteria:
                stmt = stmt.where(patient_reports.c.patient_id == criteria["patient_id"])
            if "diagnosis" in criteria:
                stmt = stmt.where(patient_reports.c.diagnosis.ilike(f"%{criteria['diagnosis']}%"))
            if "report_type" in criteria:
                stmt = stmt.where(patient_reports.c.report_type == criteria["report_type"])
            result = await conn.execute(stmt)
            return [dict(row_to_dict(row)) for row in result.fetchall()]

    async def list_patient_reports(
        self, patient_id: str, limit: int = 100, offset: int = 0,
    ) -> list[dict[str, Any]]:
        """某患者的临床文档（时间线「报告解析」卡用）。

        limit 默认 100 仅为兼容旧调用方；分页场景调用方需显式传 limit/offset。
        """
        return await self.search_reports(
            {"patient_id": patient_id}, limit=limit, offset=offset,
        )

    async def update_report(
        self, report_id: str, data: dict[str, Any], *, user_id: str,
    ) -> None:
        """更新文档记录。user_id: 写入 update_by。"""
        now = datetime.utcnow()
        values: dict[str, Any] = {"update_at": now, "update_by": user_id}
        for key in REPORT_COLUMNS:
            if key in data:
                values[key] = data[key]
        async with self._engine.begin() as conn:
            await conn.execute(
                update(patient_reports).where(patient_reports.c.id == report_id).values(**values)
            )
        logger.info("临床文档更新成功: %s", report_id)

    async def delete_report(
        self, report_id: str, *, user_id: str,
    ) -> bool:
        """逻辑删除文档（is_deleted = 1）。user_id: 写入 update_by。"""
        now = datetime.utcnow()
        async with self._engine.begin() as conn:
            result = await conn.execute(
                update(patient_reports)
                .where((patient_reports.c.id == report_id) & (patient_reports.c.is_deleted == 0))
                .values(is_deleted=1, update_at=now, update_by=user_id)
            )
        deleted = result.rowcount > 0
        if deleted:
            logger.info("临床文档删除成功: %s", report_id)
        return deleted

    # ── 时间线（派生，不存表）─────────────────────────────────────

    async def get_timeline(
        self, patient_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """患者时间线 = patient_reports 按患者聚合投影（event_id/type/date/title/detail）。"""
        async with self._engine.connect() as conn:
            stmt = (
                select(patient_reports)
                .where(
                    (patient_reports.c.patient_id == patient_id)
                    & (patient_reports.c.is_deleted == 0)
                )
                .order_by(patient_reports.c.create_at.desc())
                .limit(limit)
            )
            result = await conn.execute(stmt)
            rows = [dict(row_to_dict(row)) for row in result.fetchall()]
        events: list[dict[str, Any]] = []
        for r in rows:
            event_date = r.get("visit_date") or r.get("create_at")
            events.append(
                {
                    "event_id": r["id"],
                    "event_type": r.get("report_type", ""),
                    "event_date": (
                        event_date.isoformat()
                        if isinstance(event_date, (datetime, date))
                        else str(event_date or "")
                    ),
                    "title": r.get("diagnosis") or r.get("file_name", ""),
                    "detail": r.get("summary", ""),
                }
            )
        return events

    # ── 咨询会话登记（仅 insert，无 update / delete）──────────────────────

    async def insert_consultation(
        self,
        consultation_id: str,
        session_id: str,
        patient_id: str,
        doctor_id: str,
        title: str = "",
    ) -> None:
        """插入一条咨询登记行。同一患者可多次咨询，每次新建 session 和登记行。"""
        now = datetime.utcnow()
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(consultations).values(
                    id=consultation_id,
                    session_id=session_id,
                    patient_id=patient_id,
                    doctor_id=doctor_id,
                    title=title,
                    created_at=now,
                )
            )
        logger.info("咨询登记成功: session=%s patient=%s", session_id, patient_id)

    async def get_consultation_by_session(self, session_id: str) -> dict[str, Any] | None:
        """按 session_id 查询咨询登记（工具入口校验用）。"""
        async with self._engine.connect() as conn:
            result = await conn.execute(
                select(consultations).where(consultations.c.session_id == session_id)
            )
            row = result.fetchone()
            return dict(row_to_dict(row)) if row else None

    async def list_consultations(
        self, doctor_id: str, patient_id: str | None = None
    ) -> list[dict[str, Any]]:
        """查询医生的咨询列表，可选按患者过滤。"""
        async with self._engine.connect() as conn:
            stmt = (
                select(consultations)
                .where(consultations.c.doctor_id == doctor_id)
                .order_by(consultations.c.created_at.desc())
            )
            if patient_id:
                stmt = stmt.where(consultations.c.patient_id == patient_id)
            result = await conn.execute(stmt)
            return [dict(row_to_dict(row)) for row in result.fetchall()]
