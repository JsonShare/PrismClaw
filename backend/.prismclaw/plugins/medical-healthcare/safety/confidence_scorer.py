#!/usr/bin/env python3
# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0
"""置信度评分器 — 基于证据量化输出的置信度。

用法:
    python confidence_scorer.py                  # 运行内置测试
    echo '{"tools_used": 3, "data_sources": 5, "hedging_phrases": ["可能", "也许"], "tool_errors": 0, "total_tool_calls": 5}' | python confidence_scorer.py --stdin

输入 (input_data):
    {
        "tools_used": int,        -- 已使用的工具数量
        "data_sources": int,      -- 数据来源的数量
        "hedging_phrases": [str], -- 响应中的模糊表述列表
        "tool_errors": int,       -- 工具错误的数量
        "total_tool_calls": int   -- 工具调用的总数
    }

输出:
    { "overall": 0.0-1.0,
      "factors": {
          "tools_used": 0.0-1.0,
          "data_richness": 0.0-1.0,
          "response_hedging": 0.0-1.0,
          "tool_error_rate": 0.0-1.0
      } }
"""
from __future__ import annotations

import json
import sys

HEDGING_PATTERNS: list[str] = [
    "可能", "也许", "大概", "或许",
    "maybe", "perhaps", "likely", "unlikely",
    "不确定", "不明确", "尚不清楚", "需要进一步",
    "suggest", "appears to", "seems to", "possibly",
    "据推测", "据认为", "据估计",
]


def _score_tools_used(tools_used: int, total_tool_calls: int) -> float:
    if total_tool_calls <= 0:
        return 0.0
    if tools_used <= 0:
        return 0.0
    ratio = min(tools_used / max(total_tool_calls, 1), 1.0)
    if ratio >= 0.8:
        return 1.0
    if ratio >= 0.5:
        return 0.7
    if ratio >= 0.3:
        return 0.5
    if ratio > 0:
        return 0.2
    return 0.0


def _score_data_richness(data_sources: int) -> float:
    if data_sources <= 0:
        return 0.0
    if data_sources >= 10:
        return 1.0
    if data_sources >= 5:
        return 0.8
    if data_sources >= 3:
        return 0.6
    if data_sources >= 1:
        return 0.4
    return 0.0


def _score_hedging(hedging_phrases: list[str]) -> float:
    if not hedging_phrases:
        return 1.0
    hit_count = 0
    for phrase in hedging_phrases:
        p = phrase.strip().lower()
        for pattern in HEDGING_PATTERNS:
            if pattern.lower() in p:
                hit_count += 1
                break
    if hit_count == 0:
        return 1.0
    if hit_count <= 1:
        return 0.7
    if hit_count <= 3:
        return 0.4
    return 0.1


def _score_tool_error_rate(tool_errors: int, total_tool_calls: int) -> float:
    if total_tool_calls <= 0:
        return 0.5
    error_rate = tool_errors / total_tool_calls
    if error_rate == 0.0:
        return 1.0
    if error_rate <= 0.1:
        return 0.5
    if error_rate <= 0.25:
        return 0.3
    if error_rate <= 0.5:
        return 0.1
    return 0.0


def verify(input_data: dict) -> dict:
    tools_used = input_data.get("tools_used", 0)
    data_sources = input_data.get("data_sources", 0)
    hedging_phrases = input_data.get("hedging_phrases", [])
    tool_errors = input_data.get("tool_errors", 0)
    total_tool_calls = input_data.get("total_tool_calls", 0)

    if not isinstance(tools_used, int):
        tools_used = 0
    if not isinstance(data_sources, int):
        data_sources = 0
    if not isinstance(hedging_phrases, list):
        hedging_phrases = []
    if not isinstance(tool_errors, int):
        tool_errors = 0
    if not isinstance(total_tool_calls, int):
        total_tool_calls = 0
    if total_tool_calls < 0:
        total_tool_calls = 0

    f_tools = _score_tools_used(tools_used, total_tool_calls)
    f_data = _score_data_richness(data_sources)
    f_hedging = _score_hedging(hedging_phrases)
    f_error = _score_tool_error_rate(tool_errors, total_tool_calls)

    overall = round(
        0.25 * f_tools + 0.25 * f_data + 0.15 * f_hedging + 0.35 * f_error,
        4,
    )

    overall = max(0.0, min(1.0, overall))

    return {
        "overall": overall,
        "factors": {
            "tools_used": round(f_tools, 4),
            "data_richness": round(f_data, 4),
            "response_hedging": round(f_hedging, 4),
            "tool_error_rate": round(f_error, 4),
        },
    }


def handle_hook_context(context: dict) -> dict:
    meta = context.get("meta", {})
    args = context.get("arguments", {})

    tools_used = meta.get("tools_used", args.get("tools_used", 0))
    data_sources = meta.get("data_sources", args.get("data_sources", 0))
    hedging_phrases = meta.get("hedging_phrases", args.get("hedging_phrases", []))
    tool_errors = meta.get("tool_errors", args.get("tool_errors", 0))
    total_tool_calls = meta.get("total_tool_calls", args.get("total_tool_calls", 0))

    input_data = {
        "tools_used": tools_used,
        "data_sources": data_sources,
        "hedging_phrases": hedging_phrases,
        "tool_errors": tool_errors,
        "total_tool_calls": total_tool_calls,
    }
    result = verify(input_data)

    overall = result.get("overall", 0.0)
    factors = result.get("factors", {})

    if overall >= 0.7:
        return {
            "outcome": "success",
            "system_message": f"置信度评分通过：overall={overall:.2f}。",
            "patch": None,
        }

    if overall >= 0.35:
        return {
            "outcome": "non_blocking_error",
            "system_message": (
                f"置信度评分警告：overall={overall:.2f}。"
                f"factors: tools_used={factors.get('tools_used', 0):.2f},"
                f"data_richness={factors.get('data_richness', 0):.2f},"
                f"response_hedging={factors.get('response_hedging', 0):.2f},"
                f"tool_error_rate={factors.get('tool_error_rate', 0):.2f}。"
            ),
            "patch": None,
        }

    return {
        "outcome": "non_blocking_error",
        "system_message": (
            f"置信度评分较低：overall={overall:.2f}。"
            f"factors: tools_used={factors.get('tools_used', 0):.2f},"
            f"data_richness={factors.get('data_richness', 0):.2f},"
            f"response_hedging={factors.get('response_hedging', 0):.2f},"
            f"tool_error_rate={factors.get('tool_error_rate', 0):.2f}。"
            f"建议补充更多工具调用和数据来源以提高置信度。"
        ),
        "patch": None,
    }


def _run_tests() -> None:
    tests: list[tuple[str, dict, str]] = [
        (
            "高质量响应（充分工具、丰富数据、无模糊表述、零错误）",
            {
                "tools_used": 5,
                "data_sources": 12,
                "hedging_phrases": [],
                "tool_errors": 0,
                "total_tool_calls": 5,
            },
            "HIGH",
        ),
        (
            "中等质量（一些工具、适量数据、少量模糊表述、少量错误）",
            {
                "tools_used": 2,
                "data_sources": 3,
                "hedging_phrases": ["可能", "也许"],
                "tool_errors": 1,
                "total_tool_calls": 4,
            },
            "MEDIUM",
        ),
        (
            "低质量（无工具、无数据、大量模糊表述）",
            {
                "tools_used": 0,
                "data_sources": 0,
                "hedging_phrases": ["可能", "也许", "大概", "或许"],
                "tool_errors": 0,
                "total_tool_calls": 0,
            },
            "LOW",
        ),
        (
            "高错误率（大量工具失败）",
            {
                "tools_used": 2,
                "data_sources": 3,
                "hedging_phrases": ["可能", "也许"],
                "tool_errors": 4,
                "total_tool_calls": 5,
            },
            "LOW",
        ),
        (
            "单一数据源 + 工具错误",
            {
                "tools_used": 2,
                "data_sources": 1,
                "hedging_phrases": ["据推测"],
                "tool_errors": 1,
                "total_tool_calls": 3,
            },
            "MEDIUM",
        ),
    ]

    all_pass = True
    for label, data, tier in tests:
        result = verify(data)
        overall = result["overall"]
        if tier == "HIGH":
            ok = overall >= 0.7
        elif tier == "MEDIUM":
            ok = 0.35 <= overall < 0.75
        else:
            ok = overall < 0.4
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        print(f"[{status}] {label}")
        print(f"       overall={overall:.4f}, tier={tier}")
        factors = result["factors"]
        for k, v in factors.items():
            print(f"         {k}: {v:.4f}")
    print()
    print("ALL TESTS PASSED" if all_pass else "SOME TESTS FAILED")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--stdin":
        raw = sys.stdin.read()
        context = json.loads(raw)
        result = handle_hook_context(context)
        print(json.dumps(result, ensure_ascii=False))
    else:
        _run_tests()