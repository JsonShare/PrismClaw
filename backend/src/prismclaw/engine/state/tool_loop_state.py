from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ToolLoopState:
    compaction_failures: int = 0
    total_usd_cost: float = 0.0
    per_tool_call_counts: dict[str, int] = None
    # 配对 token 列表："{tool_signature}@{result_fingerprint}"。
    # 含结果指纹后，"同调用同结果"的重复（死循环真信号）才会累计
    # streak；结果一变 token 即不同，streak 自动归 1——"结果在变则
    # 放宽"无需任何额外阈值。每回合只 append 一次（由
    # record_tool_call_observation 唯一写入）。
    recent_tool_signatures: list[str] = None
    consecutive_errors: int = 0
    # Set of tool names frozen due to loop detection (Check 4 search-loop
    # freeze). When a tool is frozen, pre_request_pipeline removes it from
    # the visible tool names so the model cannot call it again.
    # Cleared only on terminate — a frozen tool stays frozen for the rest
    # of the run (that is the point of the intervention).
    frozen_tools: set[str] = None
    # 调用签名 → 连续被冻结阻止的次数（按回合归一：同一签名每回合
    # 至多 +1）。Check 2 判定"同调用同结果重复超过 absolute_max"后
    # 写入（初始 0），下一回合 before_tool_call 命中即 block 并计数；
    # 计数达 escalate_above 才 terminate 整轮。仅在 terminate 时清除。
    # 结果变化 → token 变 → streak 归 1 → 冻结自动失效。
    frozen_call_signatures: dict[str, int] = None
    # Cumulative chars of tool output in the current tool-loop turn.
    # Used by search-loop detection (Check 4) to compute the share
    # of history consumed by tool output. Cleared alongside
    # per_tool_call_counts on non-tool turns and terminate.
    tool_output_chars_used: int = 0
    # 有界循环（bounded cycling）连续计数：签名窗口已满且独特
    # "{sig}@{fp}" 组合数 <= _CYCLING_UNIQUE_CAP 时 +1，否则归 0。
    # 捕捉"在 1-2 个调用+结果组合之间反复交替"的循环（eadc0d 类：
    # 61 次审查全部 repair、跑满 run_seconds）——这类循环的尾随
    # streak 恒为 1，Check 2 的逐字节相同连续重复阶梯够不到。
    # 结果指纹每次都变的循环不在此列（新信息 = 疑似正常推进）。
    # 写入点同样是 record_tool_call_observation（每回合恰好一次）。
    cycling_loop_streak: int = 0
    # P2 同目标重复读取检测（Check 5）的计数：target（归一化文件
    # 路径 / URL / 查询词）→ 累计读取 / 写入次数。读取 = 可提取唯一
    # 目标且结果内容达到 call_handling._READ_MIN_RESULT_CHARS 的
    # 调用；写入 = 已分类 write 类工具（bash 的 unknown_effect 按
    # 保守默认计入写入）的小结果调用。bash 多目标等歧义调用不计
    # （宁缺勿错——本检查只产 advisory，不阻断不冻结）。
    # 存在意义：Check 2 的字节指纹对「同文件不同区间」重读天然
    # 失效（每次区间/字节都不同 = 机械层面的新信息），2026-10-23
    # s:d54adf8fb4aa4742 实测 26 轮重读 L1 全过，故单独计数。
    # 生命周期与 per_tool_call_counts 一致（terminate 时清除）。
    target_read_counts: dict[str, int] = None
    target_write_counts: dict[str, int] = None
    # 归因分流计数：external 失败
    # 不进 recent_tool_signatures / consecutive_errors——外部数据源
    # 挂掉不是模型空转（科研检索高频场景，logs/mcp_server_* 实证）。
    # 同签名连续 external 失败 = 死磕单一挂掉源（换源/换参数归 0）。
    consecutive_external_errors: int = 0
    consecutive_external_same_signature: int = 0
    last_external_signature: str = ""
    # 台账不变量（吸收 Pi failToolCallsFromTruncatedMessage 纪律）：
    # 每一次工具尝试（成功/失败/截断）都确定性落账，消除"失败/截断静默
    # 跳过导致证据失真"的盲区。截断（消息被截断无 tool_result）伪造 error 入账。
    tool_attempt_events: list[dict[str, Any]] = None
    _TOOL_ATTEMPT_CAP = 128
    # 模型无视签名冻结重试达 escalate_above（此处只作嫌疑信号，
    # 不产生终态）。
    frozen_call_defied: bool = False
    # 收敛软信号一次性护栏（D1，sdd-convergence-efficiency）：
    # 完成契约机械核验无缺口时注入一次收尾 advisory（"交付已齐备，
    # 停止调用工具，给出最终答复"），置位后不再注入——advisory 是
    # 信息不是催促，模型收到后仍不停止则尊重其自治（终态只在停止时
    # 由 CompletionKernel 裁决）。
    convergence_advisory_sent: bool = False

    def __post_init__(self):
        if self.per_tool_call_counts is None:
            self.per_tool_call_counts = {}
        if self.recent_tool_signatures is None:
            self.recent_tool_signatures = []
        if self.frozen_tools is None:
            self.frozen_tools = set()
        if self.frozen_call_signatures is None:
            self.frozen_call_signatures = {}
        if self.target_read_counts is None:
            self.target_read_counts = {}
        if self.target_write_counts is None:
            self.target_write_counts = {}
        if self.tool_attempt_events is None:
            self.tool_attempt_events = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "compaction_failures": self.compaction_failures,
            "total_usd_cost": self.total_usd_cost,
            "per_tool_call_counts": self.per_tool_call_counts,
            "recent_tool_signatures": self.recent_tool_signatures,
            "consecutive_errors": self.consecutive_errors,
            "frozen_tools": sorted(self.frozen_tools),
            "frozen_call_signatures": dict(self.frozen_call_signatures),
            "tool_output_chars_used": self.tool_output_chars_used,
            "cycling_loop_streak": self.cycling_loop_streak,
            "target_read_counts": self.target_read_counts,
            "target_write_counts": self.target_write_counts,
            "consecutive_external_errors": self.consecutive_external_errors,
            "consecutive_external_same_signature": self.consecutive_external_same_signature,
            "last_external_signature": self.last_external_signature,
            "tool_attempt_events": self.tool_attempt_events,
            "frozen_call_defied": self.frozen_call_defied,
            "convergence_advisory_sent": self.convergence_advisory_sent,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolLoopState":
        frozen = set(data.get("frozen_tools") or [])
        return cls(
            compaction_failures=data.get("compaction_failures", 0),
            total_usd_cost=data.get("total_usd_cost", 0.0),
            per_tool_call_counts=data.get("per_tool_call_counts", {}),
            recent_tool_signatures=data.get("recent_tool_signatures", []),
            consecutive_errors=data.get("consecutive_errors", 0),
            frozen_tools=frozen,
            frozen_call_signatures=data.get("frozen_call_signatures") or {},
            tool_output_chars_used=data.get("tool_output_chars_used", 0),
            cycling_loop_streak=int(data.get("cycling_loop_streak", 0) or 0),
            target_read_counts=data.get("target_read_counts") or {},
            target_write_counts=data.get("target_write_counts") or {},
            consecutive_external_errors=int(data.get("consecutive_external_errors", 0) or 0),
            consecutive_external_same_signature=int(
                data.get("consecutive_external_same_signature", 0) or 0
            ),
            last_external_signature=str(data.get("last_external_signature") or ""),
            tool_attempt_events=list(data.get("tool_attempt_events") or []),
            frozen_call_defied=bool(data.get("frozen_call_defied", False)),
            convergence_advisory_sent=bool(data.get("convergence_advisory_sent", False)),
        )
