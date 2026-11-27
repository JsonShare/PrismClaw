"""多副本模块发布文档及其不可变校验。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from redis.asyncio.client import PubSub

logger = logging.getLogger(__name__)

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_SKIPPED_DIRS = frozenset({"__pycache__", "node_modules"})
_SKIPPED_FILES = frozenset({".gitkeep", ".DS_Store", "Thumbs.db"})


class ReleaseValidationError(ValueError):
    """发布文档结构、内容 hash 或 checksum 不合法。"""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


class ReleaseAckTimeoutError(TimeoutError):
    code = "RELEASE_ACK_TIMEOUT"

    def __init__(self, version: int, expected: set[str], missing: set[str]) -> None:
        self.version = version
        self.expected = expected
        self.missing = missing
        super().__init__(
            f"{self.code}: version {version} missing acknowledgements from {sorted(missing)}"
        )


class WorkerCatalog(Protocol):
    async def active_workers(self, stale_threshold: float = 90.0) -> list[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class PackageRelease:
    manifest_hash: str
    shipped: bool = True


@dataclass(frozen=True, slots=True)
class ReleaseDocument:
    version: int
    packages: Mapping[str, PackageRelease]
    capability_policy: Mapping[str, Any]
    metadata: Mapping[str, Any]
    checksum: str

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ReleaseDocument:
        if not isinstance(raw, Mapping):
            raise ReleaseValidationError("RELEASE_SCHEMA_INVALID", "expected an object")
        version = raw.get("version")
        if type(version) is not int or version < 0:
            raise ReleaseValidationError(
                "RELEASE_SCHEMA_INVALID", "version must be a non-negative integer"
            )
        packages = _parse_packages(raw.get("packages"))
        capability_policy = _json_object(raw.get("capability_policy"), "capability_policy")
        metadata = _json_object(raw.get("metadata"), "metadata")
        checksum = raw.get("checksum")
        if not isinstance(checksum, str) or not _SHA256_RE.fullmatch(checksum):
            raise ReleaseValidationError(
                "RELEASE_SCHEMA_INVALID", "checksum must be a lowercase SHA-256 hex digest"
            )
        expected = _content_checksum(packages, capability_policy, metadata)
        if checksum != expected:
            raise ReleaseValidationError(
                "RELEASE_CHECKSUM_MISMATCH", "document content does not match its checksum"
            )
        return cls(
            version=version,
            packages=MappingProxyType(packages),
            capability_policy=MappingProxyType(capability_policy),
            metadata=MappingProxyType(metadata),
            checksum=checksum,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "packages": {
                package_id: {
                    "manifest_hash": package.manifest_hash,
                    "shipped": package.shipped,
                }
                for package_id, package in sorted(self.packages.items())
            },
            "capability_policy": _json_copy(self.capability_policy),
            "metadata": _json_copy(self.metadata),
            "checksum": self.checksum,
        }


def create_release_document(
    *,
    version: int,
    packages: Mapping[str, PackageRelease | Mapping[str, Any]],
    capability_policy: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> ReleaseDocument:
    raw_packages = {
        package_id: (
            {
                "manifest_hash": package.manifest_hash,
                "shipped": package.shipped,
            }
            if isinstance(package, PackageRelease)
            else dict(package)
        )
        for package_id, package in packages.items()
    }
    checksum = _content_checksum(
        _parse_packages(raw_packages),
        _json_object(capability_policy, "capability_policy"),
        _json_object(metadata, "metadata"),
    )
    return ReleaseDocument.from_dict(
        {
            "version": version,
            "packages": raw_packages,
            "capability_policy": capability_policy,
            "metadata": metadata,
            "checksum": checksum,
        }
    )


class ModuleReleaseStore:
    """Redis 中的版本化发布文档；只协调文档，不传输或重建包体。"""

    _PUBLISH_LUA = """
local next_version = redis.call('INCR', KEYS[1])
local document = cjson.decode(ARGV[1])
document['version'] = next_version
local encoded = cjson.encode(document)
redis.call('SET', KEYS[2] .. next_version, encoded)
redis.call('SET', KEYS[3], next_version)
redis.call('PUBLISH', ARGV[2], tostring(next_version))
return {next_version, encoded}
"""

    def __init__(self, redis_client: Redis, *, key_prefix: str = "prismclaw:modules") -> None:
        self._redis = redis_client
        self._prefix = key_prefix.rstrip(":")
        self._version_key = f"{self._prefix}:version"
        self._current_key = f"{self._prefix}:current"
        self._manifest_prefix = f"{self._prefix}:manifest:"
        self.channel = f"{self._prefix}:invalidate"

    def pubsub(self) -> PubSub:
        return self._redis.pubsub()

    async def publish(self, document: ReleaseDocument) -> ReleaseDocument:
        encoded = json.dumps(document.to_dict(), ensure_ascii=False, allow_nan=False)
        result = await self._redis.eval(
            self._PUBLISH_LUA,
            3,
            self._version_key,
            self._manifest_prefix,
            self._current_key,
            encoded,
            self.channel,
        )
        raw_document = result[1]
        if isinstance(raw_document, bytes):
            raw_document = raw_document.decode("utf-8")
        return ReleaseDocument.from_dict(json.loads(raw_document))

    async def publish_and_wait(
        self,
        document: ReleaseDocument,
        workers: WorkerCatalog,
        *,
        ack_timeout_seconds: float = 30.0,
        poll_interval: float = 0.2,
    ) -> ReleaseDocument:
        active = await workers.active_workers()
        expected = {
            str(worker["id"]) for worker in active if worker.get("status") == "healthy"
        }
        published = await self.publish(document)
        await self.wait_for_acks(
            published.version,
            expected,
            ack_timeout_seconds=ack_timeout_seconds,
            poll_interval=poll_interval,
        )
        return published

    async def ack(self, version: int, worker_id: str, *, ttl_seconds: int = 90) -> None:
        key = f"{self._prefix}:ack:{version}"
        pipe = self._redis.pipeline()
        pipe.hset(key, worker_id, str(time.time()))
        pipe.expire(key, ttl_seconds)
        await pipe.execute()

    async def wait_for_acks(
        self,
        version: int,
        expected: set[str],
        *,
        ack_timeout_seconds: float,
        poll_interval: float,
    ) -> None:
        deadline = time.monotonic() + ack_timeout_seconds
        key = f"{self._prefix}:ack:{version}"
        while True:
            raw_acks = await self._redis.hgetall(key)
            acked = {
                (worker_id.decode("utf-8") if isinstance(worker_id, bytes) else str(worker_id))
                for worker_id in raw_acks
            }
            missing = expected - acked
            if expected and not missing:
                return
            if time.monotonic() >= deadline:
                raise ReleaseAckTimeoutError(version, expected, missing)
            await asyncio.sleep(poll_interval)

    async def fetch_current(self) -> ReleaseDocument | None:
        raw_version = await self._redis.get(self._current_key)
        if raw_version is None:
            return None
        if isinstance(raw_version, bytes):
            raw_version = raw_version.decode("ascii")
        version = int(raw_version)
        try:
            return await self.fetch_version(version)
        except ReleaseValidationError as exc:
            # 协议升级（如 module_states → capability_policy）后旧文档不再可读。
            # 不保留旧字段兼容分支：按「未发布」处理，控制面随后会因缺发布文档
            # 保持 not_ready，直到用当前发布工具重新发布一次。
            if exc.code != "RELEASE_SCHEMA_INVALID":
                raise
            logger.warning(
                "current module release v%s is not readable by this protocol (%s); "
                "republish with the current release tooling",
                version,
                exc,
            )
            return None

    async def fetch_version(self, version: int) -> ReleaseDocument | None:
        raw = await self._redis.get(f"{self._manifest_prefix}{version}")
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return ReleaseDocument.from_dict(json.loads(raw))


def build_local_release_document(
    package_roots: Mapping[str, str | Path],
    *,
    package_manifest: Mapping[str, Mapping[str, Any]] | None = None,
    capability_policy: Mapping[str, Any],
    metadata: Mapping[str, Any],
    version: int = 0,
) -> ReleaseDocument:
    entries = package_manifest or {package_id: {} for package_id in package_roots}
    if not set(package_roots).issubset(entries):
        raise ReleaseValidationError(
            "RELEASE_PACKAGE_SET_MISMATCH", "local package is absent from package_manifest"
        )
    packages: dict[str, PackageRelease | Mapping[str, Any]] = {}
    for package_id, entry in entries.items():
        shipped = entry.get("shipped", True)
        if shipped:
            if package_id not in package_roots:
                raise ReleaseValidationError(
                    "RELEASE_PACKAGE_MISSING", f"no local directory for {package_id}"
                )
            packages[package_id] = {
                "manifest_hash": compute_manifest_hash(package_roots[package_id]),
                "shipped": True,
            }
        else:
            packages[package_id] = {
                "manifest_hash": entry.get("manifest_hash"),
                "shipped": False,
            }
    return create_release_document(
        version=version,
        packages=packages,
        capability_policy=capability_policy,
        metadata=metadata,
    )


def validate_local_packages(
    document: ReleaseDocument,
    package_roots: Mapping[str, str | Path],
) -> None:
    shipped_ids = {
        package_id for package_id, package in document.packages.items() if package.shipped
    }
    if shipped_ids != set(package_roots):
        raise ReleaseValidationError(
            "RELEASE_PACKAGE_SET_MISMATCH",
            f"local package IDs {sorted(package_roots)} do not match shipped manifest "
            f"{sorted(shipped_ids)}",
        )
    for package_id in sorted(shipped_ids):
        actual = compute_manifest_hash(package_roots[package_id])
        expected = document.packages[package_id].manifest_hash
        if actual != expected:
            raise ReleaseValidationError(
                "RELEASE_PACKAGE_HASH_MISMATCH",
                f"package {package_id} local hash {actual} does not match {expected}",
            )


def compute_manifest_hash(package_path: str | Path) -> str:
    package_root = Path(package_path).expanduser().resolve()
    if not package_root.is_dir():
        raise ReleaseValidationError(
            "RELEASE_PACKAGE_MISSING", f"package directory does not exist: {package_root}"
        )
    digest = hashlib.sha256()
    for file_path in sorted(package_root.rglob("*"), key=lambda path: path.as_posix()):
        relative = file_path.relative_to(package_root)
        if file_path.is_symlink():
            raise ReleaseValidationError(
                "RELEASE_SCHEMA_INVALID", f"symbolic links are not supported: {relative}"
            )
        if not file_path.is_file():
            continue
        if any(part.startswith(".") and part != ".plugin" for part in relative.parts):
            continue
        if any(part in _SKIPPED_DIRS for part in relative.parts):
            continue
        if file_path.name in _SKIPPED_FILES:
            continue
        content = file_path.read_bytes()
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _parse_packages(raw: object) -> dict[str, PackageRelease]:
    if not isinstance(raw, Mapping) or not raw:
        raise ReleaseValidationError(
            "RELEASE_SCHEMA_INVALID", "packages must be a non-empty object"
        )
    result: dict[str, PackageRelease] = {}
    for package_id, value in raw.items():
        if not isinstance(package_id, str) or not package_id:
            raise ReleaseValidationError(
                "RELEASE_SCHEMA_INVALID", "package IDs must be non-empty strings"
            )
        if not isinstance(value, Mapping):
            raise ReleaseValidationError(
                "RELEASE_SCHEMA_INVALID", f"package {package_id} must be an object"
            )
        manifest_hash = value.get("manifest_hash")
        shipped = value.get("shipped")
        if not isinstance(manifest_hash, str) or not _SHA256_RE.fullmatch(manifest_hash):
            raise ReleaseValidationError(
                "RELEASE_SCHEMA_INVALID",
                f"package {package_id} manifest_hash must be a lowercase SHA-256 digest",
            )
        if type(shipped) is not bool:
            raise ReleaseValidationError(
                "RELEASE_SCHEMA_INVALID", f"package {package_id} shipped must be a boolean"
            )
        result[package_id] = PackageRelease(manifest_hash, shipped)
    return result


def _json_object(raw: object, field: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ReleaseValidationError("RELEASE_SCHEMA_INVALID", f"{field} must be an object")
    try:
        copied = _json_copy(raw)
    except (TypeError, ValueError) as exc:
        raise ReleaseValidationError(
            "RELEASE_SCHEMA_INVALID", f"{field} must contain JSON values"
        ) from exc
    if not isinstance(copied, dict):
        raise ReleaseValidationError("RELEASE_SCHEMA_INVALID", f"{field} must be an object")
    return copied


def _json_copy(raw: object) -> object:
    value = dict(raw) if isinstance(raw, Mapping) else raw
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _content_checksum(
    packages: Mapping[str, PackageRelease],
    capability_policy: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> str:
    # Redis Lua 原子分配 version；checksum 只覆盖发布 payload，使旧文档可作为
    # 更高 version 原样重发，同时仍能检测包、模块状态和发布元数据篡改。
    payload = {
        "packages": {
            package_id: {
                "manifest_hash": package.manifest_hash,
                "shipped": package.shipped,
            }
            for package_id, package in sorted(packages.items())
        },
        "capability_policy": capability_policy,
        "metadata": metadata,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
