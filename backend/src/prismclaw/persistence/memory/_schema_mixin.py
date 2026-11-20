"""PostgreSQL 记忆后端 schema 初始化。"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import async_sessionmaker

from prismclaw.persistence.memory._base_mixin import _PostgresBackendBase
from prismclaw.persistence.sql_runner import apply_core_schema

logger = logging.getLogger(__name__)


class SchemaMixin(_PostgresBackendBase):
    async def _init_db(self) -> None:
        if self._migrated:
            return

        async with self._init_lock:
            if self._migrated:
                return

            self._async_sessionmaker = async_sessionmaker(
                self._engine, expire_on_commit=False
            )
            await apply_core_schema(self._engine)
            self._migrated = True
            logger.info("PostgreSQL 后端 schema 初始化完成")
