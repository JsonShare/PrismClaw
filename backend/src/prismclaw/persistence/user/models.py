from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

from prismclaw.persistence.sql_runner import apply_core_schema

# 用户全部在 pc_users 单表（表结构见 deploy/sql/init_postgres.sql）。
ALL_COLUMNS = (
    "u.id, u.password_hash, u.email, u.nickname, "
    "u.disabled, u.failed_attempts, u.locked_until, u.last_login_at, "
    "u.password_changed_at, u.created_at, u.updated_at"
)

# 读取 SQL 模板：所有 SELECT 都通过此 FROM 子句
FROM_CLAUSE = "pc_users u"


async def init_schema(engine: AsyncEngine) -> None:
    await apply_core_schema(engine)
