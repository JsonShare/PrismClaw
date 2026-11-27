from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import cast

import pytest
from redis.asyncio import Redis

from prismclaw.app.gateway.worker_registry import WorkerRegistry
from prismclaw.infra.config.module_release import (
    ModuleReleaseStore,
    ReleaseAckTimeoutError,
    ReleaseDocument,
    ReleaseValidationError,
    build_local_release_document,
    create_release_document,
    validate_local_packages,
)


@pytest.fixture
async def redis_context() -> AsyncIterator[tuple[Redis, str]]:
    url = os.environ.get("PRISMCLAW_TEST_REDIS_URL")
    if not url:
        pytest.skip("PRISMCLAW_TEST_REDIS_URL is required for real Redis integration")
    client = Redis.from_url(url, decode_responses=True)
    await client.ping()
    prefix = f"prismclaw:test:module-release:{uuid.uuid4().hex}"
    try:
        yield client, prefix
    finally:
        keys = await client.keys(f"{prefix}:*")
        if keys:
            await client.delete(*keys)
        await client.aclose()


class _EventReleaseStore(ModuleReleaseStore):
    def __init__(self, client: Redis, prefix: str) -> None:
        super().__init__(client, key_prefix=prefix)
        self.published = asyncio.Event()

    async def publish(self, document: ReleaseDocument) -> ReleaseDocument:
        published = await super().publish(document)
        self.published.set()
        return published


def test_release_document_rejects_tampered_checksum() -> None:
    document = create_release_document(
        version=1,
        packages={
            "expert-a": {"manifest_hash": "a" * 64, "shipped": True},
        },
        capability_policy={"defaults": {"expert": True}},
        metadata={"published_by": "test"},
    )
    raw = document.to_dict()
    raw["packages"]["expert-a"]["manifest_hash"] = "b" * 64

    with pytest.raises(ReleaseValidationError, match="RELEASE_CHECKSUM_MISMATCH"):
        ReleaseDocument.from_dict(raw)


class _StoredRedis:
    """只实现发布文档读取所需的 Redis 子集。"""

    def __init__(self, keys: dict[str, str]) -> None:
        self._keys = keys

    async def get(self, key: str) -> str | None:
        return self._keys.get(key)


def _stored_store(prefix: str, raw: dict[str, object]) -> ModuleReleaseStore:
    client = _StoredRedis(
        {
            f"{prefix}:current": str(raw["version"]),
            f"{prefix}:manifest:{raw['version']}": json.dumps(raw),
        }
    )
    return ModuleReleaseStore(cast(Redis, client), key_prefix=prefix)


async def test_current_document_from_older_protocol_is_treated_as_unpublished(
    caplog: pytest.LogCaptureFixture,
) -> None:
    prefix = "prismclaw:test:module-release:legacy"
    store = _stored_store(
        prefix,
        {
            "version": 3,
            "packages": {"expert-a": {"manifest_hash": "a" * 64, "shipped": True}},
            # 旧协议字段：改名前的 module_states
            "module_states": {},
            "metadata": {},
            "checksum": "b" * 64,
        },
    )

    with caplog.at_level(logging.WARNING):
        assert await store.fetch_current() is None
    assert "republish" in caplog.text


async def test_current_document_checksum_mismatch_still_fails() -> None:
    prefix = "prismclaw:test:module-release:tampered"
    document = create_release_document(
        version=1,
        packages={"expert-a": {"manifest_hash": "a" * 64, "shipped": True}},
        capability_policy={},
        metadata={},
    )
    raw = document.to_dict()
    raw["metadata"]["tampered"] = True
    store = _stored_store(prefix, raw)

    with pytest.raises(ReleaseValidationError, match="RELEASE_CHECKSUM_MISMATCH"):
        await store.fetch_current()


def test_shipped_packages_must_match_local_manifest_hash(tmp_path: Path) -> None:
    package = tmp_path / "expert-a"
    (package / ".plugin").mkdir(parents=True)
    (package / ".plugin/plugin.json").write_text('{"name":"expert-a"}\n', encoding="utf-8")
    (package / "SKILL.md").write_text("# A\n", encoding="utf-8")
    (package / ".ignored").mkdir()
    (package / ".ignored/secret").write_text("not packaged", encoding="utf-8")

    document = build_local_release_document(
        {"expert-a": package},
        capability_policy={"defaults": {"expert": True}},
        metadata={"published_by": "test"},
    )

    validate_local_packages(document, {"expert-a": package})
    (package / "SKILL.md").write_text("# changed\n", encoding="utf-8")
    with pytest.raises(ReleaseValidationError, match="RELEASE_PACKAGE_HASH_MISMATCH"):
        validate_local_packages(document, {"expert-a": package})


def test_unshipped_package_keeps_declared_hash_without_local_directory(tmp_path: Path) -> None:
    package = tmp_path / "expert-a"
    package.mkdir()
    (package / "SKILL.md").write_text("# A\n", encoding="utf-8")
    remote_hash = "c" * 64

    document = build_local_release_document(
        {"expert-a": package},
        package_manifest={
            "expert-a": {"shipped": True},
            "external-only": {"manifest_hash": remote_hash, "shipped": False},
        },
        capability_policy={},
        metadata={},
    )

    assert document.packages["external-only"].manifest_hash == remote_hash
    assert document.packages["external-only"].shipped is False
    validate_local_packages(document, {"expert-a": package})


@pytest.mark.integration
async def test_real_redis_publish_retains_versions_and_republishes_old_document(
    redis_context: tuple[Redis, str],
) -> None:
    client, prefix = redis_context
    store = ModuleReleaseStore(client, key_prefix=prefix)
    pubsub = client.pubsub()
    await pubsub.subscribe(store.channel)
    await pubsub.get_message(timeout=1.0)
    first = create_release_document(
        version=0,
        packages={"expert-a": {"manifest_hash": "a" * 64, "shipped": True}},
        capability_policy={"defaults": {"expert": True}},
        metadata={"published_by": "test"},
    )
    second = create_release_document(
        version=0,
        packages={"expert-a": {"manifest_hash": "b" * 64, "shipped": True}},
        capability_policy={"defaults": {"expert": False}},
        metadata={"published_by": "test"},
    )
    try:
        published_first = await store.publish(first)
        published_second = await store.publish(second)
        rolled_back = await store.publish(published_first)

        assert (published_first.version, published_second.version, rolled_back.version) == (1, 2, 3)
        assert await store.fetch_current() == rolled_back
        assert await store.fetch_version(1) == published_first
        assert await store.fetch_version(2) == published_second
        assert rolled_back.to_dict()["packages"] == published_first.to_dict()["packages"]
        messages = [
            await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            for _ in range(3)
        ]
        assert [message["data"] for message in messages if message is not None] == ["1", "2", "3"]
    finally:
        await cast(Callable[[], Awaitable[None]], pubsub.aclose)()


@pytest.mark.integration
async def test_real_redis_publish_waits_for_every_healthy_worker_ack(
    redis_context: tuple[Redis, str],
) -> None:
    client, prefix = redis_context
    store = _EventReleaseStore(client, prefix)
    workers_key = f"{prefix}:workers"
    worker_a = WorkerRegistry(client, "worker-a", key=workers_key)
    worker_b = WorkerRegistry(client, "worker-b", key=workers_key)
    await worker_a.register()
    await worker_b.register()
    document = create_release_document(
        version=0,
        packages={"expert-a": {"manifest_hash": "a" * 64, "shipped": True}},
        capability_policy={},
        metadata={},
    )

    pending = asyncio.create_task(
        store.publish_and_wait(document, worker_a, ack_timeout_seconds=0.15, poll_interval=0.01)
    )
    await asyncio.wait_for(store.published.wait(), timeout=1.0)
    await store.ack(1, "worker-a")
    with pytest.raises(ReleaseAckTimeoutError) as caught:
        await pending
    assert caught.value.missing == {"worker-b"}

    store.published.clear()
    pending = asyncio.create_task(
        store.publish_and_wait(document, worker_a, ack_timeout_seconds=1.0, poll_interval=0.01)
    )
    await asyncio.wait_for(store.published.wait(), timeout=1.0)
    await store.ack(2, "worker-a")
    await store.ack(2, "worker-b")
    published = await pending
    assert published.version == 2
    assert await client.ttl(f"{prefix}:ack:2") > 0
