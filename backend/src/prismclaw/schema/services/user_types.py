"""User data container — pure data, no behaviour.

Moved from kernel.user to contracts so that contracts.infra.storage_types
can reference User without creating a kernel→contracts→kernel circular dependency.
kernel.user re-exports for backward compatibility.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class User:
    """A row of the ``pc_users`` table.

    This is a plain data container (NOT a SQLAlchemy ORM model).  The
    repository builds it from a ``RowMapping`` so the rest of the code
    can keep using attribute access unchanged.
    """

    id: str
    password_hash: str
    disabled: bool
    failed_attempts: int
    locked_until: datetime | None
    last_login_at: datetime | None
    password_changed_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None
    # 邮箱（找回密码/邮箱登录用，历史账号可为空）；昵称（非空、忽略大小写唯一）。
    email: str | None = None
    nickname: str | None = None
