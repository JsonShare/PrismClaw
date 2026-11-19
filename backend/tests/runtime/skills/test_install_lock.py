"""技能安装锁：并发安装不得互相破坏目录或 skills-lock.json。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from prismclaw.runtime.skills.installer import _install_lock


async def test_install_lock_serializes_writers(tmp_path: Path) -> None:
    order: list[str] = []

    async def worker(tag: str) -> None:
        async with _install_lock(tmp_path):
            order.append(f"{tag}-enter")
            await asyncio.sleep(0.02)
            order.append(f"{tag}-exit")

    await asyncio.gather(worker("a"), worker("b"))

    assert order in (
        ["a-enter", "a-exit", "b-enter", "b-exit"],
        ["b-enter", "b-exit", "a-enter", "a-exit"],
    ), order


async def test_install_lock_released_after_exception(tmp_path: Path) -> None:
    """异常路径必须释放锁，否则后续安装会永久阻塞。"""

    async def boom() -> None:
        async with _install_lock(tmp_path):
            raise RuntimeError("boom")

    try:
        await boom()
    except RuntimeError:
        pass

    async with asyncio.timeout(2):
        async with _install_lock(tmp_path):
            pass
