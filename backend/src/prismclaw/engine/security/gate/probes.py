from __future__ import annotations

import ast
import os
import re
import secrets
from pathlib import Path
from typing import NamedTuple

from prismclaw.schema.agent.task_runtime import (
    PreflightProbeResult,
    ProbeSignal,
    RecoveryProbeResult,
)
from prismclaw.schema.agent.task_types import TaskSpec

_WORKSPACE_SCAN_FILE_LIMIT = 400
_WORKSPACE_SCAN_DIR_LIMIT = 120
_PYTHON_DEPENDENCY_SCAN_LIMIT = 8
_WORKSPACE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_./\\-])"
    r"(?!https?://)(?!file://)(?![A-Za-z]:[\\/])"
    r"(?:"
    r"[.A-Za-z0-9_-]+(?:/[.A-Za-z0-9_-]+)+"
    r"|[A-Za-z0-9_.-]+\.(?:py|pyi|ts|tsx|js|jsx|mjs|cjs|json|toml|ya?ml|md|txt|lock"
    r"|go|rs|java|kt|kts|sql|sh|bash|css|scss|html|vue|ipynb|cfg|ini|env|gradle|properties)"
    r"|(?:dockerfile|makefile)"
    r")"
    r"(?=$|[\s)\]}>,.:;!?，。：；！？、）】\"'])",
    re.IGNORECASE,
)

_CRITICAL_PATH_SEGMENTS = {
    "pyproject.toml",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "docker-compose.yml",
    "docker-compose.yaml",
    "dockerfile",
    ".github",
    "configs",
}
_CODE_FILE_EXTENSIONS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".kt",
}
_IGNORED_SCAN_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".idea",
}


def _signal(code: str, severity: str, summary: str) -> ProbeSignal:
    return ProbeSignal(code=code, severity=severity, summary=summary)


def _workspace_root(workspace_path: str | os.PathLike[str] | None) -> Path | None:
    if workspace_path is None:
        return None
    try:
        root = Path(workspace_path).resolve()
    except OSError:
        return None
    return root if root.exists() and root.is_dir() else None


def _extract_workspace_paths(text: str) -> list[str]:
    paths: list[str] = []
    for match in _WORKSPACE_PATH_RE.finditer(text or ""):
        candidate = match.group(0)
        if candidate and candidate not in paths:
            paths.append(candidate)
    return paths


def _resolve_within_workspace(root: Path, raw_path: str) -> tuple[str, Path] | None:
    normalized = raw_path.strip()
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized:
        return None
    candidate = (root / normalized).resolve()
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return None
    return relative.as_posix(), candidate


def _is_critical_path(relative_path: str) -> bool:
    lowered = relative_path.lower()
    if lowered in _CRITICAL_PATH_SEGMENTS:
        return True
    first_segment = lowered.split("/", 1)[0]
    basename = lowered.rsplit("/", 1)[-1]
    return first_segment in _CRITICAL_PATH_SEGMENTS or basename in _CRITICAL_PATH_SEGMENTS


def _dedupe_paths(paths: list[str]) -> list[str]:
    return list(dict.fromkeys(path for path in paths if path))


class _WorkspaceScan(NamedTuple):
    """一次全树遍历的聚合结果。

    计数只用于规模信号；``critical_paths`` 是恢复阶段判定漂移的唯一依据。
    """

    file_count: int
    dir_count: int
    top_level_root_count: int
    code_file_count: int
    critical_paths: list[str]


def _scan_workspace_tree(root: Path) -> _WorkspaceScan:
    file_count = 0
    dir_count = 0
    code_file_count = 0
    critical_paths: list[str] = []
    top_level_roots: list[str] = []
    for current_root, dirnames, filenames in os.walk(root):
        current_path = Path(current_root)
        dirnames[:] = sorted(
            dirname for dirname in dirnames if dirname not in _IGNORED_SCAN_DIRS
        )
        if current_path != root:
            dir_count += 1
        for dirname in dirnames:
            if dir_count >= _WORKSPACE_SCAN_DIR_LIMIT:
                dirnames[:] = []
                break
            relative = (current_path / dirname).relative_to(root).as_posix()
            top_level = relative.split("/", 1)[0]
            if top_level not in top_level_roots:
                top_level_roots.append(top_level)
        for filename in sorted(filenames):
            if file_count >= _WORKSPACE_SCAN_FILE_LIMIT:
                return _WorkspaceScan(
                    file_count=file_count,
                    dir_count=dir_count,
                    top_level_root_count=len(top_level_roots),
                    code_file_count=code_file_count,
                    critical_paths=critical_paths,
                )
            try:
                relative = (current_path / filename).relative_to(root).as_posix()
            except ValueError:
                continue
            file_count += 1
            if Path(filename).suffix.lower() in _CODE_FILE_EXTENSIONS:
                code_file_count += 1
            top_level = relative.split("/", 1)[0]
            if top_level not in top_level_roots:
                top_level_roots.append(top_level)
            if _is_critical_path(relative) and relative not in critical_paths:
                critical_paths.append(relative)
        if dir_count >= _WORKSPACE_SCAN_DIR_LIMIT:
            break
    return _WorkspaceScan(
        file_count=file_count,
        dir_count=dir_count,
        top_level_root_count=len(top_level_roots),
        code_file_count=code_file_count,
        critical_paths=critical_paths,
    )


def _local_package_root_map(root: Path) -> dict[str, str]:
    package_roots: dict[str, str] = {}
    for child in root.iterdir():
        if child.name in _IGNORED_SCAN_DIRS:
            continue
        if child.is_dir():
            package_roots[child.name] = child.name
    src_root = root / "src"
    if src_root.exists() and src_root.is_dir():
        package_roots["src"] = "src"
        for child in src_root.iterdir():
            if child.is_dir() and (child / "__init__.py").exists():
                package_roots[child.name] = "src"
    return package_roots


def _scan_local_python_dependency_roots(root: Path, existing_paths: list[str]) -> list[str]:
    package_roots = _local_package_root_map(root)
    dependency_roots: list[str] = []
    scanned = 0
    for relative_path in existing_paths:
        if not relative_path.endswith(".py"):
            continue
        candidate = root / relative_path
        if not candidate.exists() or not candidate.is_file():
            continue
        try:
            module = ast.parse(candidate.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        scanned += 1
        for node in ast.walk(module):
            module_name: str | None = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module_name = alias.name
                    import_root = module_name.split(".", 1)[0]
                    workspace_root = package_roots.get(import_root)
                    if workspace_root and workspace_root not in dependency_roots:
                        dependency_roots.append(workspace_root)
            elif isinstance(node, ast.ImportFrom) and node.module:
                module_name = node.module
                import_root = module_name.split(".", 1)[0]
                workspace_root = package_roots.get(import_root)
                if workspace_root and workspace_root not in dependency_roots:
                    dependency_roots.append(workspace_root)
        if scanned >= _PYTHON_DEPENDENCY_SCAN_LIMIT:
            break
    return dependency_roots


def _task_spec_probe_targets(task_spec: TaskSpec) -> list[str]:
    return _dedupe_paths(
        _extract_workspace_paths(
            " ".join(
                [
                    task_spec.goal,
                    *task_spec.constraints,
                ]
            )
        )
    )


def _recovery_probe_targets(
    *,
    task_spec: TaskSpec | None,
    preflight_probe: PreflightProbeResult | None,
) -> tuple[str, list[str]]:
    if preflight_probe is not None:
        return "preflight_probe", _dedupe_paths(list(preflight_probe.existing_paths))
    if task_spec is None:
        return "none", []
    spec_targets = _task_spec_probe_targets(task_spec)
    if spec_targets:
        return "task_spec_text", spec_targets
    return "none", []


def run_preflight_probe(
    *,
    user_message: str,
    workspace_path: str | os.PathLike[str] | None,
) -> PreflightProbeResult:
    root = _workspace_root(workspace_path)
    mentioned_paths = _extract_workspace_paths(user_message)
    existing_paths: list[str] = []
    touched_roots: list[str] = []
    signals: list[ProbeSignal] = []
    critical_workspace_paths: list[str] = []

    if root is None:
        signals.append(_signal("workspace_unavailable", "warning", "探测时工作区根目录不可用。"))
    else:
        scan = _scan_workspace_tree(root)
        critical_workspace_paths = scan.critical_paths
        if scan.file_count >= 120 or scan.dir_count >= 40 or scan.top_level_root_count >= 5:
            signals.append(
                _signal(
                    "workspace_tree_broad",
                    "warning",
                    "工作区结构过于庞大，需要更强的规划约束。",
                )
            )
        if scan.code_file_count >= 80:
            signals.append(
                _signal(
                    "code_surface_large",
                    "warning",
                    f"工作区包含 {scan.code_file_count} 个代码文件，执行面较广。",
                )
            )
        for raw_path in mentioned_paths:
            resolved = _resolve_within_workspace(root, raw_path)
            if resolved is None:
                continue
            relative_path, candidate = resolved
            if not candidate.exists():
                continue
            existing_paths.append(relative_path)
            top_level = relative_path.split("/", 1)[0]
            if top_level not in touched_roots:
                touched_roots.append(top_level)
            if _is_critical_path(relative_path):
                signals.append(
                    _signal(
                        "critical_path",
                        "high",
                        f"引用了关键工作区目标 `{relative_path}`。",
                    )
                )
        dependency_roots = _scan_local_python_dependency_roots(root, existing_paths)
        if len(dependency_roots) >= 2:
            signals.append(
                _signal(
                    "local_dependency_span",
                    "high",
                    "被引用的 Python 文件依赖多个本地工作区根目录。",
                )
            )
        elif dependency_roots:
            signals.append(
                _signal(
                    "local_dependency_scan",
                    "info",
                    f"被引用的 Python 文件依赖本地工作区根目录：{', '.join(dependency_roots)}。",
                )
            )

    if len(touched_roots) >= 2:
        signals.append(
            _signal(
                "cross_module_span",
                "high",
                "被引用的文件跨越多个顶层工作区区域。",
            )
        )
    elif len(existing_paths) >= 3:
        signals.append(
            _signal(
                "multi_file_span",
                "warning",
                "同一轮运行中引用了多个已存在的工作区文件。",
            )
        )

    baseline_risk = "high" if any(signal.severity == "high" for signal in signals) else "normal"
    return PreflightProbeResult(
        probe_id=f"pfp_{secrets.token_hex(4)}",
        workspace_root=str(root) if root is not None else None,
        mentioned_paths=mentioned_paths,
        existing_paths=existing_paths,
        critical_workspace_paths=critical_workspace_paths,
        baseline_risk=baseline_risk,
        signals=signals,
        summary=(
            f"预检探测发现 {len(existing_paths)} 个已存在目标、"
            f"{len(critical_workspace_paths)} 个关键工作区路径；基线风险={baseline_risk}。"
        ),
    )


def run_recovery_probe(
    *,
    task_spec: TaskSpec | None,
    workspace_path: str | os.PathLike[str] | None,
    preflight_probe: PreflightProbeResult | None = None,
) -> RecoveryProbeResult:
    root = _workspace_root(workspace_path)
    checked_paths: list[str] = []
    missing_paths: list[str] = []
    signals: list[ProbeSignal] = []
    status = "ready"
    baseline_source = "none"

    if root is None:
        signals.append(_signal("workspace_missing", "high", "恢复阶段工作区根目录不可用。"))
        status = "blocked"
    else:
        checked_paths.append(".")
        if not os.access(root, os.R_OK):
            signals.append(_signal("workspace_unreadable", "high", "工作区根目录不可读。"))
            status = "blocked"
        baseline_source, recovery_targets = _recovery_probe_targets(
            task_spec=task_spec,
            preflight_probe=preflight_probe,
        )
        if baseline_source == "preflight_probe":
            signals.append(
                _signal(
                    "preflight_baseline_reused",
                    "info",
                    "恢复探测复用了预检阶段的工作区基线。",
                )
            )
        elif baseline_source == "task_spec_text":
            signals.append(
                _signal(
                    "task_spec_target_fallback",
                    "warning",
                    "由于缺少预检基线，恢复探测回退为使用任务描述文本。",
                )
            )
        if preflight_probe is not None:
            baseline_critical_paths = _dedupe_paths(list(preflight_probe.critical_workspace_paths))
            checked_paths.extend(baseline_critical_paths)
            missing_critical_paths: list[str] = []
            for path in baseline_critical_paths:
                resolved = _resolve_within_workspace(root, path)
                if resolved is None or not resolved[1].exists():
                    missing_critical_paths.append(path)
            if missing_critical_paths:
                signals.append(
                    _signal(
                        "critical_workspace_drift",
                        "high",
                        "预检基线中记录的关键工作区路径现已缺失。",
                    )
                )
                missing_paths.extend(missing_critical_paths)
                status = "blocked"
        for raw_path in recovery_targets:
            resolved = _resolve_within_workspace(root, raw_path)
            if resolved is None:
                continue
            relative_path, candidate = resolved
            checked_paths.append(relative_path)
            if not candidate.exists():
                missing_paths.append(relative_path)
        if missing_paths and status != "blocked":
            signals.append(
                _signal(
                    "referenced_paths_missing",
                    "warning",
                    "恢复阶段发现部分任务依赖的工作区路径缺失。",
                )
            )
            status = "degraded"

    if not signals:
        signals.append(_signal("workspace_ready", "info", "工作区探测已完成，未发现偏移。"))
    summary = (
        f"恢复探测状态={status}，基线来源={baseline_source}；"
        f"共检查 {len(dict.fromkeys(checked_paths))} 个路径，"
        f"发现 {len(dict.fromkeys(missing_paths))} 个缺失目标。"
    )
    return RecoveryProbeResult(
        probe_id=f"rcp_{secrets.token_hex(4)}",
        workspace_root=str(root) if root is not None else None,
        baseline_source=baseline_source,
        status=status,
        checked_paths=list(dict.fromkeys(checked_paths)),
        missing_paths=list(dict.fromkeys(missing_paths)),
        signals=signals,
        summary=summary,
    )
