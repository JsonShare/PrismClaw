from __future__ import annotations

from typing import Any

# NOTE: This module deliberately uses **no ORM**. The rest of the codebase writes
# SQL as raw text (see ``prismclaw.persistence.memory.postgres`` for the asynchronous
# PostgreSQL path) — there is no Alembic / no ``DeclarativeBase`` anywhere else.
# The web user store follows the same convention: ``init_schema`` runs idempotent
# ``CREATE TABLE IF NOT EXISTS`` DDL and ``UserRepository`` issues parameterised
# ``text()`` statements.

# 用户全部在 pc_users 单表，不再 join pc_users_config（该表已废弃删除）。
ALL_COLUMNS = (
    "u.id, u.password_hash, u.email, u.nickname, "
    "u.disabled, u.failed_attempts, u.locked_until, u.last_login_at, "
    "u.password_changed_at, u.created_at, u.updated_at"
)

# 读取 SQL 模板：所有 SELECT 都通过此 FROM 子句
FROM_CLAUSE = "pc_users u"


# ── Idempotent schema DDL (PostgreSQL only, mirrors deploy/sql/init_postgres.sql) ──

CREATE_USERS_TABLE_POSTGRES = """
CREATE TABLE IF NOT EXISTS pc_users (
    id                  TEXT        PRIMARY KEY,
    password_hash       TEXT        NOT NULL,
    email               TEXT,
    nickname            TEXT,
    disabled            BOOLEAN     NOT NULL DEFAULT FALSE,
    failed_attempts     INTEGER     NOT NULL DEFAULT 0,
    locked_until        TIMESTAMP,
    last_login_at       TIMESTAMP,
    password_changed_at TIMESTAMP,
    created_at          TIMESTAMP   NOT NULL DEFAULT now(),
    updated_at          TIMESTAMP   NOT NULL DEFAULT now()
)
"""

INDEX_USERS_POSTGRES = [
    "CREATE INDEX IF NOT EXISTS idx_pc_users_disabled ON pc_users (disabled)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_pc_users_email_lower "
    "ON pc_users (lower(email)) WHERE email IS NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_pc_users_nickname_lower "
    "ON pc_users (lower(nickname)) WHERE nickname IS NOT NULL",
]

# Keep ``updated_at`` fresh on every update without app code (Postgres only).
TOUCH_TRIGGER_POSTGRES = [
    """
    CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS TRIGGER AS $$
    BEGIN
        NEW.updated_at = now();
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """,
    "DROP TRIGGER IF EXISTS trg_pc_users_touch ON pc_users",
    """
    CREATE TRIGGER trg_pc_users_touch
        BEFORE UPDATE ON pc_users
        FOR EACH ROW
        EXECUTE FUNCTION touch_updated_at();
    """,
]


async def init_schema(engine: Any) -> None:
    """Idempotent schema creation (PostgreSQL only).

    Wrapped in ``pg_advisory_xact_lock`` to prevent concurrent DDL races when
    multiple workers start simultaneously against a shared database.
    """
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.execute(text("SELECT pg_advisory_xact_lock(742100)"))
        await conn.execute(text(CREATE_USERS_TABLE_POSTGRES))

        # 迁移必须在建索引之前：历史表里残留 role 列与 pc_users_config 表，
        # 先清理再建索引，避免 UndefinedColumnError。
        await _migrate_drop_role_and_config(conn)
        await _migrate_user_identity(conn)

        for stmt in INDEX_USERS_POSTGRES:
            await conn.execute(text(stmt))
        for stmt in TOUCH_TRIGGER_POSTGRES:
            await conn.execute(text(stmt))


async def _migrate_drop_role_and_config(conn: Any) -> None:
    """Drop the obsolete ``pc_users.role`` column and ``pc_users_config`` table.

    The role column belonged to the retired local-helper admin/member model and
    the config table held per-user preferences that are no longer used. Both
    removals are idempotent: no-ops when already gone.
    """
    from sqlalchemy import text

    # 1) pc_users.role 列（DROP COLUMN 会自动清理其上的约束与列索引）
    col_check = await conn.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'pc_users' AND column_name = 'role'"
    ))
    if col_check.first() is not None:
        import logging as _log
        _log.getLogger(__name__).info("Dropping pc_users.role column")
        await conn.execute(text("DROP INDEX IF EXISTS idx_pc_users_role"))
        await conn.execute(text("ALTER TABLE pc_users DROP COLUMN role"))

    # 2) pc_users_config 表（含历史 tier_default→mode_default 迁移目标）
    tbl_check = await conn.execute(text("SELECT to_regclass('pc_users_config')"))
    row = tbl_check.first()
    if row is not None and row[0] is not None:
        import logging as _log
        _log.getLogger(__name__).info("Dropping pc_users_config table")
        await conn.execute(text("DROP TABLE IF EXISTS pc_users_config CASCADE"))


async def _migrate_user_identity(conn: Any) -> None:
    """补建 email/nickname 列、回填昵称、消除忽略大小写冲突。

    历史账号没有昵称，回填为 id（id 字符集与昵称规则兼容）。
    回填后若出现忽略大小写冲突（如 id 为 Foo 与 foo），按 id 升序保留先出现者，
    后来者依次追加 _2 / _3 …，保证唯一索引可以建立。整个过程幂等。
    """
    from sqlalchemy import text

    await conn.execute(text("ALTER TABLE pc_users ADD COLUMN IF NOT EXISTS email TEXT"))
    await conn.execute(text("ALTER TABLE pc_users ADD COLUMN IF NOT EXISTS nickname TEXT"))
    await conn.execute(text("UPDATE pc_users SET nickname = id WHERE nickname IS NULL"))

    dup = await conn.execute(
        text(
            "SELECT lower(nickname) FROM pc_users WHERE nickname IS NOT NULL "
            "GROUP BY 1 HAVING count(*) > 1 LIMIT 1"
        )
    )
    if dup.first() is None:
        return

    await conn.execute(text(
        """
        DO $$
        DECLARE
            rec RECORD;
            base TEXT;
            candidate TEXT;
            taken TEXT[] := '{}';
            n INT;
        BEGIN
            FOR rec IN SELECT id, nickname FROM pc_users ORDER BY id LOOP
                base := rec.nickname;
                candidate := base;
                n := 1;
                WHILE lower(candidate) = ANY(taken) LOOP
                    n := n + 1;
                    candidate := base || '_' || n::text;
                END LOOP;
                taken := taken || lower(candidate);
                IF candidate <> base THEN
                    UPDATE pc_users SET nickname = candidate WHERE id = rec.id;
                END IF;
            END LOOP;
        END $$;
        """
    ))
