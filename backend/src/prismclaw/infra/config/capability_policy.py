"""专家、专家团与技能的部署级启停策略（capability_policy.yaml）。"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Protocol, runtime_checkable

from prismclaw.infra.config_io import load_document

type CapabilityKind = Literal["expert", "team", "skill"]
type CapabilityPolicySource = Literal["env", "override", "bulk", "default", "strict", "missing"]

CAPABILITY_KINDS = frozenset(("expert", "team", "skill"))
_TOP_LEVEL_KEYS = frozenset(("version", "strict", "defaults", "bulk", "overrides"))

logger = logging.getLogger(__name__)


@runtime_checkable
class CapabilityCatalog(Protocol):
    """由装配层注入的已知模块目录；ExpertRegistry 实现该接口。"""

    def known_ids(self, kind: str) -> Iterable[str]: ...


@dataclass(frozen=True, slots=True)
class CapabilityPolicyEntry:
    kind: str
    module_id: str
    enabled: bool
    reason: str | None
    source: CapabilityPolicySource


@dataclass(frozen=True, slots=True)
class CapabilityPolicyIssue:
    path: str
    message: str
    code: str | None = None


@dataclass(frozen=True, slots=True)
class CapabilityPolicySnapshot:
    version: int
    states: Mapping[tuple[str, str], CapabilityPolicyEntry]
    issues: tuple[CapabilityPolicyIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class CapabilityPolicyCandidate:
    snapshot: CapabilityPolicySnapshot
    catalog: CapabilityCatalog


@dataclass(frozen=True, slots=True)
class _Override:
    enabled: bool
    reason: str | None


class CapabilityPolicyLoadError(RuntimeError):
    """能力策略文件存在但无法读取或解析；冷启动必须向上抛出。"""

    code = "CAPABILITY_POLICY_LOAD_FAILED"

    def __init__(self, path: Path, detail: str) -> None:
        self.path = path
        super().__init__(f"{self.code}: cannot load capability policy file {path}: {detail}")


class CapabilityPolicyRegistry:
    """只读、线程安全的能力策略注册表；通过 mtime/size 在请求边界热加载。"""

    def __init__(
        self,
        path: str | Path,
        *,
        catalog: CapabilityCatalog,
        reload_interval_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if reload_interval_seconds < 0:
            raise ValueError("reload_interval_seconds must be non-negative")
        self._path = Path(path)
        self._catalog = catalog
        self._reload_interval_seconds = reload_interval_seconds
        self._clock = clock
        self._lock = threading.RLock()
        self._file_stamp = self._read_file_stamp()
        self._snapshot = self._load_snapshot(version=1, file_stamp=self._file_stamp)
        self._last_reload_check = self._clock()
        self._log_issues(self._snapshot.issues)

    def is_enabled(self, kind: str, module_id: str) -> bool:
        return self.state_of(kind, module_id).enabled

    def state_of(self, kind: str, module_id: str) -> CapabilityPolicyEntry:
        return self._state_from_snapshot(self.snapshot(), kind, module_id)

    def filter_enabled(self, kind: str, ids: Iterable[str]) -> list[str]:
        snapshot = self.snapshot()
        return [
            module_id
            for module_id in ids
            if self._state_from_snapshot(snapshot, kind, module_id).enabled
        ]

    def snapshot(self) -> CapabilityPolicySnapshot:
        self.reload_if_changed()
        with self._lock:
            return self._snapshot

    def build_candidate(
        self,
        document: Mapping[str, object],
        *,
        version: int,
        catalog: CapabilityCatalog | None = None,
    ) -> CapabilityPolicyCandidate:
        """构建远程候选，不改变当前目录、文件戳或已发布快照。"""
        candidate_catalog = catalog or self._catalog
        snapshot = self._build_snapshot(
            dict(document), version, catalog=candidate_catalog
        )
        return CapabilityPolicyCandidate(snapshot=snapshot, catalog=candidate_catalog)

    def commit(self, candidate: CapabilityPolicyCandidate) -> None:
        """同步提交已由调用方完整验证的候选；此段没有异步等待。"""
        with self._lock:
            self._snapshot = candidate.snapshot
            self._catalog = candidate.catalog
            self._last_reload_check = self._clock()
        self._log_issues(candidate.snapshot.issues)

    def reload_if_changed(self) -> bool:
        now = self._clock()
        with self._lock:
            if now - self._last_reload_check < self._reload_interval_seconds:
                return False
            self._last_reload_check = now
            try:
                file_stamp = self._read_file_stamp()
                if file_stamp == self._file_stamp:
                    return False
                candidate = self._load_snapshot(
                    version=self._snapshot.version + 1,
                    file_stamp=file_stamp,
                )
            except CapabilityPolicyLoadError as exc:
                # 失败的文件版本只检查一次；修复文件改变 mtime/size 后再试。
                if "file_stamp" in locals():
                    self._file_stamp = file_stamp
                logger.warning("capability policy reload failed; keeping snapshot: %s", exc)
                return False
            self._snapshot = candidate
            self._file_stamp = file_stamp
        self._log_issues(candidate.issues)
        return True

    def _read_file_stamp(self) -> tuple[int, int] | None:
        try:
            stat = self._path.stat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise CapabilityPolicyLoadError(self._path, str(exc)) from exc
        return stat.st_mtime_ns, stat.st_size

    def _load_snapshot(
        self,
        *,
        version: int,
        file_stamp: tuple[int, int] | None,
    ) -> CapabilityPolicySnapshot:
        if file_stamp is None:
            document: dict[str, object] = {}
        else:
            try:
                loaded = load_document(self._path)
            except Exception as exc:
                raise CapabilityPolicyLoadError(self._path, str(exc)) from exc
            if loaded is None:
                raise CapabilityPolicyLoadError(self._path, "invalid YAML or non-object document")
            document = dict(loaded)
        return self._build_snapshot(document, version)

    def _build_snapshot(
        self,
        document: dict[str, object],
        version: int,
        *,
        catalog: CapabilityCatalog | None = None,
    ) -> CapabilityPolicySnapshot:
        issues: list[CapabilityPolicyIssue] = []
        for key in document:
            if key not in _TOP_LEVEL_KEYS:
                issues.append(
                    CapabilityPolicyIssue(path=str(key), message="unknown top-level key; ignored")
                )

        defaults = self._parse_kind_flags(document.get("defaults"), "defaults", issues)
        bulk = self._parse_kind_flags(document.get("bulk"), "bulk", issues)
        strict = self._parse_strict(document.get("strict"), issues)
        known = {
            kind: frozenset((catalog or self._catalog).known_ids(kind)) for kind in CAPABILITY_KINDS
        }
        overrides = self._parse_overrides(document.get("overrides"), known, issues)

        states: dict[tuple[str, str], CapabilityPolicyEntry] = {}
        for kind in sorted(CAPABILITY_KINDS):
            for module_id in sorted(known[kind]):
                override = overrides.get((kind, module_id))
                if override is None and kind == "skill":
                    override = overrides.get((kind, f"*:{_skill_name(module_id)}"))
                if override is not None:
                    state = CapabilityPolicyEntry(
                        kind=kind,
                        module_id=module_id,
                        enabled=override.enabled,
                        reason=override.reason,
                        source="override",
                    )
                elif kind in bulk:
                    state = CapabilityPolicyEntry(kind, module_id, bulk[kind], None, "bulk")
                elif strict:
                    # strict 模式下未被显式启用的一律禁用，不看 defaults。
                    state = CapabilityPolicyEntry(kind, module_id, False, None, "strict")
                elif kind in defaults:
                    state = CapabilityPolicyEntry(kind, module_id, defaults[kind], None, "default")
                else:
                    state = CapabilityPolicyEntry(kind, module_id, True, None, "missing")
                states[(kind, module_id)] = state

        return CapabilityPolicySnapshot(
            version=version,
            states=MappingProxyType(states),
            issues=tuple(issues),
        )

    @staticmethod
    def _parse_strict(raw: object, issues: list[CapabilityPolicyIssue]) -> bool:
        if raw is None:
            return False
        if type(raw) is not bool:
            issues.append(
                CapabilityPolicyIssue(path="strict", message="expected a boolean; ignored")
            )
            return False
        return raw

    @staticmethod
    def _parse_kind_flags(
        raw: object,
        section: str,
        issues: list[CapabilityPolicyIssue],
    ) -> dict[str, bool]:
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            issues.append(
                CapabilityPolicyIssue(path=section, message="expected an object; ignored")
            )
            return {}
        result: dict[str, bool] = {}
        for kind, enabled in raw.items():
            path = f"{section}.{kind}"
            if kind not in CAPABILITY_KINDS:
                issues.append(CapabilityPolicyIssue(path=path, message="unknown kind; ignored"))
            elif type(enabled) is not bool:
                issues.append(
                    CapabilityPolicyIssue(path=path, message="expected a boolean; ignored")
                )
            else:
                result[kind] = enabled
        return result

    @staticmethod
    def _parse_overrides(
        raw: object,
        known: dict[str, frozenset[str]],
        issues: list[CapabilityPolicyIssue],
    ) -> dict[tuple[str, str], _Override]:
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            issues.append(
                CapabilityPolicyIssue(path="overrides", message="expected an object; ignored")
            )
            return {}
        known_to_kind = {
            module_id: kind for kind, module_ids in known.items() for module_id in module_ids
        }
        result: dict[tuple[str, str], _Override] = {}
        for kind, entries in raw.items():
            section = f"overrides.{kind}"
            if kind not in CAPABILITY_KINDS:
                issues.append(CapabilityPolicyIssue(path=section, message="unknown kind; ignored"))
                continue
            if not isinstance(entries, dict):
                issues.append(
                    CapabilityPolicyIssue(path=section, message="expected an object; ignored")
                )
                continue
            for module_id, value in entries.items():
                path = f"{section}.{module_id}"
                if not isinstance(module_id, str) or not _valid_module_id(kind, module_id):
                    issues.append(
                        CapabilityPolicyIssue(
                            path=path,
                            code="STATE_KIND_MISMATCH",
                            message="invalid module ID for kind; ignored",
                        )
                    )
                    continue
                if module_id.startswith("*:"):
                    if kind != "skill" or not any(
                        known_id.endswith(f":{_skill_name(module_id)}")
                        for known_id in known[kind]
                    ):
                        issues.append(
                            CapabilityPolicyIssue(
                                path=path,
                                code="STATE_UNKNOWN_MODULE",
                                message="unknown module ID; ignored",
                            )
                        )
                        continue
                    issues.append(
                        CapabilityPolicyIssue(
                            path=path,
                            message="global skill override applies to all packages",
                        )
                    )
                elif module_id not in known[kind]:
                    actual_kind = known_to_kind.get(module_id)
                    issues.append(
                        CapabilityPolicyIssue(
                            path=path,
                            code=(
                                "STATE_KIND_MISMATCH"
                                if actual_kind is not None
                                else "STATE_UNKNOWN_MODULE"
                            ),
                            message=(
                                f"module belongs to kind {actual_kind}; ignored"
                                if actual_kind is not None
                                else "unknown module ID; ignored"
                            ),
                        )
                    )
                    continue
                override = _parse_override(path, value, issues)
                if override is not None:
                    result[(kind, module_id)] = override
        return result

    @staticmethod
    def _state_from_snapshot(
        snapshot: CapabilityPolicySnapshot,
        kind: str,
        module_id: str,
    ) -> CapabilityPolicyEntry:
        if kind not in CAPABILITY_KINDS:
            raise ValueError(f"unknown module kind: {kind}")
        return snapshot.states.get(
            (kind, module_id),
            CapabilityPolicyEntry(kind, module_id, True, None, "missing"),
        )

    @staticmethod
    def _log_issues(issues: tuple[CapabilityPolicyIssue, ...]) -> None:
        for issue in issues:
            logger.warning(
                "capability policy report: path=%s code=%s message=%s",
                issue.path,
                issue.code or "INVALID_CONFIG_KEY",
                issue.message,
            )


def _parse_override(
    path: str,
    value: object,
    issues: list[CapabilityPolicyIssue],
) -> _Override | None:
    if type(value) is bool:
        return _Override(enabled=value, reason=None)
    if not isinstance(value, dict):
        issues.append(
            CapabilityPolicyIssue(path=path, message="expected a boolean or object; ignored")
        )
        return None
    unknown_keys = set(value) - {"enabled", "reason"}
    if unknown_keys:
        issues.append(
            CapabilityPolicyIssue(
                path=path,
                message=f"unknown keys {sorted(unknown_keys)!r}; ignored",
            )
        )
        return None
    enabled = value.get("enabled")
    reason = value.get("reason")
    if type(enabled) is not bool or (reason is not None and not isinstance(reason, str)):
        issues.append(
            CapabilityPolicyIssue(path=path, message="invalid enabled/reason value; ignored")
        )
        return None
    return _Override(enabled=enabled, reason=reason)


def _valid_module_id(kind: str, module_id: str) -> bool:
    if not module_id or module_id.strip() != module_id:
        return False
    if kind == "skill":
        package, separator, skill = module_id.partition(":")
        return bool(separator and package and skill and ":" not in skill)
    return "/" not in module_id and ":" not in module_id


def _skill_name(module_id: str) -> str:
    return module_id.partition(":")[2]


__all__ = [
    "CAPABILITY_KINDS",
    "CapabilityCatalog",
    "CapabilityKind",
    "CapabilityPolicyCandidate",
    "CapabilityPolicyEntry",
    "CapabilityPolicyIssue",
    "CapabilityPolicyLoadError",
    "CapabilityPolicyRegistry",
    "CapabilityPolicySnapshot",
    "CapabilityPolicySource",
]
