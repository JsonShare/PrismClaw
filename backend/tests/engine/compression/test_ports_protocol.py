"""Phase 2 基线: ContextRuntimePorts 依赖反转协议测试。

锁定不变量（重构过程必须持续通过）：
1. ContextRuntimePorts 是 runtime_checkable Protocol
2. ExecutionRuntimePorts 实例满足 isinstance(runtime, ContextRuntimePorts)
3. HookInvocationResult 字段结构 + 字符串比较语义
4. 静态守卫：compression 层无 runtime 反向 import 到
   execution/hooks/security/task_profile/plugins（行首 `from` 锚定）
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from prismclaw.engine.compression.ports import (
    ContextRuntimePorts,
    HookInvocationResult,
)


# ──────────────────────────── 1. Protocol runtime_checkable ────────────────────────────


def test_context_runtime_ports_is_runtime_checkable():
    """ContextRuntimePorts 必须是 runtime_checkable Protocol，
    否则 isinstance 检查在运行时不可用。"""
    # runtime_checkable 装饰的 Protocol 会有 __protocol_attrs__ 或 _is_runtime_protocol
    assert hasattr(ContextRuntimePorts, "_is_runtime_protocol"), (
        "ContextRuntimePorts 必须用 @runtime_checkable 装饰"
    )


def test_context_runtime_ports_has_all_required_methods():
    """协议必须声明全部 12 个依赖反转方法。"""
    required = {
        "get_effective_context_window",
        "is_persisted_content",
        "persist_tool_result_sync",
        "persist_context_block",
        "compress_tool_result",
        "build_compaction_context",
        "notify_before_compaction",
        "notify_after_compaction",
        "invoke_compaction_hooks",
        "raise_circuit_breaker",
        "get_context_validator_chain",
        "persist_run_snapshot",
    }
    missing = [name for name in required if not hasattr(ContextRuntimePorts, name)]
    assert not missing, f"ContextRuntimePorts 缺失方法: {missing}"


# ──────────────────────────── 2. ExecutionRuntimePorts isinstance ────────────────────────────


def test_execution_runtime_ports_satisfies_protocol():
    """ExecutionRuntimePorts 实例必须通过 isinstance 检查。"""
    from prismclaw.engine.execution.loop.bootstrap import (
        ExecutionRuntimePorts,
    )

    ports = ExecutionRuntimePorts(
        hook_dispatcher=None,
        task_profile_service=None,
    )
    assert isinstance(ports, ContextRuntimePorts), (
        "ExecutionRuntimePorts 必须满足 ContextRuntimePorts 协议"
    )


def test_execution_runtime_ports_method_signatures():
    """ExecutionRuntimePorts 必须实现协议声明的全部方法。"""
    from prismclaw.engine.execution.loop.bootstrap import (
        ExecutionRuntimePorts,
    )

    required = {
        "get_effective_context_window",
        "is_persisted_content",
        "persist_tool_result_sync",
        "persist_context_block",
        "compress_tool_result",
        "build_compaction_context",
        "notify_before_compaction",
        "notify_after_compaction",
        "invoke_compaction_hooks",
        "raise_circuit_breaker",
        "get_context_validator_chain",
        "persist_run_snapshot",
    }
    missing = [name for name in required if not hasattr(ExecutionRuntimePorts, name)]
    assert not missing, f"ExecutionRuntimePorts 缺失: {missing}"


# ──────────────────────────── 3. HookInvocationResult 契约 ────────────────────────────


def test_hook_invocation_result_fields():
    """HookInvocationResult 必须有 outcome + system_message 字段。"""
    result = HookInvocationResult(outcome="SUCCESS")
    assert result.outcome == "SUCCESS"
    assert result.system_message is None


def test_hook_invocation_result_string_comparison():
    """outcome 是字符串而非枚举，必须支持 == "BLOCKING" 比较。"""
    blocking = HookInvocationResult(outcome="BLOCKING", system_message="halted")
    success = HookInvocationResult(outcome="SUCCESS")

    assert blocking.outcome == "BLOCKING"
    assert success.outcome == "SUCCESS"
    assert blocking.outcome != success.outcome


def test_hook_invocation_result_is_frozen():
    """frozen=True dataclass 不可变。"""
    result = HookInvocationResult(outcome="SUCCESS")
    with pytest.raises(Exception):
        result.outcome = "BLOCKING"  # type: ignore[misc]


# ──────────────────────────── 4. 静态守卫：compression 层无 runtime 反向 import ────────────────────────────


def _compression_root() -> Path:
    """定位 compression 包根目录。"""
    import prismclaw.engine.compression as comp_pkg

    return Path(comp_pkg.__file__).parent


_FORBIDDEN_PATTERN = re.compile(
    r"^from prismclaw\.engine\.(execution|hooks|security|task_profile|plugins)"
)


def _scan_runtime_reverse_imports(root: Path) -> list[tuple[Path, int, str]]:
    """扫描 compression 目录，返回行首 `from prismclaw.engine.<forbidden>` 的命中。

    TYPE_CHECKING 块内的 import 有缩进，不会被 `^from` 锚定命中。
    Phase 3 已扁平化所有子包（state/ 已移除），不再有特例目录。
    """
    hits: list[tuple[Path, int, str]] = []
    for py_file in root.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        try:
            text = py_file.read_text(encoding="utf-8")
        except Exception:
            continue
        for idx, line in enumerate(text.splitlines(), start=1):
            if _FORBIDDEN_PATTERN.match(line):
                hits.append((py_file, idx, line))
    return hits


def test_no_runtime_reverse_imports_in_compression():
    """compression 层禁止 runtime 反向 import 到 execution/hooks/security/task_profile/plugins。

    依赖方向必须单向：schema ← state ← compression.ports ← compression.* ← execution。
    """
    hits = _scan_runtime_reverse_imports(_compression_root())
    if hits:
        details = "\n".join(
            f"{p.relative_to(_compression_root())}:{lineno}: {line}"
            for p, lineno, line in hits
        )
        pytest.fail(
            "compression 层存在 runtime 反向 import（违反 Phase 2 依赖反转）:\n" + details
        )
