from __future__ import annotations

from pathlib import Path

from prismclaw.engine.security.gate.probes import (
    run_preflight_probe,
    run_recovery_probe,
)
from prismclaw.schema.agent.task_types import TaskSpec


def _write(root: Path, relative: str, content: str = "x") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _task_spec(goal: str) -> TaskSpec:
    return TaskSpec(task_id="t1", session_id="s1", goal=goal)


def _signal_codes(result) -> list[str]:
    return [signal.code for signal in result.signals]


def test_preflight_reports_existing_and_critical_paths(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml")
    _write(tmp_path, "src/main.py")

    result = run_preflight_probe(user_message="看一下 src/main.py", workspace_path=tmp_path)

    assert result.mentioned_paths == ["src/main.py"]
    assert result.existing_paths == ["src/main.py"]
    assert "pyproject.toml" in result.critical_workspace_paths
    assert result.baseline_risk == "normal"
    assert set(result.model_dump()) == {
        "probe_id",
        "workspace_root",
        "mentioned_paths",
        "existing_paths",
        "critical_workspace_paths",
        "baseline_risk",
        "signals",
        "summary",
    }


def test_preflight_marks_critical_path_high(tmp_path: Path) -> None:
    _write(tmp_path, "configs/app.yaml")

    result = run_preflight_probe(user_message="改 configs/app.yaml", workspace_path=tmp_path)

    assert result.baseline_risk == "high"
    assert "critical_path" in _signal_codes(result)


def test_preflight_detects_single_file_adjacent_to_cjk(tmp_path: Path) -> None:
    """中文紧邻的单文件名也必须被识别为关键路径。"""
    _write(tmp_path, "pyproject.toml")

    result = run_preflight_probe(user_message="修改pyproject.toml", workspace_path=tmp_path)

    assert result.mentioned_paths == ["pyproject.toml"]
    assert result.existing_paths == ["pyproject.toml"]
    assert result.baseline_risk == "high"
    assert "critical_path" in _signal_codes(result)


def test_preflight_preserves_dot_directory_paths(tmp_path: Path) -> None:
    _write(tmp_path, ".github/workflows/pages.yml")

    result = run_preflight_probe(
        user_message="更新 .github/workflows/pages.yml",
        workspace_path=tmp_path,
    )

    assert result.mentioned_paths == [".github/workflows/pages.yml"]
    assert result.existing_paths == [".github/workflows/pages.yml"]
    assert result.baseline_risk == "high"


def test_path_extraction_ignores_urls_and_versions() -> None:
    from prismclaw.engine.security.gate.probes import _extract_workspace_paths

    assert _extract_workspace_paths("见 https://github.com/a/b/blob/main/README.md") == []
    assert _extract_workspace_paths("版本 v1.2.3 与 Python 3.11") == []
    assert _extract_workspace_paths("调用 os.path.join") == []


def test_preflight_without_workspace(tmp_path: Path) -> None:
    result = run_preflight_probe(user_message="你好", workspace_path=tmp_path / "missing")

    assert result.workspace_root is None
    assert result.mentioned_paths == []
    assert result.existing_paths == []
    assert result.baseline_risk == "normal"
    assert _signal_codes(result) == ["workspace_unavailable"]


def test_preflight_ignores_git_worktree_state(tmp_path: Path) -> None:
    """风险基线不随 Git 工作区脏度变化（git 探查已删除）。"""
    _write(tmp_path, "src/main.py")
    before = run_preflight_probe(user_message="看 src/main.py", workspace_path=tmp_path)

    _write(tmp_path, ".git/HEAD", "ref: refs/heads/main\n")
    _write(tmp_path, "src/untracked.py")
    after = run_preflight_probe(user_message="看 src/main.py", workspace_path=tmp_path)

    assert before.baseline_risk == "normal"
    assert after.baseline_risk == before.baseline_risk
    assert [s.code for s in after.signals] == [s.code for s in before.signals]


def test_recovery_ready_after_clean_preflight(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml")
    _write(tmp_path, ".github/workflows/pages.yml")
    preflight = run_preflight_probe(user_message="你好", workspace_path=tmp_path)
    assert ".github/workflows/pages.yml" in preflight.critical_workspace_paths

    result = run_recovery_probe(
        task_spec=None,
        workspace_path=tmp_path,
        preflight_probe=preflight,
    )

    assert result.status == "ready"
    assert result.baseline_source == "preflight_probe"
    assert result.missing_paths == []
    assert "preflight_baseline_reused" in _signal_codes(result)


def test_recovery_degraded_when_preflight_target_missing(tmp_path: Path) -> None:
    _write(tmp_path, "src/main.py")
    preflight = run_preflight_probe(user_message="看 src/main.py", workspace_path=tmp_path)
    assert preflight.existing_paths == ["src/main.py"]

    (tmp_path / "src/main.py").unlink()
    result = run_recovery_probe(
        task_spec=None,
        workspace_path=tmp_path,
        preflight_probe=preflight,
    )

    assert result.status == "degraded"
    assert result.missing_paths == ["src/main.py"]
    assert "referenced_paths_missing" in _signal_codes(result)


def test_recovery_blocked_when_critical_path_missing(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml")
    _write(tmp_path, "src/main.py")
    preflight = run_preflight_probe(user_message="你好", workspace_path=tmp_path)
    assert preflight.critical_workspace_paths == ["pyproject.toml"]

    (tmp_path / "pyproject.toml").unlink()
    result = run_recovery_probe(
        task_spec=None,
        workspace_path=tmp_path,
        preflight_probe=preflight,
    )

    assert result.status == "blocked"
    assert result.missing_paths == ["pyproject.toml"]
    assert "critical_workspace_drift" in _signal_codes(result)


def test_recovery_blocked_without_workspace(tmp_path: Path) -> None:
    result = run_recovery_probe(
        task_spec=None,
        workspace_path=tmp_path / "missing",
        preflight_probe=None,
    )

    assert result.status == "blocked"
    assert _signal_codes(result) == ["workspace_missing"]


def test_recovery_falls_back_to_task_spec_text(tmp_path: Path) -> None:
    _write(tmp_path, "src/main.py")

    result = run_recovery_probe(
        task_spec=_task_spec("检查 src/main.py"),
        workspace_path=tmp_path,
        preflight_probe=None,
    )

    assert result.baseline_source == "task_spec_text"
    assert result.status == "ready"
    assert "task_spec_target_fallback" in _signal_codes(result)
