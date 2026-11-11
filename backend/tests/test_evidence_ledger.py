"""产物事实与证据台账的单元契约。

* ``ArtifactScanner`` 是产物事实的唯一来源，只做 stat / 枚举。
* ``record_tool_evidence`` 只认「成功 + 声明路径真实变更」，不看工具名类别。
"""
from __future__ import annotations

import os
from pathlib import Path

from prismclaw.engine.execution.control.artifacts import ArtifactScanner, Scope
from prismclaw.engine.execution.control.evidence import declared_paths, record_tool_evidence


class _Call:
    def __init__(self, call_id: str, name: str, arguments: dict | None) -> None:
        self.call_id = call_id
        self.name = name
        self.arguments = arguments


class _Result:
    def __init__(self, tool_call_id: str, is_error: bool = False) -> None:
        self.tool_call_id = tool_call_id
        self.is_error = is_error


def _bump(path: Path) -> None:
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


def test_stat_paths_detects_change_and_missing(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    target = workspace / "out.md"
    target.write_text("v1", encoding="utf-8")

    scanner = ArtifactScanner(workspace)
    before = scanner.stat_paths(("out.md", "absent.md"))
    assert before["out.md"] is not None
    assert before["absent.md"] is None

    _bump(target)
    after = scanner.stat_paths(("out.md", "absent.md"))
    assert after["out.md"] != before["out.md"]
    assert after["absent.md"] is None


def test_stat_paths_resolves_relative_against_workspace_root(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "rel.md").write_text("x", encoding="utf-8")

    scanner = ArtifactScanner(workspace)
    snapshot = scanner.stat_paths(("rel.md",))
    assert snapshot["rel.md"] is not None


def test_record_tool_evidence_counts_real_write(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    target = workspace / "out.md"

    scanner = ArtifactScanner(workspace)
    call = _Call("c1", "write", {"file_path": "out.md"})
    before = scanner.stat_paths(declared_paths(call))
    target.write_text("v1", encoding="utf-8")
    after = scanner.stat_paths(declared_paths(call))

    record = record_tool_evidence(
        task_id="t1", call=call, result=_Result("c1"), before=before, after=after
    )
    assert record.wrote_paths == ["out.md"]
    assert record.status == "ok"
    assert record.origin == "main"


def test_record_tool_evidence_unchanged_file_is_not_a_write(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    target = workspace / "out.md"
    target.write_text("v1", encoding="utf-8")

    scanner = ArtifactScanner(workspace)
    call = _Call("c1", "write", {"file_path": "out.md"})
    before = scanner.stat_paths(declared_paths(call))
    after = scanner.stat_paths(declared_paths(call))

    record = record_tool_evidence(
        task_id="t1", call=call, result=_Result("c1"), before=before, after=after
    )
    assert record.wrote_paths == []


def test_record_tool_evidence_read_and_error_have_no_writes(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    target = workspace / "out.md"
    target.write_text("v1", encoding="utf-8")

    scanner = ArtifactScanner(workspace)
    read_call = _Call("c1", "read", {"file_path": "out.md"})
    before = scanner.stat_paths(declared_paths(read_call))
    after = scanner.stat_paths(declared_paths(read_call))

    read_record = record_tool_evidence(
        task_id="t1", call=read_call, result=_Result("c1"), before=before, after=after
    )
    assert read_record.wrote_paths == []

    target.write_text("v2", encoding="utf-8")
    changed_after = scanner.stat_paths(declared_paths(read_call))
    error_record = record_tool_evidence(
        task_id="t1",
        call=read_call,
        result=_Result("c1", is_error=True),
        before=before,
        after=changed_after,
    )
    assert error_record.status == "error"
    assert error_record.wrote_paths == []


def test_record_tool_evidence_sub_agent_origin(tmp_path: Path):
    call = _Call("c1", "sub_agent", None)
    record = record_tool_evidence(
        task_id="t1", call=call, result=_Result("c1"), before={}, after={}
    )
    assert record.origin == "subagent"
    assert record.wrote_paths == []


def test_scan_outputs_scope_keys_are_root_relative(tmp_path: Path):
    workspace = tmp_path / "ws"
    outputs = workspace / "outputs"
    outputs.mkdir(parents=True)
    (outputs / "x.md").write_text("x", encoding="utf-8")
    (workspace / "root.md").write_text("root", encoding="utf-8")

    scanner = ArtifactScanner(workspace)
    snapshot = scanner.scan(Scope(kind="workspace", root=str(workspace), prefixes=("outputs",)))
    assert set(snapshot) == {"outputs/x.md"}
