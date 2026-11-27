from __future__ import annotations

import asyncio
import logging
import re
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

logger = logging.getLogger(__name__)

REQUIRED_EXTENSIONS = ("vchord", "pg_tokenizer", "vchord_bm25", "vector")

SchemaTarget = AsyncEngine | async_sessionmaker[AsyncSession]

#: 域自建 SQL 脚本目录（相对域包根），启动时自动扫描并按文件名升序执行。
DOMAIN_SQL_SUBDIR = Path("deploy") / "sql"

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
            escaped = (
                ch == "'"
                and i > 0
                and sql[i - 1] in "Ee"
                and (i < 2 or not (sql[i - 2].isalnum() or sql[i - 2] == "_"))
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


async def _ensure_extensions(executor: AsyncConnection | AsyncSession) -> None:
    result = await executor.execute(text("SELECT extname FROM pg_extension"))
    installed = {row[0] for row in result}
    missing = [name for name in REQUIRED_EXTENSIONS if name not in installed]
    if missing:
        commands = "\n".join(f"CREATE EXTENSION IF NOT EXISTS {name} CASCADE;" for name in missing)
        raise RuntimeError(f"PostgreSQL 缺少必需扩展：{', '.join(missing)}。请先执行：\n{commands}")


def _file_state(path: Path) -> tuple[str, bool]:
    return str(path.resolve()), path.is_file()


@asynccontextmanager
async def _connection(target: SchemaTarget) -> AsyncIterator[AsyncConnection | AsyncSession]:
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


def domain_sql_dir(bundle: object) -> Path | None:
    """定位域包内的 ``deploy/sql`` 目录。

    约定：``subpackages/<域>/deploy/sql/*.sql``。从 Bundle 类的模块文件
    向上最多 4 层查找，兼容 ``integration/bundle.py`` 与扁平 ``bundle.py``。
    找不到返回 ``None``（该域没有自建业务表）。
    """
    module = sys.modules.get(type(bundle).__module__)
    module_file = getattr(module, "__file__", None)
    if not module_file:
        return None

    current = Path(module_file).resolve().parent
    for _ in range(4):
        candidate = current / DOMAIN_SQL_SUBDIR
        if candidate.is_dir():
            return candidate
        if current == current.parent:
            break
        current = current.parent
    return None


async def apply_domain_sql(target: SchemaTarget, bundle: object) -> int:
    """启动时扫描并执行域自建 SQL 脚本，返回执行的文件数。

    - 文件名升序执行，建议使用 ``001_``、``002_`` 之类的前缀固定顺序。
    - 每个文件独立持有 advisory lock，语句须为 ``IF NOT EXISTS`` 等幂等写法。
    - 域没有 ``deploy/sql`` 目录视为「无业务表」，跳过且不报错。
    """
    name = getattr(bundle, "name", "unknown")
    sql_dir = domain_sql_dir(bundle)
    if sql_dir is None:
        logger.debug("域 %s 未提供 %s，跳过 SQL 扫描", name, DOMAIN_SQL_SUBDIR)
        return 0

    files = sorted(path for path in sql_dir.glob("*.sql") if path.is_file())
    for path in files:
        await _apply_file(target, path, required=False)
    logger.info("域 %s SQL 扫描完成：%s（%d 个文件）", name, sql_dir, len(files))
    return len(files)
