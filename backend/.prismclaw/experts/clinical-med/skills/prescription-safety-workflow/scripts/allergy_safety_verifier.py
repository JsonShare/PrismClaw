#!/usr/bin/env python3
# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0
"""过敏安全验证器 — 将患者过敏史与用药进行交叉比对。

用法:
    python3 allergy_safety_verifier.py --selftest
    python3 allergy_safety_verifier.py --input input.json
    echo '{"allergies": ["penicillin"], "medications": ["amoxicillin"]}' | python3 allergy_safety_verifier.py

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

边界声明:
    本规则为宽口径保守筛查，仅用于提示潜在风险，不构成给药禁忌结论。
    真实青霉素与头孢菌素之间的交叉过敏率约 2%，且主要与 R1 侧链相似性相关，并非全类交叉过敏。
    本规则未做 R1 侧链比对，可能对部分药物产生假阳性。
    结果仅供提示，最终给药禁忌须由执业医师结合具体药物与患者情况判定。
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


CN_ALIASES: dict[str, str] = {
    # 类别 / 过敏原
    "青霉素": "penicillin", "青霉素类": "penicillin", "盘尼西林": "penicillin",
    "头孢": "cephalosporins", "头孢菌素": "cephalosporins", "头孢菌素类": "cephalosporins",
    "磺胺": "sulfa drugs", "磺胺类": "sulfa drugs", "磺胺类药物": "sulfa drugs",
    "阿司匹林过敏": "aspirin",
    "非甾体抗炎药": "nsaids", "非甾体抗炎药过敏": "nsaids", "解热镇痛药": "nsaids",
    "阿片类": "codeine", "阿片类镇痛药": "codeine",
    "造影剂": "contrast dye", "碘造影剂": "contrast dye", "对比剂": "contrast dye",
    "贝类": "shellfish", "海鲜": "shellfish",
    "乳胶": "latex", "橡胶": "latex",
    "花生": "peanuts", "坚果": "peanuts",
    "鸡蛋": "eggs", "蛋类": "eggs",
    "四环素类": "tetracycline",
    "大环内酯类": "macrolides",
    "氟喹诺酮类": "fluoroquinolones", "喹诺酮类": "fluoroquinolones",
    "万古霉素类": "vancomycin", "糖肽类": "vancomycin",
    # 具体药物
    "阿莫西林": "amoxicillin", "氨苄西林": "ampicillin", "氨苄青霉素": "ampicillin",
    "阿莫西林克拉维酸": "augmentin", "哌拉西林": "piperacillin", "替卡西林": "ticarcillin",
    "美洛西林": "mezlocillin",
    "头孢氨苄": "cephalexin", "头孢呋辛": "cefuroxime", "头孢曲松": "ceftriaxone",
    "头孢唑林": "cefazolin",
    "环丙沙星": "ciprofloxacin", "莫西沙星": "moxifloxacin", "左氧氟沙星": "levofloxacin",
    "氧氟沙星": "ofloxacin", "诺氟沙星": "norfloxacin", "加替沙星": "gatifloxacin",
    "依诺沙星": "enoxacin", "洛美沙星": "lomefloxacin", "司氟沙星": "sparfloxacin",
    "磺胺甲噁唑": "sulfamethoxazole", "复方磺胺甲噁唑": "trimethoprim-sulfamethoxazole",
    "复方新诺明": "trimethoprim-sulfamethoxazole",
    "磺胺嘧啶": "sulfadiazine", "柳氮磺吡啶": "sulfasalazine",
    "磺胺醋酰": "sulfacetamide", "磺胺吡啶": "sulfapyridine",
    "阿司匹林": "aspirin", "乙酰水杨酸": "aspirin",
    "布洛芬": "ibuprofen", "萘普生": "naproxen", "双氯芬酸": "diclofenac",
    "酮咯酸": "ketorolac", "吲哚美辛": "indomethacin", "美洛昔康": "meloxicam",
    "吡罗昔康": "piroxicam", "塞来昔布": "celecoxib", "依托考昔": "etoricoxib",
    "氯诺昔康": "lornoxicam",
    "可待因": "codeine", "吗啡": "morphine", "氢可酮": "hydrocodone", "羟考酮": "oxycodone",
    "芬太尼": "fentanyl", "曲马多": "tramadol", "丁丙诺啡": "buprenorphine",
    "美沙酮": "methadone", "氢吗啡酮": "hydromorphone", "羟吗啡酮": "oxymorphone",
    "碘海醇": "iohexol", "碘帕醇": "iopamidol", "泛影酸盐": "diatrizoate",
    "碘他拉酸盐": "iothalamate", "甲泛葡胺": "metrizamide",
    "花生油": "peanut oil",
    "卵清蛋白": "egg albumin", "流感疫苗": "influenza vaccine",
    "黄热病疫苗": "yellow fever vaccine", "狂犬病疫苗": "rabies vaccine",
    "四环素": "tetracycline", "多西环素": "doxycycline", "米诺环素": "minocycline",
    "土霉素": "oxytetracycline", "去甲金霉素": "demeclocycline", "替加环素": "tigecycline",
    "红霉素": "erythromycin", "阿奇霉素": "azithromycin", "克拉霉素": "clarithromycin",
    "泰利霉素": "telithromycin", "地红霉素": "dirithromycin", "醋竹桃霉素": "troleandomycin",
    "万古霉素": "vancomycin", "替考拉宁": "teicoplanin",
    "达巴万星": "dalbavancin", "奥利万星": "oritavancin",
}

_DOSAGE_SUFFIXES = ("缓释片", "肠溶片", "注射液", "口服液", "胶囊", "颗粒", "片", "针", "钠", "钾盐")


def _normalize(name: str) -> str:
    key = name.strip().lower()
    for _ in range(3):
        hit = CN_ALIASES.get(key)
        if hit:
            return hit
        for suffix in _DOSAGE_SUFFIXES:
            if key.endswith(suffix) and len(key) > len(suffix):
                key = key[: -len(suffix)]
                break
        else:
            break
    return key


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


def _cli_main(argv: list[str]) -> None:
    """WorkBuddy 技能 CLI 入口。

    --selftest           运行内置自测，打印每条 PASS/FAIL。
    --input <json文件>   从文件读取输入 JSON，调用 verify() 并打印结果 JSON。
    无参数                若 stdin 是管道则读取 stdin JSON；否则运行内置自测。
    """
    if "--selftest" in argv:
        _run_tests()
        return

    input_data: dict | None = None
    if "--input" in argv:
        idx = argv.index("--input")
        if idx + 1 >= len(argv):
            print(json.dumps({"error": "--input 需要一个 JSON 文件路径"}, ensure_ascii=False))
            raise SystemExit(2)
        with open(argv[idx + 1], "r", encoding="utf-8") as f:
            input_data = json.load(f)
    elif not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        if raw:
            input_data = json.loads(raw)

    if input_data is None:
        _run_tests()
        return

    print(json.dumps(verify(input_data), ensure_ascii=False))


if __name__ == "__main__":
    _cli_main(sys.argv[1:])
