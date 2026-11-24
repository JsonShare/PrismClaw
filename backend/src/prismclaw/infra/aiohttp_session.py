"""取消安全的 aiohttp 会话开闭。

日志症状：``asyncio:__del__ | Unclosed client session``。
根因：``async with aiohttp.ClientSession()`` 在任务被取消时，``__aexit__``
里的 ``await close()`` 也会被同一次取消打断，连接池未释放，对象被 GC 时
aiohttp 打出上述 ERROR。

本上下文管理器在退出时用 ``shield`` 等待 ``close()`` 完成；若当前任务已被
取消，则吞掉该次取消信号（asyncio 只投递一次）并继续等待关闭，保证会话
不会以「无主资源」形态泄漏。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiohttp


@asynccontextmanager
async def open_client_session(
    *, connector: aiohttp.BaseConnector | None = None
) -> AsyncIterator[aiohttp.ClientSession]:
    import aiohttp

    session = aiohttp.ClientSession(connector=connector)
    try:
        yield session
    finally:
        close_task = asyncio.ensure_future(session.close())
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await close_task
