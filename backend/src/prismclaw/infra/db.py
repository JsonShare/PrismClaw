"""Shared PostgreSQL async engine factory.

All subsystems (user store, memory store, scheduler, dream, curator, context
audit) share a single AsyncEngine instance to avoid connection-pool
fragmentation across workers.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from prismclaw.infra.settings import StorageSettings


def create_shared_engine(settings: StorageSettings) -> AsyncEngine:
    """Create the application-wide shared AsyncEngine.

    Pool sizing: 10 base + 10 overflow = 20 max per worker.
    3 workers × 20 = 60 connections (Postgres default max_connections=100).
    """
    dsn = settings.postgres_dsn
    # 当配置文件使用 {env:...} 占位符但环境变量未设置时，
    # _resolve_env_placeholders 会返回空字符串；此时 fallback 到默认值，
    # 避免空字符串传给 SQLAlchemy 触发 URL 解析错误。
    if not dsn:
        dsn = StorageSettings.model_fields["postgres_dsn"].default
    return create_async_engine(
        dsn,
        pool_size=10,
        max_overflow=10,
        pool_pre_ping=True,
        future=True,
    )
