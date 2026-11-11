"""Curator 后台启动服务 — 从 wiring/bundles/lifecycle.py 迁出"""
from __future__ import annotations

import logging
from functools import partial
from typing import Any

logger = logging.getLogger(__name__)


async def start_curator_if_enabled(
    *,
    app: Any,
    settings: Any,
    redis_client: Any,
    task_manager: Any,
    runner_deps: Any,
    lock_manager: Any,
    workspace_base: Any,
    memory_store: Any,
) -> None:
    """启动 curator 后台循环（如果配置启用且前置条件满足）"""
    if not settings.evolution.enabled or not settings.evolution.curator.enabled:
        return

    from prismclaw.engine.curator import create_curator_loop

    l1_index = getattr(memory_store, "_l1", None) if memory_store else None
    session_factory = getattr(app.state, "session_factory", None)
    if l1_index is None or redis_client is None or session_factory is None:
        if l1_index is not None:
            logger.warning("curator disabled: redis/db unavailable in fallback mode")
        return

    from prismclaw.persistence.curator.factory import create_curator_repository

    # inject graduate_single_sop as callback to avoid
    # persistence→engine layer violation
    graduator = None
    try:
        from prismclaw.engine.services.skill_graduation import graduate_single_sop
        from prismclaw.runtime.skills.discovery import discover_skills

        graduator = partial(
            graduate_single_sop,
            skill_discoverer=discover_skills,
            settings=settings,
        )
    except ImportError:
        logger.warning("curator: skill graduation unavailable (import error)")

    sop_repo = create_curator_repository(session_factory, graduator=graduator)

    # SDD D9：验证信号毕业路径——注入 memory store 的聚合函数
    # （attribution 关闭时 use_count 路径不生效，此路径补上毕业闭环）。
    count_validation_signals = (
        memory_store.count_validation_signals if memory_store is not None else None
    )

    task_manager.spawn(
        "curator-scan",
        create_curator_loop(
            settings=settings.evolution.curator,
            sop_repo=sop_repo,
            redis_client=redis_client,
            l1_index=l1_index,
            lock_manager=lock_manager,
            task_manager=task_manager,
            workspace_base_dir=workspace_base,
            llm_client=runner_deps.llm,
            count_validation_signals=count_validation_signals,
        ),
        category="curator",
    )
    logger.info("Curator background loop started")
