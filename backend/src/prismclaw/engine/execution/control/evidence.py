from __future__ import annotations

import uuid
from typing import Any

from prismclaw.engine.foundation.metrics import emit
from prismclaw.schema.agent.task_runtime import EvidenceRecord

_PATH_ARG_NAMES = ("file_path", "path", "file", "filepath")


def declared_paths(call: Any) -> tuple[str, ...]:
    """从工具入参解析调用方声明的文件路径；解析失败即为空集。"""
    arguments = getattr(call, "arguments", None)
    if not isinstance(arguments, dict):
        return ()
    paths: list[str] = []
    for name in _PATH_ARG_NAMES:
        value = arguments.get(name)
        if isinstance(value, str) and value.strip():
            paths.append(value)
    return tuple(dict.fromkeys(paths))


def record_tool_evidence(
    *,
    task_id: str,
    call: Any,
    result: Any,
    before: dict[str, tuple[int, int] | None],
    after: dict[str, tuple[int, int] | None],
) -> EvidenceRecord:
    """把一次工具调用落成一条证据：只认声明路径的真实变更。"""
    tool_name = str(getattr(call, "name", "") or "unknown_tool")
    status = "error" if bool(getattr(result, "is_error", False)) else "ok"
    wrote_paths: list[str] = []
    if status == "ok":
        for path in declared_paths(call):
            observed = after.get(path)
            if observed is not None and observed != before.get(path):
                wrote_paths.append(path)
    origin = "subagent" if tool_name == "sub_agent" else "main"
    if wrote_paths:
        emit(
            "evidence.write_paths_total",
            float(len(wrote_paths)),
            kind="counter",
            labels={"origin": origin},
        )
    return EvidenceRecord(
        evidence_id=f"{task_id}:ev:{uuid.uuid4().hex[:12]}",
        task_id=task_id,
        tool_name=tool_name,
        status=status,
        wrote_paths=wrote_paths,
        origin=origin,
    )
