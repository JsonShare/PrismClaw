from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import SmallInteger
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from prismclaw.persistence import sql_runner
from subpackages.medical.models import tables as medical_tables


@pytest.fixture(autouse=True)
def restore_applied_cache() -> Iterator[None]:
    previous = sql_runner._APPLIED.copy()
    sql_runner._APPLIED.clear()
    yield
    sql_runner._APPLIED.clear()
    sql_runner._APPLIED.update(previous)


def test_split_sql_preserves_quoted_and_dollar_quoted_semicolons() -> None:
    statements = sql_runner.split_sql(
        """
        SELECT ';';
        CREATE FUNCTION f() RETURNS TEXT AS $func$
        BEGIN
            RETURN ';';
        END;
        $func$ LANGUAGE plpgsql;
        SELECT 1;
        """
    )

    assert len(statements) == 3
    assert statements[0] == "SELECT ';'"
    assert "RETURN ';';" in statements[1]
    assert statements[2] == "SELECT 1"


async def test_schema_cache_distinguishes_databases_and_checks_session_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "init_postgres.sql").write_text("SELECT 1;", encoding="utf-8")
    monkeypatch.setattr(sql_runner, "sql_root", lambda: tmp_path)

    engine_a = create_async_engine("postgresql+asyncpg://user:pass@localhost/db_a")
    engine_b = create_async_engine("postgresql+asyncpg://user:pass@localhost/db_b")
    factory_a = async_sessionmaker(engine_a)
    calls = []

    async def fake_execute(target, statements, name, *, check_extensions) -> None:
        calls.append((target, statements, name, check_extensions))

    monkeypatch.setattr(sql_runner, "_execute", fake_execute)

    await sql_runner.apply_core_schema(factory_a)
    await sql_runner.apply_core_schema(engine_a)
    await sql_runner.apply_core_schema(engine_b)

    assert len(calls) == 2
    assert calls[0][0] is factory_a
    assert calls[1][0] is engine_b
    assert all(call[3] for call in calls)

    await engine_a.dispose()
    await engine_b.dispose()


def test_core_schema_contains_static_memory_objects() -> None:
    schema = (sql_runner.sql_root() / "init_postgres.sql").read_text(encoding="utf-8")
    memory_schema = schema[schema.index("-- ── 记忆") :]
    statements = sql_runner.split_sql(memory_schema)

    assert sum(
        stmt.startswith("CREATE TABLE") for stmt in sql_runner.split_sql(schema)
    ) == 19
    assert schema.count("VECTOR(1024)") == 3
    assert "VECTOR{" not in schema
    assert len(statements) == 25
    assert sum(stmt.startswith("CREATE TABLE") for stmt in statements) == 6
    assert sum(
        stmt.startswith(("CREATE INDEX", "CREATE UNIQUE INDEX"))
        for stmt in statements
    ) == 16
    assert sum(stmt.startswith("DO $$") for stmt in statements) == 3
    assert sum("USING hnsw" in stmt for stmt in statements) == 2


async def test_memory_backend_uses_core_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from prismclaw.persistence.memory import _schema_mixin
    from prismclaw.persistence.memory.postgres import PostgresMemoryBackend

    engine = create_async_engine("postgresql+asyncpg://user:pass@localhost/memory_db")
    calls = []

    async def fake_apply(target) -> None:
        calls.append(target)

    monkeypatch.setattr(_schema_mixin, "apply_core_schema", fake_apply)
    backend = PostgresMemoryBackend(engine)
    await backend._init_db()
    await backend._init_db()

    assert calls == [engine]
    await backend.close()


def test_medical_tables_leave_defaults_to_database() -> None:
    for table in medical_tables.metadata.tables.values():
        for column in table.c:
            assert column.default is None, f"{table.name}.{column.name}"

    for table in (
        medical_tables.kb_literatures,
        medical_tables.kb_guidelines,
        medical_tables.kb_drugs,
    ):
        assert isinstance(table.c.is_deleted.type, SmallInteger)
