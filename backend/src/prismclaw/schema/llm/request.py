from __future__ import annotations

"""LLM request data contract — PackedLLMRequest.

Moved from core.agent.request_packing to contracts so that engine-external
modules (dream, memory, curator, etc.) can import this data class without
coupling to the agent layer.
"""

from dataclasses import dataclass
from typing import Any

from prismclaw.schema.agent.data_types import CacheStrategySnapshot


@dataclass
class PackedLLMRequest:
    """LLM 请求数据封装——从 agent/request_packing 提取到 contracts。"""

    messages: list[dict[str, Any]]
    system: str | list[dict[str, Any]]
    context_payload: dict[str, Any] | None
    cache_strategy: CacheStrategySnapshot
    context_payload_tokens: int = 0
    # 可选单调用超时（秒）：由调用方显式指定；为 None 时核心沿用 120s 全局兜底。
    # 科研挖掘等大上下文场景经此单独提高超时，不影响其他域默认行为（向后兼容）。
    timeout_s: float | None = None
    # 请求级缓存路由键来源（D2/F5）：仅主链路 packing 透传；评审/摘要类
    # 辅助请求保持 None——不携带路由键即不与主缓存前缀粘连（pi P-7）。
    session_id: str | None = None
    # 输入预算（token）：由打包方用 resolve_runtime_zones 填充（ratio=A_eff）。
    # client 以此为 max_tokens 钳制基准；None 时回退模型 context_window 推导。
    input_budget: int | None = None


__all__ = ["PackedLLMRequest"]
