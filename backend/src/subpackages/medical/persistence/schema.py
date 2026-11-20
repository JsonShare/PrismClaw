"""医疗域 Schema 确保 — DDL 见 deploy/sql/init_medical.sql。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

from prismclaw.persistence.sql_runner import apply_domain_schema


async def ensure_medical_schema(engine: AsyncEngine) -> None:
    await apply_domain_schema(engine, "medical")
