from __future__ import annotations

import hashlib
import json
from typing import Any

from prismclaw.engine.toolkit.registry.tool_runtime import Tool


_VALID_TOOL_CLASSES: frozenset[str] = frozenset(("read", "memory-write-safe", "write"))

# Calibrated bytes-per-token for the tool-schema JSON wire block. Two real runs
# give 4.92 and 5.05 (see ``wire_tokens_estimate``), so this is measurement, not
# a guess; revise only against fresh provider-reported ``input_tokens``.
_WIRE_BYTES_PER_TOKEN = 5


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        # SDD R-A2: (schema fingerprint, tokens) cache for wire_tokens_estimate().
        self._wire_tokens_cache: tuple[str, int] | None = None

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} already registered")
        cls = getattr(tool, "tool_class", None)
        if cls not in _VALID_TOOL_CLASSES:
            raise ValueError(
                f"tool {tool.name!r} missing or invalid tool_class "
                f"(got {cls!r}; expected one of {sorted(_VALID_TOOL_CLASSES)})"
            )
        is_mcp_imported = hasattr(tool, "server_config")
        if not is_mcp_imported and "__" in tool.name:
            raise ValueError(
                f"non-MCP tool {tool.name!r} must not contain '__' "
                f"(reserved for the LLM-API ':' → '__' rewrite output)"
            )
        if ":" in tool.name and not is_mcp_imported:
            raise ValueError(
                f"non-MCP tool {tool.name!r} must not contain ':' "
                f"(reserved for the canonical '{{server}}:{{tool}}' MCP namespace)"
            )
        if is_mcp_imported and tool.name.count(":") != 1:
            raise ValueError(
                f"MCP-imported tool {tool.name!r} must contain exactly one ':' "
                f"(canonical form is '{{server}}:{{tool}}')"
            )
        if is_mcp_imported:
            server_part = tool.name.split(":", 1)[0]
            if "__" in server_part:
                raise ValueError(
                    f"MCP-imported tool {tool.name!r} server prefix must not contain '__' "
                    f"(would collide with the LLM-API rewrite)"
                )
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """移除已注册的工具（域注册回滚用）"""
        if name in self._tools:
            del self._tools[name]

    def get(self, name: str | None) -> Tool | None:
        if not name:
            return None
        tool = self._tools.get(name)
        if tool is not None:
            return tool
        if "__" in name:
            canonical = name.replace("__", ":", 1)
            tool = self._tools.get(canonical)
            if tool is not None:
                return tool
        return None

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    @staticmethod
    def to_llm_name(name: str) -> str:
        return name.replace(":", "__") if ":" in name else name

    def list_tools(
        self,
        allowed_names: set[str] | None = None,
        *,
        subagent_depth: int | None = None,
    ) -> list[Tool]:
        _ = subagent_depth
        visible: list[Tool] = []
        for name, tool in self._tools.items():
            if allowed_names is not None and name not in allowed_names:
                continue
                    
            visible.append(tool)
            
        # Stable sort: built-ins first, MCP second, then alphabetically by name
        def sort_key(tool: Tool) -> tuple[int, str]:
            is_mcp = hasattr(tool, "server_config") or ":" in tool.name
            return (1 if is_mcp else 0, tool.name)
            
        visible.sort(key=sort_key)
        return visible

    def list_for_llm(
        self,
        allowed_names: set[str],
        *,
        subagent_depth: int | None = None,
    ) -> list[dict[str, Any]]:
        return [
            self._to_openai_function(tool)
            for tool in self.list_tools(
                allowed_names=allowed_names,
                subagent_depth=subagent_depth,
            )
        ]

    @staticmethod
    def _to_openai_function(tool: Tool) -> dict[str, Any]:
        params = tool.parameters or {"type": "object", "properties": {}}
        if "type" not in params:
            params = {"type": "object", "properties": params.get("properties", params)}
        llm_name = ToolRegistry.to_llm_name(tool.name)
        return {
            "type": "function",
            "function": {
                "name": llm_name,
                "description": tool.description,
                "parameters": params,
            },
        }

    def wire_tokens_estimate(self) -> int:
        """Token estimate of the FULL wire schema JSON, calibrated on wire bytes.

        SDD R-A2 / W13 (A-0 measurement E14): the ``tools`` parameter is a
        per-request wire cost that must enter ``compute_history_budget`` —
        previously it was invisible, making ``budget_remaining`` misleading.
        The full-registry measurement is a conservative UPPER BOUND for
        governance-filtered subsets (visible ⊆ registered), which is the safe
        direction for a budget.

        Calibration: the previous ``estimate_tokens()`` CJK heuristic
        overestimated tool schemas by ~1.8× (11,840 est. vs. ~6,450 actual for
        31 tools), which made the drift sentinel report a permanent false
        alarm (~40% of input). The unit is now serialized UTF-8 bytes ÷
        ``_WIRE_BYTES_PER_TOKEN``, calibrated against the provider's reported
        ``input_tokens`` on two real runs:

          31,720 B (29 visible tools) ↔ ~6,450 tok → 4.92 B/token
          29,811 B (25 visible tools) ↔ ~5,900 tok → 5.05 B/token

        Both land on 5.0, and the constant-drift shape (drift identical across
        turns of a run while history/per-turn varied) is what proves the
        un-accounted remainder is exactly the tools block. Schema JSON is
        ASCII-dominated, so bytes and chars coincide in practice; bytes is the
        measured unit. CJK descriptions would tokenize at ~4 B/token, i.e. this
        constant would over-estimate them — acceptable for a conservative
        budget, and visible as drift if it ever matters.

        Fingerprint-cached: any register/unregister/schema change invalidates
        via the schema-content hash, so MCP dynamic registration and refresh
        paths are covered without manual invalidation.
        """
        schemas = [self._to_openai_function(t) for t in self.list_tools()]
        if not schemas:
            return 0
        canonical = json.dumps(schemas, sort_keys=True, ensure_ascii=False)
        fingerprint = hashlib.sha1(canonical.encode("utf-8")).hexdigest()
        cached = self._wire_tokens_cache
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        tokens = len(canonical.encode("utf-8")) // _WIRE_BYTES_PER_TOKEN
        self._wire_tokens_cache = (fingerprint, tokens)
        return tokens
