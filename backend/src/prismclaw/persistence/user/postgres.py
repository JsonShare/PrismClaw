from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prismclaw.schema.services.user_types import User
from prismclaw.persistence.user.hashing import hash_password, verify_password
from prismclaw.persistence.user.models import ALL_COLUMNS, FROM_CLAUSE

_UNSET = object()

# Columns written on INSERT into pc_users, in the same order as the VALUES placeholders.
_USER_INSERT_COLUMNS = [
    "id",
    "password_hash",
    "email",
    "nickname",
    "disabled",
    "failed_attempts",
    "locked_until",
    "last_login_at",
    "password_changed_at",
    "created_at",
    "updated_at",
]


def _utcnow() -> datetime:
    """Naive UTC timestamp — matches the naive timestamp columns in the DDL."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _coerce_dt(v: Any) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v)
        except ValueError:
            return None
    return None


def _fmt_dt(v: datetime | None) -> datetime | None:
    """Return a naive datetime — asyncpg accepts datetime objects natively.
    Passing ISO strings breaks asyncpg's strict type checking on TIMESTAMP columns.
    """
    if v is None:
        return None
    return v.replace(tzinfo=None)


def _row_to_user(row: Any) -> User | None:
    if row is None:
        return None
    d = dict(row)
    return User(
        id=d["id"],
        password_hash=d["password_hash"],
        email=d.get("email"),
        nickname=d.get("nickname"),
        disabled=bool(d["disabled"]),
        failed_attempts=int(d["failed_attempts"]),
        locked_until=_coerce_dt(d["locked_until"]),
        last_login_at=_coerce_dt(d["last_login_at"]),
        password_changed_at=_coerce_dt(d["password_changed_at"]),
        created_at=_coerce_dt(d["created_at"]),
        updated_at=_coerce_dt(d["updated_at"]),
    )


class PostgresUserRepository:
    """Async CRUD + authentication for the ``pc_users`` table (raw SQL Core).

    PostgreSQL via ``asyncpg`` — the engine decides the dialect.
    Queries are parameterised ``text()`` statements, matching the project's
    existing SQL convention (``prismclaw.persistence.memory.postgres``).

    The ``User`` dataclass and all public method signatures are kept for
    compatibility with the ``UserStore`` Protocol defined in
    ``contracts/infra/storage_types`` via duck-typing — no explicit
    inheritance needed.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        lockout_threshold: int = 5,
        lockout_window_seconds: int = 900,
        scheme: str = "scrypt",
    ) -> None:
        self._sf = session_factory
        self._lockout_threshold = lockout_threshold
        self._lockout_window = lockout_window_seconds
        self._scheme = scheme

    # ── Reads ──────────────────────────────────────────────────────────
    async def get_by_id(self, user_id: str) -> User | None:
        async with self._sf() as s:
            res = await s.execute(
                text(f"SELECT {ALL_COLUMNS} FROM {FROM_CLAUSE} WHERE u.id = :id"),
                {"id": user_id},
            )
            return _row_to_user(res.mappings().first())

    async def list_all(self, *, include_disabled: bool = False) -> list[User]:
        sql = f"SELECT {ALL_COLUMNS} FROM {FROM_CLAUSE}"
        if not include_disabled:
            sql += " WHERE u.disabled = false"
        async with self._sf() as s:
            res = await s.execute(text(sql))
            return [_row_to_user(r) for r in res.mappings().all()]

    async def count(self) -> int:
        async with self._sf() as s:
            return int((await s.execute(text("SELECT COUNT(*) AS n FROM pc_users"))).scalar() or 0)

    # ── Authentication ─────────────────────────────────────────────────
    async def authenticate(self, identifier: str, password: str) -> User | None:
        """Verify credentials, enforcing lockout. Returns the user on success.

        ``identifier`` 可以是用户 id、邮箱或昵称（后两者忽略大小写），
        按 id > 邮箱 > 昵称的优先级匹配。

        Generic failure (unknown user / wrong password / disabled / locked) all
        return ``None`` so callers cannot enumerate accounts.

        Lockout accounting uses an atomic ``UPDATE … RETURNING`` so that
        concurrent login attempts across workers cannot lose increments.
        """
        async with self._sf() as s:
            row = (
                await s.execute(
                    text(
                        f"SELECT {ALL_COLUMNS} FROM {FROM_CLAUSE} "
                        "WHERE u.id = :identifier "
                        "   OR lower(u.email) = lower(:identifier) "
                        "   OR lower(u.nickname) = lower(:identifier) "
                        "ORDER BY CASE "
                        "    WHEN u.id = :identifier THEN 0 "
                        "    WHEN lower(u.email) = lower(:identifier) THEN 1 "
                        "    ELSE 2 END "
                        "LIMIT 1"
                    ),
                    {"identifier": identifier},
                )
            ).mappings().first()
            if row is None:
                return None
            user = _row_to_user(row)
            if user is None or user.disabled:
                return None

            now = _utcnow()
            if user.locked_until is not None and user.locked_until > now:
                return None

            if not verify_password(password, user.password_hash):
                # Atomic increment + conditional lock — single SQL statement
                # prevents lost-update when multiple workers process parallel
                # failed logins for the same account.
                lock_until = _fmt_dt(now + timedelta(seconds=self._lockout_window))
                result = await s.execute(
                    text(
                        "UPDATE pc_users "
                        "SET failed_attempts = failed_attempts + 1, "
                        "    locked_until = CASE "
                        "        WHEN failed_attempts + 1 >= :threshold THEN :lock_until "
                        "        ELSE locked_until "
                        "    END, "
                        "    updated_at = :now "
                        "WHERE id = :id "
                        "RETURNING failed_attempts, locked_until"
                    ),
                    {
                        "threshold": self._lockout_threshold,
                        "lock_until": lock_until,
                        "now": now,
                        "id": user.id,
                    },
                )
                updated = result.mappings().first()
                if updated:
                    user.failed_attempts = int(updated["failed_attempts"])
                    user.locked_until = _coerce_dt(updated["locked_until"])
                await s.commit()
                return None

            await s.execute(
                text(
                    "UPDATE pc_users SET failed_attempts = 0, locked_until = NULL, "
                    "last_login_at = :ll, updated_at = :now WHERE id = :id"
                ),
                {"ll": now, "now": now, "id": user.id},
            )
            await s.commit()
            user.failed_attempts = 0
            user.locked_until = None
            user.last_login_at = now
            return user

    # ── Writes ─────────────────────────────────────────────────────────
    async def create(
        self,
        *,
        user_id: str,
        password: str,
        email: str | None = None,
        nickname: str | None = None,
    ) -> User:
        async with self._sf() as s:
            async with s.begin():
                now = _utcnow()
                user_params = {
                    "id": user_id,
                    "password_hash": hash_password(password, scheme=self._scheme),
                    "email": email,
                    "nickname": nickname,
                    "disabled": False,
                    "failed_attempts": 0,
                    "locked_until": None,
                    "last_login_at": None,
                    "password_changed_at": now,
                    "created_at": now,
                    "updated_at": now,
                }
                cols = ", ".join(_USER_INSERT_COLUMNS)
                placeholders = ", ".join(f":{c}" for c in _USER_INSERT_COLUMNS)
                await s.execute(
                    text(f"INSERT INTO pc_users ({cols}) VALUES ({placeholders})"),
                    user_params,
                )
        created = await self.get_by_id(user_id)
        assert created is not None
        return created

    async def seed_insert(
        self,
        *,
        user_id: str,
        password: str,
        email: str | None = None,
        nickname: str | None = None,
    ) -> bool:
        """Idempotent insert for seeding — skips silently if the row already
        exists (``ON CONFLICT DO NOTHING``).

        Unlike :meth:`create`, this never raises on duplicate PK, making it
        safe for concurrent multi-worker startup seeding.
        """
        async with self._sf() as s:
            async with s.begin():
                now = _utcnow()
                user_params = {
                    "id": user_id,
                    "password_hash": hash_password(password, scheme=self._scheme),
                    "email": email,
                    "nickname": nickname,
                    "disabled": False,
                    "failed_attempts": 0,
                    "locked_until": None,
                    "last_login_at": None,
                    "password_changed_at": now,
                    "created_at": now,
                    "updated_at": now,
                }
                cols = ", ".join(_USER_INSERT_COLUMNS)
                placeholders = ", ".join(f":{c}" for c in _USER_INSERT_COLUMNS)
                sql = f"INSERT INTO pc_users ({cols}) VALUES ({placeholders}) ON CONFLICT (id) DO NOTHING"
                result = await s.execute(text(sql), user_params)
                return result.rowcount > 0

    async def get_by_email(self, email: str) -> User | None:
        """按邮箱（忽略大小写）查用户，用于找回密码与邮箱唯一性校验。"""
        async with self._sf() as s:
            res = await s.execute(
                text(
                    f"SELECT {ALL_COLUMNS} FROM {FROM_CLAUSE} "
                    "WHERE lower(u.email) = lower(:email) LIMIT 1"
                ),
                {"email": email},
            )
            return _row_to_user(res.mappings().first())

    async def nickname_exists(self, nickname: str) -> bool:
        """昵称是否已被占用（忽略大小写）。"""
        async with self._sf() as s:
            res = await s.execute(
                text("SELECT 1 FROM pc_users WHERE lower(nickname) = lower(:nickname) LIMIT 1"),
                {"nickname": nickname},
            )
            return res.first() is not None

    async def set_password(self, user_id: str, password: str) -> bool:
        async with self._sf() as s:
            exists = (
                await s.execute(text("SELECT id FROM pc_users WHERE id = :id"), {"id": user_id})
            ).mappings().first()
            if exists is None:
                return False
            now = _utcnow()
            await s.execute(
                text(
                    "UPDATE pc_users SET password_hash = :ph, password_changed_at = :pc, "
                    "failed_attempts = 0, locked_until = NULL, updated_at = :now WHERE id = :id"
                ),
                {
                    "ph": hash_password(password, scheme=self._scheme),
                    "pc": now,
                    "now": now,
                    "id": user_id,
                },
            )
            await s.commit()
            return True

    async def update_profile(
        self,
        user_id: str,
        **kwargs: Any,
    ) -> bool:
        async with self._sf() as s:
            exists = (
                await s.execute(text("SELECT id FROM pc_users WHERE id = :id"), {"id": user_id})
            ).mappings().first()
            if exists is None:
                return False

            user_sets: list[str] = []
            params: dict[str, Any] = {"id": user_id}

            if "disabled" in kwargs and kwargs["disabled"] is not _UNSET:
                user_sets.append("disabled = :disabled")
                params["disabled"] = bool(kwargs["disabled"])

            if not user_sets:
                return True

            async with s.begin():
                now = _utcnow()
                user_sets.append("updated_at = :now")
                params["now"] = now
                await s.execute(
                    text(f"UPDATE pc_users SET {', '.join(user_sets)} WHERE id = :id"), params
                )
            return True

    async def delete(self, user_id: str) -> bool:
        async with self._sf() as s:
            exists = (
                await s.execute(text("SELECT id FROM pc_users WHERE id = :id"), {"id": user_id})
            ).mappings().first()
            if exists is None:
                return False
            await s.execute(text("DELETE FROM pc_users WHERE id = :id"), {"id": user_id})
            await s.commit()
            return True
