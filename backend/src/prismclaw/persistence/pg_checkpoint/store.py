from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prismclaw.persistence.sql_runner import apply_core_schema

_UPSERT = """
INSERT INTO pc_run_snapshots (
    run_id, session_id, payload, created_at, updated_at
) VALUES (
    :run_id, :session_id, CAST(:payload AS jsonb), :created_at, :updated_at
)
ON CONFLICT (run_id) DO UPDATE SET
    session_id = EXCLUDED.session_id,
    payload = EXCLUDED.payload,
    updated_at = EXCLUDED.updated_at
"""

_SELECT_BY_RUN = """
SELECT payload FROM pc_run_snapshots WHERE run_id = :run_id
"""

_SELECT_BY_SESSION = """
SELECT payload FROM pc_run_snapshots
WHERE session_id = :session_id
ORDER BY updated_at DESC
"""


class PostgresRunSnapshotStore:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        payload_parser: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._payload_parser = payload_parser

    async def _init_schema(self) -> None:
        await apply_core_schema(self._session_factory)

    async def save(self, snapshot: Any) -> None:
        await self._init_schema()
        run_id = snapshot.run_state.run_id
        session_id = snapshot.run_state.session_id
        if not run_id or not session_id:
            return
        now_ms = float(snapshot.created_at_epoch_ms) / 1000.0
        payload = json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=True)
        async with self._session_factory() as session:
            await session.execute(
                text(_UPSERT),
                {
                    "run_id": run_id,
                    "session_id": session_id,
                    "payload": payload,
                    "created_at": now_ms,
                    "updated_at": now_ms,
                },
            )
            await session.commit()

    async def load_latest(self, run_id: str) -> Any | None:
        await self._init_schema()
        async with self._session_factory() as session:
            result = await session.execute(
                text(_SELECT_BY_RUN), {"run_id": run_id}
            )
            row = result.first()
            if row is None:
                return None
            payload = row[0]
            return self._parse_payload(payload)

    async def list_for_session(self, session_id: str) -> tuple[Any, ...]:
        await self._init_schema()
        async with self._session_factory() as session:
            result = await session.execute(
                text(_SELECT_BY_SESSION), {"session_id": session_id}
            )
            snapshots: list[Any] = []
            for row in result:
                payload = row[0]
                snapshot = self._parse_payload(payload)
                if snapshot is not None:
                    snapshots.append(snapshot)
            return tuple(snapshots)

    def _parse_payload(self, payload: Any) -> Any:
        """Parse a JSONB payload into a RunSnapshot using the injected parser."""
        if self._payload_parser is None:
            return None
        if isinstance(payload, str):
            return self._payload_parser(payload)
        if isinstance(payload, dict):
            return self._payload_parser(json.dumps(payload))
        return None
