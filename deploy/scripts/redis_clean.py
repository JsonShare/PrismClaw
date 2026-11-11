"""Clean stale affinity keys + legacy-format worker entries from ZSET."""
from __future__ import annotations

import asyncio

from prismclaw.infra.redis import close_client, get_client
from prismclaw.infra.settings import load_settings


async def main() -> None:
    settings = load_settings()
    client = await get_client(settings.redis)
    try:
        cursor = 0
        affinity_keys = []
        while True:
            cursor, batch = await client.scan(cursor=cursor, count=100, match="prismclaw:affinity:*")
            affinity_keys.extend(batch)
            if cursor == 0:
                break
        if affinity_keys:
            await client.delete(*affinity_keys)
        print(f"  deleted {len(affinity_keys)} affinity keys")

        members = await client.zrange("prismclaw:workers", 0, -1)
        legacy = []
        for m in members:
            m_str = m.decode() if isinstance(m, bytes) else m
            if not m_str.startswith("worker:"):
                legacy.append(m_str)
        if legacy:
            await client.zrem("prismclaw:workers", *legacy)
        print(f"  removed {len(legacy)} legacy worker entries from ZSET")
    finally:
        await close_client(client)


if __name__ == "__main__":
    asyncio.run(main())
