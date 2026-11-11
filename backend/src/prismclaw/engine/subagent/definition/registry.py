"""Sub-agent registry with project-only discovery.

v4 多源目录收敛(SDD §2.13):原 5 层(personal/managed/legacy/.work/agents/local)
全部收敛为单一 project 目录:``<workspace>/.work/agents/``。对应的

  ``self._sources_in_order`` 返回值类型简化:
  ``list[tuple[str, Path]]`` → ``list[Path]``(source 标签仅剩 "project",失去意义)。

Sub-agent manifests are flat ``<name>.md`` files at the directory's top
level (NOT ``<dir>/SKILL.md`` like skills), so this module implements its
own scanner rather than reusing ``discovery_helpers._scan_directory``.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any, Iterable

from prismclaw.engine.subagent.definition.manifest import AgentManifest
from prismclaw.engine.subagent.definition.parser import parse_agent_md
from prismclaw.engine.subagent.core.constants import MAX_FILE_BYTES
from prismclaw.schema.shared import (
    CapabilityLocation,
    _is_valid_entry_name,
    _validate_path_containment,
)
from prismclaw.schema.config.config import SubAgentConfig
from prismclaw.schema.shared.exceptions import SkillParseError

logger = logging.getLogger(__name__)


_MAX_FILES_PER_LAYER = 200


@dataclass(frozen=True, slots=True)
class _LayerScanDiagnostics:
    """Detailed per-file scan result.

    Produced by ``_scan_layer_detailed`` and consumed exclusively by the
    ``/agents diagnose`` slash command. Normal SubAgentRegistry construction
    discards this detail to avoid permanent memory overhead per manifest.
    """

    source: str
    directory: str
    loaded_count: int
    candidates_scanned: int
    hard_stops: tuple[dict, ...]  # each: {"reason": "...", "path": "...", "detail": "..."}
    per_file: tuple[dict, ...]  # each: {"path": "...", "result": "ok|skip|error", ... }


@dataclass(frozen=True, slots=True)
class _FullDiagnostics:
    """Complete diagnose snapshot covering all 5 discovery layers + 3 gap classes."""

    layers: tuple[_LayerScanDiagnostics, ...]
    merged_names: tuple[str, ...]  # final merged manifest name list (sorted)
    # 3类差异
    name_mismatches: tuple[dict, ...]  # 类1: manifest.name 与文件 stem/namespace 不匹配
    can_handoff_to_unknown: tuple[dict, ...]  # 类2: can_handoff_to 指向未知目标
    allowed_tools_gaps: tuple[dict, ...]  # 类3: allowed_tools 白名单包含未注册工具名
    registry_settings: dict  # subagent settings snapshot for report context


class SubAgentRegistry:
    """Discover ``<name>.md`` manifests from five directories with priority merge.

    Construction-time semantics:

    - Each layer is scanned in ascending priority order; later layers override
      earlier ones for the same persona ``name``.
    - Parse errors are logged at ``WARNING`` and the offending file is skipped
      (the rest of the layer continues to be scanned).
    - An entirely missing directory is silently skipped (zero log noise).
    - The registry is fully populated after ``__init__`` returns; there is no
      lazy-load. Reload is a future concern (``/agents reload`` slash command).
    """

    def __init__(
        self,
        settings: SubAgentConfig,
        workspace_path: Path,
        *,
        project_root: Path | None = None,
        additional_project_roots: list[Path | str] | None = None,
        agent_locations: Iterable[CapabilityLocation] | None = None,
    ) -> None:
        self._settings = settings
        self._workspace_path = Path(workspace_path)

        # 目录定位（root 去重 / 相对目录展开 / exists 判定）统一由 layout builder
        # 完成；deps 边界可直接注入同一份布局。未注入时以 workspace 为根现场展开。
        if agent_locations is None:
            from prismclaw.infra.paths import build_project_capability_layout
            from prismclaw.schema.shared import CapabilityKind

            primary = (
                Path(project_root) if project_root is not None else self._workspace_path
            )
            layout = build_project_capability_layout(
                primary=primary,
                additional=additional_project_roots,
                agents_dir=settings.asset_subagents_dir,
            )
            agent_locations = layout.locations_of(CapabilityKind.AGENTS)

        # 保留全部已配置目录（含不存在的），diagnose 与加载共用同一序列；
        # _scan_layer 对缺失目录静默跳过，故加载结果与只扫存在目录等价。
        self._sources_in_order: list[Path] = [loc.path for loc in agent_locations]
        self._manifests: dict[str, AgentManifest] = {}

        for directory in self._sources_in_order:
            for manifest in self._scan_layer(directory, source="project"):
                self._manifests[manifest.name] = manifest

        self._warn_can_handoff_to_typos()

    def _warn_can_handoff_to_typos(self) -> None:
        registered_names = set(self._manifests.keys())
        for manifest in self._manifests.values():
            if manifest.can_handoff_to is None:
                continue
            for entry in manifest.can_handoff_to:
                if entry in registered_names:
                    continue
                suggestion = _closest_match(entry, registered_names, max_dist=2)
                hint = f" Did you mean '{suggestion}'?" if suggestion else ""
                logger.warning(
                    "subagent.can_handoff_to.unknown_target manifest=%s entry=%s%s",
                    manifest.name,
                    entry,
                    hint,
                )

    def register(
        self,
        name: str,
        manifest: AgentManifest,
    ) -> None:
        """Register or override a manifest programmatically."""
        if manifest.name != name:
            raise ValueError(
                f"manifest.name {manifest.name!r} does not match registered name {name!r}"
            )
        if name in self._manifests:
            logger.warning(
                "Agent %r replaces existing agent with same canonical_id %r",
                manifest.name, name,
            )
        self._manifests[name] = manifest

    def get(self, name: str) -> AgentManifest | None:
        return self._manifests.get(name)

    def list_all(self) -> list[AgentManifest]:
        return sorted(self._manifests.values(), key=lambda m: m.name)

    def __len__(self) -> int:
        return len(self._manifests)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._manifests

    # ------------------------------------------------------------------
    # Diagnostics (observability only; not on hot-path)
    # ------------------------------------------------------------------
    def diagnose(self, registered_tool_names: set[str] | None = None) -> _FullDiagnostics:
        """Build structured diagnose snapshot (default: JSON-encodable).

        Covers all 5 discovery layers plus the 3-class gap analysis:
          类1 - manifest.name vs file-stem/namespace mismatches
          类2 - can_handoff_to references to unknown targets
          类3 - allowed_tools whitelist entries not in parent tool registry

        File-level detail from the diagnostic pass is reused directly for
        类1 so the same md files are not parsed twice.
        """
        tool_names = registered_tool_names or set()
        layers: list[_LayerScanDiagnostics] = []
        for directory in self._sources_in_order:
            layers.append(self._scan_layer_for_diagnose(directory, source="project"))
        merged_names = tuple(sorted(self._manifests.keys()))
        registered = set(self._manifests.keys())

        # --- 类1: manifest.name vs 文件 stem 不匹配 ---
        # 直接复用 layers[*].per_file 中已经解析得到的 file_stem/manifest_name，
        # 不再对磁盘重解析。所有 result=="ok" 的 per_file 条目必然已通过
        # parse 阶段，因此 manifest_name / file_stem 字段可用。
        name_mismatches: list[dict] = []
        for layer in layers:
            for rec in layer.per_file:
                if rec.get("result") != "ok":
                    continue
                manifest_name = rec.get("manifest_name")
                file_stem = rec.get("file_stem")
                if (
                    manifest_name is None
                    or file_stem is None
                    or manifest_name == file_stem
                ):
                    continue
                name_mismatches.append({
                    "path": rec["path"],
                    "file_stem": file_stem,
                    "manifest_name": manifest_name,
                    "source": layer.source,
                })

        # --- 类2: can_handoff_to 未知目标 ---
        can_handoff_unknown: list[dict] = []
        for manifest in self._manifests.values():
            targets = manifest.can_handoff_to or []
            for target in targets:
                if target in registered:
                    continue
                suggestion = _closest_match(target, registered, max_dist=2)
                can_handoff_unknown.append({
                    "manifest": manifest.name,
                    "unknown_target": target,
                    "suggestion": suggestion,
                })

        # --- 类3: allowed_tools 白名单缺口 ---
        allowed_gaps: list[dict] = []
        for manifest in self._manifests.values():
            allowed = manifest.allowed_tools
            if allowed is None:
                continue  # None = inherit all parent tools, no gap possible
            for tool_name in allowed:
                if tool_name in registered:
                    continue  # 允许 allowed_tools 与子代理同名（非冲突场景），跳过
                if registered_tool_names and tool_name in tool_names:
                    continue
                allowed_gaps.append({
                    "manifest": manifest.name,
                    "unknown_tool": tool_name,
                    "suggestion": (
                        _closest_match(tool_name, tool_names, max_dist=2)
                        if tool_names else None
                    ),
                })

        settings_snapshot = {
            "enabled": getattr(self._settings, "enabled", None),
            "max_depth": getattr(self._settings, "max_depth", None),
            "handoff_enabled": getattr(self._settings, "handoff_enabled", None),
            "max_handoff_chain_length": getattr(
                self._settings, "max_handoff_chain_length", None
            ),
        }

        return _FullDiagnostics(
            layers=tuple(layers),
            merged_names=merged_names,
            name_mismatches=tuple(name_mismatches),
            can_handoff_to_unknown=tuple(can_handoff_unknown),
            allowed_tools_gaps=tuple(allowed_gaps),
            registry_settings=settings_snapshot,
        )

    # ------------------------------------------------------------------
    # Shared scanning primitives (single source of truth for BFS walk +
    # hard-stop guards). Wrappers exist for hot-path, diagnose, and
    # file-only iteration — they all delegate to the same core logic.
    # ------------------------------------------------------------------

    @staticmethod
    def _iter_layer_nodes(root: Path, max_depth: int = 3) -> Iterable[tuple[Path, int, list[str]]]:
        """Pure BFS traversal yielding every entry that passes entry-name filter.

        No side effects, no filtering beyond ``_is_valid_entry_name`` and
        ``max_depth``. Callers apply semantics (skip/error/ok classification,
        hard stops, logging, manifest parsing) on top of the yielded tuples:

            (path: Path, depth: int, namespace_parts: list[str])

        Replaces three previously duplicated BFS loops in ``_iter_md_files``,
        ``_scan_layer``, and ``_scan_layer_for_diagnose`` with a single
        deterministic primitive.
        """
        stack: list[tuple[Path, int, list[str]]] = [(root, 1, [])]
        while stack:
            current_dir, depth, ns_parts = stack.pop()
            if depth > max_depth:
                continue
            try:
                entries = sorted(current_dir.iterdir(), key=lambda p: p.name)
            except OSError:
                continue
            for path in entries:
                if not _is_valid_entry_name(path.name):
                    continue
                if path.is_dir() and depth < max_depth:
                    stack.append((path, depth + 1, ns_parts + [path.name]))
                else:
                    yield path, depth, ns_parts

    @staticmethod
    def _iter_md_files(root: Path) -> Iterable[Path]:
        """Thin wrapper: yield only the .md files discovered by the BFS primitive."""
        for path, _depth, _ns in SubAgentRegistry._iter_layer_nodes(root):
            if path.is_file() and path.suffix == ".md":
                yield path

    @staticmethod
    def _do_scan_layer(
        directory: Path,
        source: str,
        *,
        produce_per_file: bool,
        emit_logs: bool,
    ) -> tuple[list[AgentManifest], int, list[dict], list[dict]]:
        """Single, authoritative scanner for a single discovery layer.

        One implementation handles both the hot path (used at registry init)
        and the diagnose path. Behaviour is toggled through two flags rather
        than through code duplication:

        * ``produce_per_file`` — when False, ``per_file`` is returned as an
          empty list to save allocation overhead on the hot path.
        * ``emit_logs`` — when True, hard-stops fire at WARNING level and
          skip/parse events at DEBUG. When False the scan is silent and all
          diagnostics are surfaced through ``hard_stops`` + ``per_file``.

        Returns a 4-tuple ``(loaded, candidates_scanned, hard_stops, per_file)``
        rather than a dataclass so callers can cheaply unpack only what they
        need.
        """
        loaded: list[AgentManifest] = []
        hard_stops: list[dict] = []
        per_file: list[dict] = []
        candidates_scanned = 0

        if not directory.is_dir():
            if produce_per_file or not emit_logs:
                hard_stops.append({
                    "reason": "directory_not_found_or_not_dir",
                    "path": str(directory),
                    "detail": "",
                })
            if emit_logs:
                logger.debug(
                    "subagent.discovery.layer_skip reason=not_a_dir source=%s path=%s",
                    source, directory,
                )
            return loaded, 0, hard_stops, per_file

        if emit_logs:
            logger.debug(
                "subagent.discovery.layer_start source=%s path=%s",
                source, directory,
            )

        # -- Shared hard-stop + record helpers (parametrized by flags) -----
        def _record_hard_stop(reason: str, path: Path, detail: str = "") -> None:
            hard_stops.append({
                "reason": reason,
                "path": str(path),
                "detail": detail,
            })

        def _log_or_record(
            level: str,
            reason: str,
            path: Path,
            *,
            exc: BaseException | None = None,
            **fields: Any,
        ) -> None:
            """Unified sink: emit log line when ``emit_logs`` and append a
            per-file dict when ``produce_per_file``."""
            if emit_logs:
                if level == "warning" and exc is not None:
                    logger.warning(
                        "subagent.discovery.%s path=%s error_type=%s error=%s",
                        reason, path, type(exc).__name__, exc,
                    )
                elif level == "warning":
                    extra = " ".join(f"{k}={v}" for k, v in fields.items())
                    logger.warning(
                        "subagent.discovery.%s path=%s %s", reason, path, extra,
                    )
                else:  # debug
                    extra = " ".join(f"{k}={v}" for k, v in fields.items())
                    logger.debug(
                        "subagent.discovery.%s path=%s %s", reason, path, extra,
                    )
            if produce_per_file:
                rec: dict[str, Any] = {
                    "path": str(path),
                    "result": fields.pop("result", level),
                    "reason": reason,
                }
                rec.update(fields)
                if exc is not None:
                    rec["error_type"] = type(exc).__name__
                    rec["error_message"] = str(exc)
                per_file.append(rec)

        # -- Single BFS pass with all guards shared -----------------------
        layer_cap_hit = False
        for path, depth, ns_parts in SubAgentRegistry._iter_layer_nodes(directory):
            if len(loaded) >= _MAX_FILES_PER_LAYER:
                layer_cap_hit = True
                break
            if depth > 3:
                # _iter_layer_nodes already caps depth, but belt-and-braces
                if produce_per_file:
                    per_file.append({
                        "path": str(path), "result": "skip",
                        "reason": "max_depth_exceeded", "depth": depth,
                    })
                continue

            # Directories are just scaffolding for the BFS primitive; a
            # directory itself never produces a per-file record.
            if path.is_dir():
                continue

            # 1) non-.md files: skip cheaply
            if not (path.is_file() and path.suffix == ".md"):
                _log_or_record(
                    "debug", "non_md_file", path, result="skip",
                    ext=path.suffix or "",
                )
                continue

            # 2) count .md candidate; candidate_overload hard stop
            candidates_scanned += 1
            if candidates_scanned > 1000:
                msg = f"scanned={candidates_scanned}, limit=1000"
                _record_hard_stop("candidate_overload", directory, msg)
                if emit_logs:
                    logger.warning(
                        "subagent.discovery.layer_hard_stop reason=candidate_overload "
                        "source=%s scanned=%d limit=1000 dir=%s",
                        source, candidates_scanned, directory,
                    )
                break

            # 3) stat + file size gate
            try:
                size = path.stat().st_size
            except OSError as exc:
                _record_hard_stop("stat_error", path, type(exc).__name__)
                _log_or_record("warning", "stat_error", path, exc=exc, result="error")
                continue
            if size > MAX_FILE_BYTES:
                detail = f"size={size}, max={MAX_FILE_BYTES}"
                _record_hard_stop("file_too_large", path, detail)
                _log_or_record(
                    "warning", "file_too_large", path,
                    result="error", size=size, max=MAX_FILE_BYTES,
                )
                continue

            # 4) containment escape (symlink jailbreak)
            if not _validate_path_containment(path, directory):
                detail = f"root={directory}"
                _record_hard_stop("containment_escape", path, detail)
                _log_or_record(
                    "warning", "containment_escape", path,
                    result="error", detail=detail,
                )
                continue

            if emit_logs:
                logger.debug(
                    "subagent.discovery.file_parse_start path=%s size=%d",
                    path, size,
                )

            # 5) parse + optional namespace injection
            ns = ".".join(ns_parts + [path.stem])
            try:
                manifest = parse_agent_md(path)
                previous_name = manifest.name
                if manifest.name == path.stem and ns_parts:
                    manifest = manifest.model_copy(update={"name": ns})
            except (SkillParseError, ValueError) as exc:
                _record_hard_stop(
                    "parse_error", path,
                    f"{type(exc).__name__}: {exc}",
                )
                _log_or_record("warning", "parse_error", path, exc=exc, result="error")
                continue

            loaded.append(manifest.model_copy(update={"source": source}))
            if emit_logs:
                applied = "yes" if (manifest.name != previous_name and ns_parts) else "no"
                logger.debug(
                    "subagent.discovery.file_parse_ok path=%s name=%s "
                    "namespace_applied=%s source=%s",
                    path, manifest.name, applied, source,
                )
            if produce_per_file:
                per_file.append({
                    "path": str(path),
                    "result": "ok",
                    "size": size,
                    "manifest_name": manifest.name,
                    "file_stem": path.stem,
                    "namespace_applied": (
                        manifest.name != previous_name and bool(ns_parts)
                    ),
                })

        if layer_cap_hit:
            _record_hard_stop(
                "max_files_per_layer", directory,
                f"limit={_MAX_FILES_PER_LAYER}",
            )
            if emit_logs:
                logger.warning(
                    "subagent.discovery.layer_hard_stop reason=max_files_per_layer "
                    "source=%s limit=%d", source, _MAX_FILES_PER_LAYER,
                )

        if emit_logs:
            logger.debug(
                "subagent.discovery.layer_end source=%s loaded=%d total_candidates_scanned=%d",
                source, len(loaded), candidates_scanned,
            )
        return loaded, candidates_scanned, hard_stops, per_file

    @staticmethod
    def _scan_layer_for_diagnose(directory: Path, source: str) -> _LayerScanDiagnostics:
        """Diagnose wrapper: full per-file + hard-stop detail, no logging."""
        loaded, scanned, hard_stops, per_file = SubAgentRegistry._do_scan_layer(
            directory, source, produce_per_file=True, emit_logs=False,
        )
        return _LayerScanDiagnostics(
            source=source,
            directory=str(directory),
            loaded_count=len(loaded),
            candidates_scanned=scanned,
            hard_stops=tuple(hard_stops),
            per_file=tuple(per_file),
        )

    def _scan_layer(self, directory: Path, source: str) -> Iterable[AgentManifest]:
        """Hot-path wrapper: manifests only, WARNING/DEBUG logging on."""
        loaded, _scanned, _hs, _pf = SubAgentRegistry._do_scan_layer(
            directory, source, produce_per_file=False, emit_logs=True,
        )
        return loaded


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[-1]


def _closest_match(
    target: str, candidates: set[str], *, max_dist: int = 2
) -> str | None:
    best: str | None = None
    best_dist = max_dist + 1
    for cand in candidates:
        dist = _levenshtein(target, cand)
        if dist < best_dist:
            best_dist = dist
            best = cand
    if best_dist > max_dist:
        return None
    return best


__all__ = ["SubAgentRegistry"]
