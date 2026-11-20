from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

REQUIRED_EXTENSIONS = ("vchord", "pg_tokenizer", "vchord_bm25", "vector")

SchemaTarget = AsyncEngine | async_sessionmaker[AsyncSession]

_DOLLAR_TAG = re.compile(r"\$[A-Za-z0-9_]*\$")
_APPLIED: set[tuple[str, str]] = set()
_APPLY_LOCK = asyncio.Lock()


def _target_engine(target: SchemaTarget) -> AsyncEngine:
    if isinstance(target, AsyncEngine):
        return target
    engine = target.kw.get("bind")
    if not isinstance(engine, AsyncEngine):
        raise TypeError("schema session factory 必须绑定 AsyncEngine")
    return engine


def sql_root() -> Path:
    from prismclaw.infra.settings._helpers import _repo_root_from_settings_module

    repo_root = _repo_root_from_settings_module()
    if repo_root is None:
        raise RuntimeError("无法确定仓库根目录")
    return repo_root / "deploy" / "sql"


def split_sql(sql: str) -> list[str]:
    """按分号切分语句，跳过注释并保留 ``$$`` 函数体/字符串/标识符内容。"""
    statements: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)

    while i < n:
        ch = sql[i]

        if ch == "-" and sql.startswith("--", i):
            newline = sql.find("\n", i)
            i = n if newline < 0 else newline + 1
            continue

        if ch == "/" and sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            if end < 0:
                raise ValueError("未闭合的块注释")
            i = end + 2
            continue

        if ch == "$":
            match = _DOLLAR_TAG.match(sql, i)
            if match:
                tag = match.group(0)
                end = sql.find(tag, match.end())
                if end < 0:
                    raise ValueError(f"未闭合的美元引用：{tag}")
                end += len(tag)
                buf.append(sql[i:end])
                i = end
                continue

        if ch in ("'", '"'):
            escaped = ch == "'" and i > 0 and sql[i - 1] in "Ee" and (
                i < 2 or not (sql[i - 2].isalnum() or sql[i - 2] == "_")
            )
            j = i + 1
            closed = False
            while j < n:
                if escaped and sql[j] == "\\":
                    j += 2
                    continue
                if sql[j] == ch:
                    if j + 1 < n and sql[j + 1] == ch:
                        j += 2
                        continue
                    j += 1
                    closed = True
                    break
                j += 1
            if not closed:
                raise ValueError(f"未闭合的引号：{ch}")
            buf.append(sql[i:j])
            i = j
            continue

        if ch == ";":
            statement = "".join(buf).strip()
            if statement:
                statements.append(statement)
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


async def _ensure_extensions(executor: Any) -> None:
    result = await executor.execute(text("SELECT extname FROM pg_extension"))
    installed = {row[0] for row in result}
    missing = [name for name in REQUIRED_EXTENSIONS if name not in installed]
    if missing:
        commands = "\n".join(f"CREATE EXTENSION IF NOT EXISTS {name} CASCADE;" for name in missing)
        raise RuntimeError(
            f"PostgreSQL 缺少必需扩展：{', '.join(missing)}。请先执行：\n{commands}"
        )


def _file_state(path: Path) -> tuple[str, bool]:
    return str(path.resolve()), path.is_file()


@asynccontextmanager
async def _connection(target: SchemaTarget) -> AsyncIterator[Any]:
    if isinstance(target, AsyncEngine):
        async with target.begin() as conn:
            yield conn
    else:
        async with target.begin() as session:
            yield session


async def _execute(
    target: SchemaTarget,
    statements: list[str],
    name: str,
    *,
    check_extensions: bool,
) -> None:
    async with _connection(target) as executor:
        await executor.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"prismclaw:schema:{name}"},
        )
        if check_extensions:
            await _ensure_extensions(executor)
        for index, statement in enumerate(statements, start=1):
            if re.match(r"(?is)^create\s+extension\b", statement):
                continue
            try:
                await executor.execute(text(statement))
            except Exception as exc:
                raise RuntimeError(f"{name} 第 {index} 条语句执行失败：{exc}") from exc


async def _apply_file(
    target: SchemaTarget,
    path: Path,
    *,
    required: bool,
    check_extensions: bool = False,
) -> None:
    path_key, exists = await asyncio.to_thread(_file_state, path)
    if not exists:
        if required:
            raise FileNotFoundError(f"缺少 schema 文件：{path}")
        return

    database_key = _target_engine(target).url.render_as_string(hide_password=True)
    cache_key = (database_key, path_key)
    if cache_key in _APPLIED:
        return

    async with _APPLY_LOCK:
        if cache_key in _APPLIED:
            return
        statements = split_sql(await asyncio.to_thread(path.read_text, "utf-8"))
        await _execute(
            target,
            statements,
            path.name,
            check_extensions=check_extensions,
        )
        _APPLIED.add(cache_key)
        logger.info("已应用 schema %s（%d 条语句）", path, len(statements))


async def apply_core_schema(target: SchemaTarget) -> None:
    await _apply_file(
        target,
        sql_root() / "init_postgres.sql",
        required=True,
        check_extensions=True,
    )


async def apply_domain_schema(target: SchemaTarget, domain_key: str) -> None:
    path = sql_root() / f"init_{domain_key}.sql"
    if not path.is_file():
        logger.error(
            "域 %s 已激活，但缺少初始化脚本：%s\n"
            "请补齐该文件，或在 domains.yaml 将 %s.enabled 设为 false / 使用 "
            "PRISMCLAW_DOMAINS 裁剪。\n（仅含注释的空文件是允许的）",
            domain_key,
            path,
            domain_key,
        )
        return
    await _apply_file(target, path, required=False)
