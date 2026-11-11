"""同目标重复读取防护（P0 文案 + P1 直方图 + P2 Check 5）单测。

锁定 2026-10-23 s:d54adf8fb4aa4742 失败模式（26 轮同文件不同区间
重读，除逐调用签名外无任何早期提示）的修复契约：

* extract_call_target：参数形态 / bash 单路径 / 多路径弃权 / 无目标
* record_target_observations：读（大结果）/ 写（write 类小结果）/
  不计（read 类小结果、歧义多目标、无目标）三态 + 全批次覆盖
* Check 5：K 次读取 + 零写入 → advisory；有写入不触发；阈值 0 禁用；
  搜索类工具归 Check 4；重提醒节奏 K / K+3；他目标不触发
* ToolLoopState 计数器 checkpoint 往返
"""
from __future__ import annotations

from types import SimpleNamespace

from prismclaw.engine.execution.dispatch.call_handling import (
    evaluate_tool_loop_policies,
    extract_call_target,
    record_target_observations,
    tool_result_char_count,
)
from prismclaw.engine.state.tool_loop_state import ToolLoopState
from prismclaw.schema.config import RunLoopTuningConfig


# ---------------------------------------------------------------------------
# Fakes / helpers
# ---------------------------------------------------------------------------

class _Registry:
    """最小 registry 桩：name → tool_class（resolve_tool_class 契约）。"""

    def __init__(self, classes: dict[str, str] | None = None):
        self._classes = classes or {}

    def get(self, name: str):
        cls = self._classes.get(name)
        return SimpleNamespace(tool_class=cls) if cls else None


_REG = _Registry({"read": "read", "write": "write", "grep": "read"})


def _result(text: str, call_id: str = "c1", *, is_error: bool = False):
    return SimpleNamespace(
        tool_call_id=call_id,
        content=[SimpleNamespace(text=text)],
        is_error=is_error,
    )


def _call(name: str, args: dict, call_id: str = "c1"):
    return SimpleNamespace(name=name, arguments=args, call_id=call_id)


def _cfg(**overrides) -> RunLoopTuningConfig:
    cfg = RunLoopTuningConfig()
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def _big(n: int = 900) -> str:
    return "x" * n


# ---------------------------------------------------------------------------
# extract_call_target
# ---------------------------------------------------------------------------

class TestExtractCallTarget:
    def test_path_arg_forms(self):
        assert extract_call_target("read", {"file_path": "/a/b.md"}) == "/a/b.md"
        assert extract_call_target("ls", {"path": "/a"}) == "/a"
        assert extract_call_target("fetch", {"url": "https://x.dev/y"}) == "https://x.dev/y"
        assert extract_call_target("search", {"query": "液压油 黏度"}) == "液压油 黏度"

    def test_file_path_wins_over_path(self):
        assert extract_call_target("t", {"path": "/p", "file_path": "/f"}) == "/f"

    def test_bash_single_absolute_path(self):
        got = extract_call_target(
            "bash", {"command": "sed -n '63,19x' /ws/mining_work/a.md"}
        )
        assert got == "/ws/mining_work/a.md"

    def test_bash_single_relative_path(self):
        got = extract_call_target("bash", {"command": "cat mining_work/a.md"})
        assert got == "mining_work/a.md"

    def test_bash_multi_path_abstains(self):
        # cp/mv 类多目标调用猜错比不猜更糟 → None
        assert extract_call_target("bash", {"command": "cp a.md b.md"}) is None

    def test_no_target_forms(self):
        assert extract_call_target("read", {}) is None
        assert extract_call_target("bash", {"command": "echo hello"}) is None
        assert extract_call_target("bash", {"command": "ls"}) is None

    def test_non_dict_args(self):
        assert extract_call_target("read", None) is None


# ---------------------------------------------------------------------------
# tool_result_char_count
# ---------------------------------------------------------------------------

class TestResultCharCount:
    def test_none_and_blocks(self):
        assert tool_result_char_count(None) == 0
        assert tool_result_char_count(_result("abc")) == 3
        assert tool_result_char_count(
            SimpleNamespace(content=[SimpleNamespace(text="ab"), SimpleNamespace(text="cde")])
        ) == 5


# ---------------------------------------------------------------------------
# record_target_observations
# ---------------------------------------------------------------------------

class TestRecordTargetObservations:
    def test_big_result_counts_as_read(self):
        ts = ToolLoopState()
        record_target_observations(
            ts,
            [_call("read", {"file_path": "/a.md"}, "c1")],
            [_result(_big(), "c1")],
            tool_registry=_REG,
        )
        assert ts.target_read_counts == {"/a.md": 1}
        assert ts.target_write_counts == {}

    def test_bash_big_result_counts_as_read(self):
        # 失败模式本体：bash + sed 大输出 = 读取（class 恒 write 不误判）
        ts = ToolLoopState()
        record_target_observations(
            ts,
            [_call("bash", {"command": "sed -n '63,19x' /ws/a.md"}, "c1")],
            [_result(_big(), "c1")],
            tool_registry=_REG,
        )
        assert ts.target_read_counts == {"/ws/a.md": 1}
        assert ts.target_write_counts == {}

    def test_small_result_write_class_counts_as_write(self):
        ts = ToolLoopState()
        record_target_observations(
            ts,
            [_call("write", {"file_path": "/a.md", "content": "x"}, "c1")],
            [_result("ok", "c1")],
            tool_registry=_REG,
        )
        assert ts.target_read_counts == {}
        assert ts.target_write_counts == {"/a.md": 1}

    def test_small_result_read_class_not_counted(self):
        # 小文件读取：不计读也不计写（宁漏勿误——同目标预警只作提示）
        ts = ToolLoopState()
        record_target_observations(
            ts,
            [_call("read", {"file_path": "/small.md"}, "c1")],
            [_result("tiny", "c1")],
            tool_registry=_REG,
        )
        assert ts.target_read_counts == {}
        assert ts.target_write_counts == {}

    def test_ambiguous_target_skipped(self):
        ts = ToolLoopState()
        record_target_observations(
            ts,
            [_call("bash", {"command": "cp a.md b.md"}, "c1")],
            [_result(_big(), "c1")],
            tool_registry=_REG,
        )
        assert ts.target_read_counts == {}
        assert ts.target_write_counts == {}

    def test_all_calls_in_batch_counted(self):
        # 与 record_tool_call_observation 的分工：全批次覆盖，
        # 平行调用漏计会削弱灵敏度
        ts = ToolLoopState()
        record_target_observations(
            ts,
            [
                _call("read", {"file_path": "/a.md"}, "c1"),
                _call("read", {"file_path": "/b.md"}, "c2"),
                _call("read", {"file_path": "/a.md"}, "c3"),
            ],
            [_result(_big(), "c1"), _result(_big(), "c2"), _result(_big(), "c3")],
            tool_registry=_REG,
        )
        assert ts.target_read_counts == {"/a.md": 2, "/b.md": 1}

    def test_results_matched_by_call_id(self):
        ts = ToolLoopState()
        record_target_observations(
            ts,
            [_call("read", {"file_path": "/a.md"}, "c1")],
            [_result("tiny", "cOTHER")],
            tool_registry=_REG,
        )
        # 配对不到结果 → 0 字符 → 不计（不拿别人的结果凑数）
        assert ts.target_read_counts == {}
        assert ts.target_write_counts == {}

    def test_empty_batch_noop(self):
        ts = ToolLoopState()
        record_target_observations(ts, [], [], tool_registry=_REG)
        assert ts.target_read_counts == {}


# ---------------------------------------------------------------------------
# Check 5：同目标重复读取早期预警
# ---------------------------------------------------------------------------

class TestCheck5SameTargetReread:
    def _state_with_reads(self, target: str, n: int, writes: int = 0) -> ToolLoopState:
        ts = ToolLoopState()
        ts.target_read_counts[target] = n
        if writes:
            ts.target_write_counts[target] = writes
        return ts

    def test_fires_at_threshold(self):
        ts = self._state_with_reads("/ws/a.md", 4)
        out = evaluate_tool_loop_policies(
            state=ts,
            config=_cfg(same_target_read_warn_at=4),
            tool_name="read",
            tool_target="/ws/a.md",
        )
        assert out.action == "warn"
        assert "/ws/a.md" in out.guidance
        assert "4 次" in out.guidance

    def test_silent_below_threshold(self):
        ts = self._state_with_reads("/ws/a.md", 3)
        out = evaluate_tool_loop_policies(
            state=ts,
            config=_cfg(same_target_read_warn_at=4),
            tool_name="read",
            tool_target="/ws/a.md",
        )
        assert out.action == "proceed"

    def test_silent_when_target_was_written(self):
        # 读后编辑再读是合法模式——写计数存在即不预警
        ts = self._state_with_reads("/ws/a.md", 6, writes=1)
        out = evaluate_tool_loop_policies(
            state=ts,
            config=_cfg(same_target_read_warn_at=4),
            tool_name="read",
            tool_target="/ws/a.md",
        )
        assert out.action == "proceed"

    def test_renudge_cadence_k_and_k_plus_3(self):
        for n, expected in ((4, "warn"), (5, "proceed"), (6, "proceed"), (7, "warn")):
            ts = self._state_with_reads("/ws/a.md", n)
            out = evaluate_tool_loop_policies(
                state=ts,
                config=_cfg(same_target_read_warn_at=4),
                tool_name="read",
                tool_target="/ws/a.md",
            )
            assert out.action == expected, f"reads={n}"

    def test_disabled_by_zero_threshold(self):
        ts = self._state_with_reads("/ws/a.md", 99)
        out = evaluate_tool_loop_policies(
            state=ts,
            config=_cfg(same_target_read_warn_at=0),
            tool_name="read",
            tool_target="/ws/a.md",
        )
        assert out.action == "proceed"

    def test_search_intent_tools_belong_to_check4(self):
        # 重复同一查询由 Check 4 计价，Check 5 跳过避免双重预警
        ts = self._state_with_reads("液压油 黏度", 10)
        out = evaluate_tool_loop_policies(
            state=ts,
            config=_cfg(same_target_read_warn_at=4),
            tool_name="web-search-mcp__search",
            tool_target="液压油 黏度",
        )
        assert out.action == "proceed"

    def test_other_target_not_triggered(self):
        ts = self._state_with_reads("/ws/a.md", 9)
        out = evaluate_tool_loop_policies(
            state=ts,
            config=_cfg(same_target_read_warn_at=4),
            tool_name="read",
            tool_target="/ws/other.md",
        )
        assert out.action == "proceed"

    def test_no_target_skips(self):
        ts = self._state_with_reads("/ws/a.md", 9)
        out = evaluate_tool_loop_policies(
            state=ts,
            config=_cfg(same_target_read_warn_at=4),
            tool_name="read",
            tool_target=None,
        )
        assert out.action == "proceed"


# ---------------------------------------------------------------------------
# ToolLoopState 持久化
# ---------------------------------------------------------------------------

class TestToolLoopStateRoundtrip:
    def test_target_counters_roundtrip(self):
        ts = ToolLoopState()
        ts.target_read_counts["/a.md"] = 5
        ts.target_write_counts["/b.md"] = 1
        restored = ToolLoopState.from_dict(ts.to_dict())
        assert restored.target_read_counts == {"/a.md": 5}
        assert restored.target_write_counts == {"/b.md": 1}

    def test_legacy_dict_without_counters(self):
        # 旧 checkpoint 无新键 → 默认空 dict，不炸
        restored = ToolLoopState.from_dict({"per_tool_call_counts": {}})
        assert restored.target_read_counts == {}
        assert restored.target_write_counts == {}
