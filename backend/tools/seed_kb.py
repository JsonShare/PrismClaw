"""把医疗知识库种子数据（文献 / 药品）灌入数据库。

背景：`seed_kb_data()` 定义了但没有任何调用点，导致文献与药品目录在重启后为空。
本脚本直接复用该函数，可反复执行（内部为幂等 upsert + 旧种子清理）。

指南不在此脚本：指南由 `backend/tools/ingest_guidelines.py` 解析真实 PDF 导入。

用法：
    python backend/tools/seed_kb.py
    python backend/tools/seed_kb.py --only literatures
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

DEFAULT_DSN = "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/prismclaw"

TABLES = ("literatures", "drugs")


def async_dsn(dsn: str) -> str:
    """统一成 asyncpg 驱动（同步 DSN 会去找 psycopg2，本环境未安装）。"""
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn
    return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)


async def run(dsn: str, only: set[str]) -> None:
    from subpackages.medical.models.tables import kb_drugs, kb_literatures
    from subpackages.medical.persistence.kb_store import KbStore
    from subpackages.medical.persistence.schema import ensure_medical_schema
    from subpackages.medical.persistence.seeds.kb import (
        _DRUG_SEEDS,
        _LITERATURE_SEEDS,
        _prune_stale_seed_rows,
        seed_kb_data,
    )

    engine = create_async_engine(async_dsn(dsn), pool_pre_ping=True, future=True)
    try:
        await ensure_medical_schema(engine)

        if only == set(TABLES):
            await seed_kb_data(engine)
            print(f"全量灌入完成：文献 {len(_LITERATURE_SEEDS)} / 药品 {len(_DRUG_SEEDS)}")
            return

        store = KbStore(engine)
        if "literatures" in only:
            for row in _LITERATURE_SEEDS:
                await store.upsert_literature(row)
            await _prune_stale_seed_rows(
                engine, kb_literatures, [r["id"] for r in _LITERATURE_SEEDS]
            )
            print(f"文献：{len(_LITERATURE_SEEDS)} 条")
        if "drugs" in only:
            for row in _DRUG_SEEDS:
                await store.upsert_drug(row)
            await _prune_stale_seed_rows(engine, kb_drugs, [r["id"] for r in _DRUG_SEEDS])
            print(f"药品：{len(_DRUG_SEEDS)} 条")
    finally:
        await engine.dispose()


def main() -> int:
    ap = argparse.ArgumentParser(description="医疗知识库种子灌入（文献 / 药品）")
    ap.add_argument("--dsn", default=os.environ.get("PRISMCLAW_PG_DSN", DEFAULT_DSN))
    ap.add_argument("--only", nargs="+", default=list(TABLES), choices=list(TABLES))
    args = ap.parse_args()
    asyncio.run(run(args.dsn, set(args.only)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
