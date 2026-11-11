from __future__ import annotations

import ast
import os
import re
import secrets
import subprocess
from pathlib import Path

from prismclaw.schema.agent.task_types import TaskSpec
from prismclaw.schema.agent.task_runtime import (
    PreflightProbeResult,
    ProbeSignal,
    RecoveryProbeResult,
)

_WORKSPACE_SCAN_FILE_LIMIT = 400
_WORKSPACE_SCAN_DIR_LIMIT = 120
_PYTHON_DEPENDENCY_SCAN_LIMIT = 8
_WORKSPACE_PATH_RE = re.compile(
    r"(?:(?:^|[\s(]))"
    r"(?!https?://)(?!file://)(?!/)(?![A-Za-z]:[\\/])"
    r"[.A-Za-z0-9_-]+(?:/[.A-Za-z0-9_-]+)+"
    r"(?=$|[\s)\],.:;!?，。：；！？])",
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
    "backend/src/prismclaw/core/agent/runner.py",
    "backend/src/prismclaw/core/agent/arbiter.py",
    "backend/src/prismclaw/core/services/persistence/run_state/persistence.py",
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
        candidate = match.group(0).strip().strip("()[]{}<>,.:;!?，。：；！？")
        if not candidate:
            continue
        if candidate not in paths:
            paths.append(candidate)
    return paths


def _resolve_within_workspace(root: Path, raw_path: str) -> tuple[str, Path] | None:
    normalized = raw_path.strip().lstrip("./")
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


def _scan_workspace_tree(
    root: Path,
) -> tuple[int, int, int, list[str], list[str]]:
    file_count = 0
    dir_count = 0
    critical_workspace_paths: list[str] = []
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
            candidate = current_path / dirname
            relative = candidate.relative_to(root).as_posix()
            top_level = relative.split("/", 1)[0]
            if top_level not in top_level_roots:
                top_level_roots.append(top_level)
        for filename in sorted(filenames):
            if file_count >= _WORKSPACE_SCAN_FILE_LIMIT:
                return file_count, dir_count, len(top_level_roots), critical_workspace_paths, top_level_roots
            candidate = current_path / filename
            try:
                relative = candidate.relative_to(root).as_posix()
            except ValueError:
                continue
            file_count += 1
            top_level = relative.split("/", 1)[0]
            if top_level not in top_level_roots:
                top_level_roots.append(top_level)
            if _is_critical_path(relative) and relative not in critical_workspace_paths:
                critical_workspace_paths.append(relative)
        if dir_count >= _WORKSPACE_SCAN_DIR_LIMIT:
            break
    return file_count, dir_count, len(top_level_roots), critical_workspace_paths, top_level_roots


def _count_code_files(root: Path) -> int:
    count = 0
    for _, dirnames, filenames in os.walk(root):
        dirnames[:] = [dirname for dirname in dirnames if dirname not in _IGNORED_SCAN_DIRS]
        for filename in filenames:
            if count >= _WORKSPACE_SCAN_FILE_LIMIT:
                return count
            if Path(filename).suffix.lower() in _CODE_FILE_EXTENSIONS:
                count += 1
    return count


def _git_dirty_paths(root: Path) -> tuple[list[str], bool]:
    git_dir = root / ".git"
    if not git_dir.exists():
        return [], False
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=normal"],
            capture_output=True,
            text=True,
            timeout=0.25,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, TimeoutError):
        return [], True
    if result.returncode != 0:
        return [], True
    dirty_paths: list[str] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.rstrip()
        if len(line) < 4:
            continue
        path_text = line[3:].strip()
        if " -> " in path_text:
            path_text = path_text.split(" -> ", 1)[1].strip()
        normalized = path_text.replace("\\", "/")
        if normalized and normalized not in dirty_paths:
            dirty_paths.append(normalized)
    return dirty_paths, False


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
    if preflight_probe is not None and preflight_probe.recovery_targets:
        return "preflight_probe", _dedupe_paths(list(preflight_probe.recovery_targets))
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
    workspace_file_count = 0
    workspace_dir_count = 0
    code_file_count = 0
    git_dirty_paths: list[str] = []
    critical_workspace_paths: list[str] = []

    if root is None:
        signals.append(_signal("workspace_unavailable", "warning", "探测时工作区根目录不可用。"))
    else:
        workspace_file_count, workspace_dir_count, top_level_root_count, critical_workspace_paths, _ = (
            _scan_workspace_tree(root)
        )
        code_file_count = _count_code_files(root)
        if (root / ".git").exists():
            signals.append(_signal("git_workspace", "info", "该工作区是一个 Git 仓库。"))
        git_dirty_paths, git_probe_failed = _git_dirty_paths(root)
        if git_probe_failed:
            signals.append(_signal("git_probe_unavailable", "warning", "Git 状态探测不可用。"))
        elif git_dirty_paths:
            signals.append(
                _signal(
                    "git_dirty_workspace",
                    "warning",
                    f"执行前工作区存在 {len(git_dirty_paths)} 个未提交变更路径。",
                )
            )
            dirty_critical = [path for path in git_dirty_paths if _is_critical_path(path)]
            if dirty_critical:
                signals.append(
                    _signal(
                        "git_dirty_critical_paths",
                        "high",
                        f"检测到存在未提交变更的关键工作区路径：{', '.join(dirty_critical[:3])}。",
                    )
                )
        if workspace_file_count >= 120 or workspace_dir_count >= 40 or top_level_root_count >= 5:
            signals.append(
                _signal(
                    "workspace_tree_broad",
                    "warning",
                    "工作区结构过于庞大，需要更强的规划约束。",
                )
            )
        if code_file_count >= 80:
            signals.append(
                _signal(
                    "code_surface_large",
                    "warning",
                    f"工作区包含 {code_file_count} 个代码文件，执行面较广。",
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
        recovery_targets=_dedupe_paths(existing_paths),
        touched_roots=touched_roots,
        workspace_file_count=workspace_file_count,
        workspace_dir_count=workspace_dir_count,
        code_file_count=code_file_count,
        git_dirty_paths=git_dirty_paths,
        critical_workspace_paths=critical_workspace_paths,
        baseline_risk=baseline_risk,
        signals=signals,
        summary=(
            f"预检探测发现 {len(existing_paths)} 个已存在目标，"
            f" 工作区文件={workspace_file_count}、目录={workspace_dir_count}、代码文件={code_file_count}，"
            f" 未提交变更路径={len(git_dirty_paths)}；基线风险={baseline_risk}。"
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
        workspace_file_count, workspace_dir_count, _, current_critical_workspace_paths, _ = (
            _scan_workspace_tree(root)
        )
        current_git_dirty_paths, git_probe_failed = _git_dirty_paths(root)
        checked_paths.append(".")
        if (root / ".git").exists():
            checked_paths.append(".git")
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
            if git_probe_failed:
                signals.append(
                    _signal(
                        "git_probe_unavailable_during_recovery",
                        "warning",
                        "恢复探测无法将 Git 未提交变更路径与预检基线进行比对。",
                    )
                )
            else:
                checked_paths.extend(current_git_dirty_paths)
                baseline_dirty_paths = set(preflight_probe.git_dirty_paths)
                current_dirty_paths = set(current_git_dirty_paths)
                newly_dirty_paths = sorted(current_dirty_paths - baseline_dirty_paths)
                if newly_dirty_paths:
                    drift_severity = "high" if any(_is_critical_path(path) for path in newly_dirty_paths) else "warning"
                    signals.append(
                        _signal(
                            "dirty_workspace_drift",
                            drift_severity,
                            "相对于预检基线，工作区出现了新的未提交变更路径。",
                        )
                    )
                    if drift_severity == "high":
                        status = "blocked"
                    elif status != "blocked":
                        status = "degraded"
            if (
                status != "blocked"
                and (
                    workspace_file_count < preflight_probe.workspace_file_count
                    or workspace_dir_count < preflight_probe.workspace_dir_count
                    or len(current_critical_workspace_paths) < len(preflight_probe.critical_workspace_paths)
                )
            ):
                signals.append(
                    _signal(
                        "workspace_surface_changed",
                        "warning",
                        "相对于预检基线，工作区范围有所收缩。",
                    )
                )
                status = "degraded"
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
                    "恢复阶段发现部分任务描述中引用的工作区路径缺失。",
                )
            )
            status = "degraded"

    if not signals:
        signals.append(_signal("workspace_ready", "info", "工作区探测已完成，未发现偏移。"))
    summary = (
        f"恢复探测状态={status}，基线来源={baseline_source}；共检查 {len(dict.fromkeys(checked_paths))} 个路径，"
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
