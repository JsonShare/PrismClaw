#!/usr/bin/env python3
# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0
"""过敏安全验证器 — 将患者过敏史与用药进行交叉比对。

用法:
    python allergy_safety_verifier.py                  # 运行内置测试
    echo '{"allergies": ["penicillin"], "medications": ["amoxicillin"]}' | python allergy_safety_verifier.py --stdin

输入 (input_data):
    {
        "allergies": [str, ...],
        "medications": [str, ...]
    }

输出:
    { "passed": bool, "flags": [{ "severity": "DIRECT|CLASS|CROSS-REACTIVE",
                                    "allergy": str,
                                    "medication": str,
                                    "class_name": str }] }
"""
from __future__ import annotations

import json
import sys

ALLERGY_DRUG_CLASS_MAP: dict[str, dict] = {
    "penicillin": {
        "class_name": "青霉素类",
        "class_drugs": [
            "penicillin", "amoxicillin", "ampicillin", "augmentin",
            "piperacillin", "ticarcillin", "mezlocillin",
        ],
        "cross_reactive_classes": ["cephalosporins"],
    },
    "cephalosporins": {
        "class_name": "头孢菌素类",
        "class_drugs": [
            "cephalexin", "cefuroxime", "ceftriaxone", "cefazolin",
            "ciprofloxacin", "moxifloxacin", "levofloxacin",
        ],
        "cross_reactive_classes": ["penicillin"],
    },
    "sulfa drugs": {
        "class_name": "磺胺类",
        "class_drugs": [
            "sulfamethoxazole", "trimethoprim-sulfamethoxazole", "sulfadiazine",
            "sulfasalazine", "sulfacetamide", "sulfapyridine",
        ],
        "cross_reactive_classes": [],
    },
    "aspirin": {
        "class_name": "NSAIDs（水杨酸类）",
        "class_drugs": [
            "aspirin", "ibuprofen", "naproxen", "diclofenac",
            "ketorolac", "indomethacin", "meloxicam", "piroxicam",
            "celecoxib", "etoricoxib", "lornoxicam",
        ],
        "cross_reactive_classes": [],
    },
    "nsaids": {
        "class_name": "非甾体抗炎药 (NSAIDs)",
        "class_drugs": [
            "aspirin", "ibuprofen", "naproxen", "diclofenac",
            "ketorolac", "indomethacin", "meloxicam", "piroxicam",
            "celecoxib", "etoricoxib", "lornoxicam",
        ],
        "cross_reactive_classes": [],
    },
    "codeine": {
        "class_name": "阿片类镇痛药",
        "class_drugs": [
            "codeine", "morphine", "hydrocodone", "oxycodone",
            "fentanyl", "tramadol", "buprenorphine", "methadone",
            "hydromorphone", "oxymorphone",
        ],
        "cross_reactive_classes": [],
    },
    "contrast dye": {
        "class_name": "碘造影剂",
        "class_drugs": [
            "contrast dye", "iohexol", "iopamidol", "iohexol",
            "diatrizoate", "iothalamate", "metrizamide",
        ],
        "cross_reactive_classes": [],
    },
    "shellfish": {
        "class_name": "贝类/碘过敏",
        "class_drugs": [
            "contrast dye", "iohexol", "iopamidol", "iohexol",
        ],
        "cross_reactive_classes": [],
    },
    "latex": {
        "class_name": "乳胶过敏",
        "class_drugs": [
            "latex", "rubber", "natural rubber",
        ],
        "cross_reactive_classes": [],
    },
    "peanuts": {
        "class_name": "坚果过敏",
        "class_drugs": [
            "peanut", "peanuts", "peanut oil", "arachis oil",
        ],
        "cross_reactive_classes": [],
    },
    "eggs": {
        "class_name": "鸡蛋过敏",
        "class_drugs": [
            "egg", "eggs", "egg albumin",
            "influenza vaccine", "flu vaccine", "yellow fever vaccine",
            "rabies vaccine",
        ],
        "cross_reactive_classes": [],
    },
    "tetracycline": {
        "class_name": "四环素类",
        "class_drugs": [
            "tetracycline", "doxycycline", "minocycline",
            "oxytetracycline", "demeclocycline", "tigecycline",
        ],
        "cross_reactive_classes": [],
    },
    "macrolides": {
        "class_name": "大环内酯类",
        "class_drugs": [
            "erythromycin", "azithromycin", "clarithromycin",
            "telithromycin", "dirithromycin", "troleandomycin",
        ],
        "cross_reactive_classes": [],
    },
    "fluoroquinolones": {
        "class_name": "氟喹诺酮类",
        "class_drugs": [
            "ciprofloxacin", "levofloxacin", "moxifloxacin",
            "ofloxacin", "norfloxacin", "gatifloxacin",
            "enoxacin", "lomefloxacin", "sparfloxacin",
        ],
        "cross_reactive_classes": [],
    },
    "vancomycin": {
        "class_name": "糖肽类抗生素",
        "class_drugs": [
            "vancomycin", "teicoplanin", "dalbavancin",
            "oritavancin",
        ],
        "cross_reactive_classes": [],
    },
    "sulfonamides": {
        "class_name": "磺胺类药物",
        "class_drugs": [
            "sulfamethoxazole", "sulfadiazine", "sulfasalazine",
            "sulfacetamide", "sulfapyridine",
            "trimethoprim-sulfamethoxazole",
        ],
        "cross_reactive_classes": [],
    },
    "penicillin allergy": {
        "class_name": "青霉素类",
        "class_drugs": [
            "penicillin", "amoxicillin", "ampicillin", "augmentin",
            "piperacillin", "ticarcillin", "mezlocillin",
        ],
        "cross_reactive_classes": ["cephalosporins"],
    },
    "sulfa allergy": {
        "class_name": "磺胺类",
        "class_drugs": [
            "sulfamethoxazole", "trimethoprim-sulfamethoxazole",
            "sulfadiazine", "sulfasalazine", "sulfacetamide",
        ],
        "cross_reactive_classes": [],
    },
    "aspirin allergy": {
        "class_name": "NSAIDs（水杨酸类）",
        "class_drugs": [
            "aspirin", "ibuprofen", "naproxen", "diclofenac",
            "celecoxib", "etoricoxib",
        ],
        "cross_reactive_classes": [],
    },
    "nsaid allergy": {
        "class_name": "非甾体抗炎药 (NSAIDs)",
        "class_drugs": [
            "aspirin", "ibuprofen", "naproxen", "diclofenac",
            "celecoxib", "etoricoxib", "meloxicam", "piroxicam",
        ],
        "cross_reactive_classes": [],
    },
}


def _normalize(name: str) -> str:
    return name.strip().lower()


def _resolve_class(allergy: str) -> str | None:
    a = _normalize(allergy)
    entry = ALLERGY_DRUG_CLASS_MAP.get(a)
    if entry:
        return entry["class_name"]
    return None


def _resolve_primary_key(term: str) -> str | None:
    term_norm = _normalize(term)
    for key, entry in ALLERGY_DRUG_CLASS_MAP.items():
        if term_norm == key or any(
            term_norm == _normalize(d) for d in entry["class_drugs"]
        ):
            return key
    return None


def _find_cross_reactive_for_allergy(
    allergy_entry: dict, allergy: str, medications: list[str], seen: set[tuple]
) -> list[dict]:
    flags: list[dict] = []
    cross_classes = allergy_entry.get("cross_reactive_classes", [])
    if not cross_classes:
        return flags

    for cross_class in cross_classes:
        cross_entry = ALLERGY_DRUG_CLASS_MAP.get(cross_class)
        if not cross_entry:
            continue
        for med in medications:
            med_norm = _normalize(med)
            if med_norm in [_normalize(d) for d in cross_entry["class_drugs"]]:
                flag_key = (cross_class, med_norm)
                if flag_key in seen:
                    continue
                seen.add(flag_key)
                flags.append({
                    "severity": "CROSS-REACTIVE",
                    "allergy": allergy,
                    "medication": med,
                    "class_name": cross_entry["class_name"],
                })

    return flags


def verify(input_data: dict) -> dict:
    allergies = input_data.get("allergies", [])
    medications = input_data.get("medications", [])

    if not isinstance(allergies, list) or not isinstance(medications, list):
        return {
            "passed": False,
            "flags": [{"severity": "DIRECT", "allergy": "", "medication": "", "class_name": "输入格式错误"}],
        }

    if not allergies or not medications:
        return {"passed": True, "flags": []}

    all_flags: list[dict] = []
    seen: set[tuple] = set()

    for allergy in allergies:
        a = _normalize(allergy)
        entry = ALLERGY_DRUG_CLASS_MAP.get(a)
        if not entry:
            entry = ALLERGY_DRUG_CLASS_MAP.get(_resolve_primary_key(a))

        for med in medications:
            m = _normalize(med)

            if a == m:
                key = (a, m)
                if key not in seen:
                    seen.add(key)
                    cls = entry["class_name"] if entry else (_resolve_class(allergy) or "未知")
                    all_flags.append({
                        "severity": "DIRECT",
                        "allergy": allergy,
                        "medication": med,
                        "class_name": cls,
                    })
                continue

            if entry and m in [_normalize(d) for d in entry["class_drugs"]]:
                key = (a, m)
                if key not in seen:
                    seen.add(key)
                    all_flags.append({
                        "severity": "CLASS",
                        "allergy": allergy,
                        "medication": med,
                        "class_name": entry["class_name"],
                    })

        if entry:
            cross_flags = _find_cross_reactive_for_allergy(entry, allergy, medications, seen)
            all_flags.extend(cross_flags)

    passed = len(all_flags) == 0
    return {"passed": passed, "flags": all_flags}


def handle_hook_context(context: dict) -> dict:
    args = context.get("arguments", {})
    allergies = args.get("allergies", [])
    medications = args.get("medications", [])

    input_data = {"allergies": allergies, "medications": medications}
    result = verify(input_data)

    flags = result.get("flags", [])
    if not flags:
        return {
            "outcome": "success",
            "system_message": "过敏安全检查通过：未发现过敏冲突。",
            "patch": None,
        }

    severities = {f["severity"] for f in flags}
    blocking_severities = {"DIRECT", "CLASS"}
    has_blocking = bool(severities & blocking_severities)

    if has_blocking:
        blocking_flags = [f for f in flags if f["severity"] in blocking_severities]
        details = "; ".join(
            f"[{f['severity']}] {f['allergy']} ↔ {f['medication']} ({f['class_name']})"
            for f in blocking_flags
        )
        return {
            "outcome": "blocking",
            "system_message": f"过敏安全阻断：发现 {len(blocking_flags)} 项严重过敏冲突。{details}",
            "patch": None,
        }

    warning_flags = [f for f in flags if f["severity"] == "CROSS-REACTIVE"]
    details = "; ".join(
        f"[{f['severity']}] {f['allergy']} ↔ {f['medication']} ({f['class_name']})"
        for f in warning_flags
    )
    return {
        "outcome": "non_blocking_error",
        "system_message": f"过敏安全警告：发现 {len(warning_flags)} 项交叉过敏风险。{details}",
        "patch": None,
    }


def _run_tests() -> None:
    tests: list[tuple[str, dict, bool]] = [
        (
            "青霉素过敏 + 阿莫西林 = DIRECT",
            {"allergies": ["penicillin"], "medications": ["amoxicillin"]},
            False,
        ),
        (
            "青霉素过敏 + 青霉素 = DIRECT",
            {"allergies": ["penicillin"], "medications": ["penicillin"]},
            False,
        ),
        (
            "青霉素过敏 + 头孢克洛 = CROSS-REACTIVE",
            {"allergies": ["penicillin"], "medications": ["cephalexin"]},
            False,
        ),
        (
            "磺胺过敏 + 复方磺胺甲噁唑 = CLASS",
            {"allergies": ["sulfa drugs"], "medications": ["trimethoprim-sulfamethoxazole"]},
            False,
        ),
        (
            "阿司匹林过敏 + 布洛芬 = CLASS",
            {"allergies": ["aspirin"], "medications": ["ibuprofen"]},
            False,
        ),
        (
            "阿片类过敏 + 吗啡 = CLASS",
            {"allergies": ["codeine"], "medications": ["morphine"]},
            False,
        ),
        (
            "鸡蛋过敏 + 流感疫苗 = CLASS",
            {"allergies": ["eggs"], "medications": ["influenza vaccine"]},
            False,
        ),
        (
            "无过敏 = 通过",
            {"allergies": ["none"], "medications": ["aspirin"]},
            True,
        ),
        (
            "过敏为空 = 通过",
            {"allergies": [], "medications": ["aspirin"]},
            True,
        ),
        (
            "药物为空 = 通过",
            {"allergies": ["penicillin"], "medications": []},
            True,
        ),
    ]

    all_pass = True
    for label, data, expected in tests:
        result = verify(data)
        ok = result["passed"] == expected
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        print(f"[{status}] {label}")
        if result["flags"]:
            for f in result["flags"]:
                print(f"       [{f['severity']}] allergy={f['allergy']}, med={f['medication']}, class={f['class_name']}")
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