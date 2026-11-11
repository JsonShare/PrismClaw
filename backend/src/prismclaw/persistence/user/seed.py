from __future__ import annotations

from prismclaw.schema.storage.storage_types import UserStore
from prismclaw.infra.settings import WebSettings


async def seed_from_config(
    user_store: UserStore,
    web_settings: WebSettings,
    *,
    bootstrap_admin_id: str = "",
    bootstrap_admin_password: str = "",
) -> dict:
    """One-time migration: populate the ``users`` table from config.

    Runs only when the table is empty. Plaintext config passwords are
    re-hashed (never stored as-is). Returns a summary for logging.

    Seed order:
      1. ``bootstrap_admin_id`` / ``bootstrap_admin_password`` env vars
         (preferred for cloud — keeps secrets out of the config file).
      2. ``channels.web.users`` from ``backend/configs/server.yaml`` (legacy).

    After the first successful seed the config ``users`` block is ignored for
    authentication and may be removed.

    Concurrency: inserts use ``ON CONFLICT (id) DO NOTHING`` (Postgres) so
    that multiple workers starting simultaneously against a shared database
    will not race on primary-key conflicts.  The ``count() > 0`` check is a
    fast-path optimisation, not a correctness gate.
    """
    if await user_store.count() > 0:
        return {"seeded": False, "reason": "table_not_empty", "created": []}

    created: list[str] = []

    if bootstrap_admin_id and bootstrap_admin_password:
        inserted = await user_store.seed_insert(
            user_id=bootstrap_admin_id,
            password=bootstrap_admin_password,
            nickname=bootstrap_admin_id,
        )
        if inserted:
            created.append(bootstrap_admin_id)

    for u in web_settings.users or []:
        inserted = await user_store.seed_insert(
            user_id=u.id,
            password=u.password,
            nickname=u.id,
        )
        if inserted:
            created.append(u.id)

    return {"seeded": bool(created), "reason": "ok", "created": created}
