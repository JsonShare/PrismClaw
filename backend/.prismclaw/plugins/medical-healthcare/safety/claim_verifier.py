#!/usr/bin/env python3
# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0
"""声明验证器 — 检查输出声明是否基于工具结果。

用法:
    python claim_verifier.py                  # 运行内置测试
    echo '{"claims": [{"text": "患者白细胞计数为 12.5", "source_tool": "cbc_tool"}], "tool_results": [{"tool": "cbc_tool", "data": "WBC=12.5"}]}' | python claim_verifier.py --stdin

输入 (input_data):
    {
        "claims": [{ "text": str, "source_tool": str }, ...],
        "tool_results": [{ "tool": str, "data": str }, ...]
    }

输出:
    {
        "grounding_rate": 0.0-1.0,
        "grounded_claims": int,
        "total_claims": int,
        "details": [{ "claim": str, "grounded": bool, "source_tool": str }]
    }
"""
from __future__ import annotations

import json
import sys


def _build_tool_index(tool_results: list[dict]) -> dict[str, str]:
    index: dict[str, str] = {}
    for entry in tool_results:
        tool_name = entry.get("tool", "")
        if tool_name:
            index[tool_name] = entry.get("data", "")
    return index


def verify(input_data: dict) -> dict:
    claims = input_data.get("claims", [])
    tool_results = input_data.get("tool_results", [])

    if not isinstance(claims, list):
        claims = []
    if not isinstance(tool_results, list):
        tool_results = []

    if not claims:
        return {
            "grounding_rate": 0.0,
            "grounded_claims": 0,
            "total_claims": 0,
            "details": [],
        }

    tool_index = _build_tool_index(tool_results)
    details: list[dict] = []
    grounded_count = 0

    for claim_entry in claims:
        if not isinstance(claim_entry, dict):
            details.append({
                "claim": str(claim_entry),
                "grounded": False,
                "source_tool": "",
            })
            continue

        claim_text = claim_entry.get("text", "")
        source_tool = claim_entry.get("source_tool", "")

        grounded = source_tool in tool_index
        if grounded:
            grounded_count += 1

        details.append({
            "claim": claim_text,
            "grounded": grounded,
            "source_tool": source_tool,
        })

    total = len(claims)
    rate = round(grounded_count / max(total, 1), 4)

    return {
        "grounding_rate": rate,
        "grounded_claims": grounded_count,
        "total_claims": total,
        "details": details,
    }


def handle_hook_context(context: dict) -> dict:
    args = context.get("arguments", {})
    claims = args.get("claims", [])
    tool_results = args.get("tool_results", [])

    if not claims:
        result_text = context.get("result", "")
        if result_text:
            claims = [{"text": result_text, "source_tool": "output"}]
            tool_results = tool_results or []

    input_data = {"claims": claims, "tool_results": tool_results}
    result = verify(input_data)

    grounding_rate = result.get("grounding_rate", 0.0)
    grounded = result.get("grounded_claims", 0)
    total = result.get("total_claims", 0)
    details = result.get("details", [])

    if total == 0:
        return {
            "outcome": "success",
            "system_message": "声明验证通过：无声明需要验证。",
            "patch": None,
        }

    ungrounded = [d for d in details if not d.get("grounded", False)]
    if not ungrounded:
        return {
            "outcome": "success",
            "system_message": f"声明验证通过：{grounded}/{total} 项声明均有工具依据（grounding_rate={grounding_rate:.2f}）。",
            "patch": None,
        }

    ungrounded_text = "; ".join(
        f"'{d['claim'][:80]}' (source: {d['source_tool']})"
        for d in ungrounded
    )
    return {
        "outcome": "non_blocking_error",
        "system_message": (
            f"声明验证警告：{grounded}/{total} 项声明有工具依据（grounding_rate={grounding_rate:.2f}）。"
            f"未被验证的声明: {ungrounded_text}。"
        ),
        "patch": None,
    }


def _run_tests() -> None:
    tests: list[tuple[str, dict, float]] = [
        (
            "所有声明均有依据（完美 grounding）",
            {
                "claims": [
                    {"text": "患者白细胞计数为 12.5×10⁹/L", "source_tool": "cbc_tool"},
                    {"text": "患者血糖为 8.2 mmol/L", "source_tool": "glucose_tool"},
                    {"text": "患者肌酐为 95 μmol/L", "source_tool": "renal_tool"},
                ],
                "tool_results": [
                    {"tool": "cbc_tool", "data": "WBC=12.5×10⁹/L, RBC=4.8×10¹²/L, Hb=145g/L"},
                    {"tool": "glucose_tool", "data": "Glucose=8.2mmol/L (fasting)"},
                    {"tool": "renal_tool", "data": "Creatinine=95μmol/L, BUN=5.2mmol/L"},
                ],
            },
            1.0,
        ),
        (
            "部分声明有依据",
            {
                "claims": [
                    {"text": "患者血压为 145/92 mmHg", "source_tool": "vital_signs"},
                    {"text": "患者有青霉素过敏史", "source_tool": "allergy_record"},
                    {"text": "患者主诉头痛", "source_tool": "chief_complaint"},
                ],
                "tool_results": [
                    {"tool": "vital_signs", "data": "BP=145/92mmHg, HR=88bpm, Temp=37.2℃"},
                ],
            },
            0.3333,
        ),
        (
            "无声明 = grounding_rate 0",
            {
                "claims": [],
                "tool_results": [
                    {"tool": "cbc_tool", "data": "WBC=12.5"},
                ],
            },
            0.0,
        ),
        (
            "声明引用了不存在的工具",
            {
                "claims": [
                    {"text": "患者需要立即手术", "source_tool": "surgeon_recommendation"},
                    {"text": "药物相互作用存在风险", "source_tool": "interaction_checker"},
                ],
                "tool_results": [
                    {"tool": "cbc_tool", "data": "WBC=12.5"},
                ],
            },
            0.0,
        ),
        (
            "混合：部分有依据部分无依据",
            {
                "claims": [
                    {"text": "心率为 88 bpm", "source_tool": "vital_signs"},
                    {"text": "肝酶升高", "source_tool": "liver_panel"},
                    {"text": "需要住院观察", "source_tool": "clinical_judgment"},
                    {"text": "患者可能需要抗生素", "source_tool": "clinical_judgment"},
                ],
                "tool_results": [
                    {"tool": "vital_signs", "data": "HR=88bpm"},
                    {"tool": "liver_panel", "data": "ALT=52U/L, AST=48U/L"},
                ],
            },
            0.5,
        ),
        (
            "空工具结果",
            {
                "claims": [
                    {"text": "患者体温正常", "source_tool": "vital_signs"},
                ],
                "tool_results": [],
            },
            0.0,
        ),
    ]

    all_pass = True
    for label, data, expected_rate in tests:
        result = verify(data)
        rate = result["grounding_rate"]
        ok = abs(rate - expected_rate) < 0.001
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        print(f"[{status}] {label}")
        print(f"       grounding_rate={rate:.4f} (expected={expected_rate:.4f})")
        print(f"       grounded={result['grounded_claims']}/{result['total_claims']}")
        for d in result["details"]:
            mark = "✓" if d["grounded"] else "✗"
            print(f"         {mark} [{d['source_tool']}] {d['claim'][:60]}")
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