"""后台任务结束时回收 ``run/jobs/<pid>`` 磁盘目录。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from prismclaw.engine.runtime.agent.executor import RuntimeExecutor, _JobEntry


def _entry(tmp_path: Path, pid: int, *, returncode: int | None) -> _JobEntry:
    job_dir = tmp_path / ".work" / "run" / "jobs" / str(pid)
    job_dir.mkdir(parents=True)
    (job_dir / "stdout").write_text("out", encoding="utf-8")
    return _JobEntry(
        pid=pid,
        command="sleep 1",
        proc=SimpleNamespace(returncode=returncode),
        job_dir=job_dir,
        stdout_path=job_dir / "stdout",
        stderr_path=job_dir / "stderr",
    )


def test_retire_job_removes_disk_dir(tmp_path: Path) -> None:
    executor = RuntimeExecutor(workspace_root=str(tmp_path))
    entry = _entry(tmp_path, 42, returncode=None)
    executor._jobs[42] = entry

    executor._retire_job(42)

    assert 42 not in executor._jobs
    assert not entry.job_dir.exists()


def test_list_jobs_retires_completed_only(tmp_path: Path) -> None:
    executor = RuntimeExecutor(workspace_root=str(tmp_path))
    done = _entry(tmp_path, 1, returncode=0)
    running = _entry(tmp_path, 2, returncode=None)
    executor._jobs.update({1: done, 2: running})

    listed = executor.list_jobs()

    assert [item["pid"] for item in listed] == [2]
    assert not done.job_dir.exists()
    assert running.job_dir.exists()
