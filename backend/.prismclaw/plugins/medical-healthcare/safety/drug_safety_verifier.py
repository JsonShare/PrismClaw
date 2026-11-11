#!/usr/bin/env python3
# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0
"""药物安全验证器 — 内置 50+ 危险药物相互作用知识库。

用法:
    python drug_safety_verifier.py                  # 运行内置测试
    echo '{"medications": ["warfarin", "aspirin"]}' | python drug_safety_verifier.py --stdin

输入 (input_data):
    { "medications": [str, ...] }

输出:
    { "passed": bool, "flags": [{ "severity": "CRITICAL|WARNING",
                                    "reason": str,
                                    "interacting_drugs": [str, ...] }] }
"""
from __future__ import annotations

import json
import sys

DRUG_INTERACTIONS: list[dict] = [
    {
        "drug_a": "warfarin",
        "drug_b": "aspirin",
        "severity": "CRITICAL",
        "reason": "显著增加出血风险，阿司匹林抑制血小板聚集，华法林抑制凝血因子合成",
    },
    {
        "drug_a": "warfarin",
        "drug_b": "ibuprofen",
        "severity": "CRITICAL",
        "reason": "NSAID 损伤胃肠道黏膜并抑制血小板聚集，与华法林联用显著增加胃肠道出血风险",
    },
    {
        "drug_a": "warfarin",
        "drug_b": "acetaminophen",
        "severity": "WARNING",
        "reason": "长期或高剂量对乙酰氨基酚可能增强华法林抗凝效果，需监测 INR",
    },
    {
        "drug_a": "warfarin",
        "drug_b": "vitamin k",
        "severity": "WARNING",
        "reason": "维生素 K 拮抗华法林抗凝作用，降低疗效",
    },
    {
        "drug_a": "warfarin",
        "drug_b": "rifampin",
        "severity": "WARNING",
        "reason": "利福平诱导 CYP2C9 酶代谢，加速华法林清除，降低抗凝效果",
    },
    {
        "drug_a": "warfarin",
        "drug_b": "amiodarone",
        "severity": "CRITICAL",
        "reason": "胺碘酮抑制华法林代谢，增强抗凝效果，需将华法林剂量减少 30-50%",
    },
    {
        "drug_a": "warfarin",
        "drug_b": "cephalosporins",
        "severity": "WARNING",
        "reason": "某些头孢菌素可能干扰维生素 K 代谢，增强华法林抗凝效果",
    },
    {
        "drug_a": "metformin",
        "drug_b": "contrast dye",
        "severity": "CRITICAL",
        "reason": "碘造影剂可导致乳酸酸中毒，肾功能受损时风险极高，造影前需停用二甲双胍",
    },
    {
        "drug_a": "metformin",
        "drug_b": "alcohol",
        "severity": "CRITICAL",
        "reason": "酒精增加乳酸酸中毒风险，与二甲双胍联用可致命",
    },
    {
        "drug_a": "metformin",
        "drug_b": "enalapril",
        "severity": "WARNING",
        "reason": "ACE 抑制剂与二甲双胍联用可能增加低血糖风险",
    },
    {
        "drug_a": "metformin",
        "drug_b": "atorvastatin",
        "severity": "WARNING",
        "reason": "他汀类药物可能增加二甲双胍相关的肌肉毒性风险",
    },
    {
        "drug_a": "furosemide",
        "drug_b": "digoxin",
        "severity": "CRITICAL",
        "reason": "呋塞米导致低钾血症，低钾增加地高辛毒性风险（心律失常）",
    },
    {
        "drug_a": "furosemide",
        "drug_b": "lisinopril",
        "severity": "WARNING",
        "reason": "联用可能增加低血压和肾功能不全风险",
    },
    {
        "drug_a": "furosemide",
        "drug_b": "aspirin",
        "severity": "WARNING",
        "reason": "阿司匹林降低呋塞米的利尿效果",
    },
    {
        "drug_a": "furosemide",
        "drug_b": "amoxicillin",
        "severity": "WARNING",
        "reason": "呋塞米可能增加青霉素过敏反应风险",
    },
    {
        "drug_a": "digoxin",
        "drug_b": "verapamil",
        "severity": "CRITICAL",
        "reason": "维拉帕米减慢地高辛清除，升高血药浓度，需减少地高辛剂量",
    },
    {
        "drug_a": "digoxin",
        "drug_b": "amiodarone",
        "severity": "CRITICAL",
        "reason": "胺碘酮显著升高地高辛血药浓度，需将地高辛剂量减半",
    },
    {
        "drug_a": "digoxin",
        "drug_b": "spironolactone",
        "severity": "WARNING",
        "reason": "螺内酯可能升高地高辛血药浓度，需监测",
    },
    {
        "drug_a": "digoxin",
        "drug_b": "quinidine",
        "severity": "CRITICAL",
        "reason": "奎尼丁升高地高辛血药浓度约 50-100%，需调整剂量",
    },
    {
        "drug_a": "aspirin",
        "drug_b": "ibuprofen",
        "severity": "WARNING",
        "reason": "布洛芬降低阿司匹林心血管保护效果，增加心血管事件风险",
    },
    {
        "drug_a": "aspirin",
        "drug_b": "clopidogrel",
        "severity": "WARNING",
        "reason": "联用可能增加出血风险，但在某些心血管适应证中是治疗方案",
    },
    {
        "drug_a": "aspirin",
        "drug_b": "omeprazole",
        "severity": "WARNING",
        "reason": "奥美拉唑可能降低阿司匹林的抗血小板活性",
    },
    {
        "drug_a": "aspirin",
        "drug_b": "warfarin",
        "severity": "CRITICAL",
        "reason": "显著增加出血风险",
    },
    {
        "drug_a": "lisinopril",
        "drug_b": "spironolactone",
        "severity": "CRITICAL",
        "reason": "联用增加高钾血症风险，可能导致心脏骤停",
    },
    {
        "drug_a": "lisinopril",
        "drug_b": "ibuprofen",
        "severity": "WARNING",
        "reason": "NSAID 降低 ACE 抑制剂降压效果，可能损害肾功能",
    },
    {
        "drug_a": "lisinopril",
        "drug_b": "potassium supplements",
        "severity": "CRITICAL",
        "reason": "ACE 抑制剂减少钾排泄，补充钾可导致危险的高钾血症",
    },
    {
        "drug_a": "atorvastatin",
        "drug_b": "clarithromycin",
        "severity": "CRITICAL",
        "reason": "克拉霉素抑制 CYP3A4，显著升高他汀血药浓度，增加横纹肌溶解风险",
    },
    {
        "drug_a": "atorvastatin",
        "drug_b": "grapefruit juice",
        "severity": "CRITICAL",
        "reason": "柚子汁抑制 CYP3A4，显著升高他汀血药浓度",
    },
    {
        "drug_a": "atorvastatin",
        "drug_b": "gemfibrozil",
        "severity": "WARNING",
        "reason": "联用可能增加横纹肌溶解风险",
    },
    {
        "drug_a": "atorvastatin",
        "drug_b": "niacin",
        "severity": "WARNING",
        "reason": "烟酸与他汀联用可能增加肌病风险",
    },
    {
        "drug_a": "amiodarone",
        "drug_b": "warfarin",
        "severity": "CRITICAL",
        "reason": "胺碘酮增强华法林抗凝效果，显著增加出血风险",
    },
    {
        "drug_a": "amiodarone",
        "drug_b": "digoxin",
        "severity": "CRITICAL",
        "reason": "胺碘酮升高地高辛血药浓度，增加毒性风险",
    },
    {
        "drug_a": "amiodarone",
        "drug_b": "simvastatin",
        "severity": "WARNING",
        "reason": "胺碘酮可能升高辛伐他汀血药浓度",
    },
    {
        "drug_a": "omeprazole",
        "drug_b": "clopidogrel",
        "severity": "WARNING",
        "reason": "奥美拉唑降低氯吡格雷抗血小板活性，增加心血管事件风险",
    },
    {
        "drug_a": "omeprazole",
        "drug_b": "warfarin",
        "severity": "WARNING",
        "reason": "奥美拉唑可能增强华法林抗凝效果",
    },
    {
        "drug_a": "omeprazole",
        "drug_b": "diazepam",
        "severity": "WARNING",
        "reason": "奥美拉唑延长地西泮半衰期，增强镇静效果",
    },
    {
        "drug_a": "sertraline",
        "drug_b": "tramadol",
        "severity": "CRITICAL",
        "reason": "联用可能导致血清素综合征（高热、抽搐、意识改变）",
    },
    {
        "drug_a": "sertraline",
        "drug_b": "warfarin",
        "severity": "WARNING",
        "reason": "SSRIs 可能增强华法林抗凝效果，增加出血风险",
    },
    {
        "drug_a": "sertraline",
        "drug_b": "ibuprofen",
        "severity": "WARNING",
        "reason": "SSRIs 与 NSAID 联用可能增加胃肠道出血风险",
    },
    {
        "drug_a": "sertraline",
        "drug_b": "phenelzine",
        "severity": "CRITICAL",
        "reason": "SSRI 与 MAOI 联用可导致致命的血清素综合征",
    },
    {
        "drug_a": "tramadol",
        "drug_b": "sertraline",
        "severity": "CRITICAL",
        "reason": "血清素综合征风险",
    },
    {
        "drug_a": "tramadol",
        "drug_b": "warfarin",
        "severity": "WARNING",
        "reason": "曲马多可能增强华法林抗凝效果",
    },
    {
        "drug_a": "ibuprofen",
        "drug_b": "warfarin",
        "severity": "CRITICAL",
        "reason": "显著增加胃肠道出血风险",
    },
    {
        "drug_a": "ibuprofen",
        "drug_b": "lisinopril",
        "severity": "WARNING",
        "reason": "NSAID 降低 ACE 抑制剂降压效果",
    },
    {
        "drug_a": "ibuprofen",
        "drug_b": "furosemide",
        "severity": "WARNING",
        "reason": "NSAID 降低呋塞米利尿效果",
    },
    {
        "drug_a": "ibuprofen",
        "drug_b": "aspirin",
        "severity": "WARNING",
        "reason": "布洛芬降低阿司匹林心血管保护效果",
    },
    {
        "drug_a": "gabapentin",
        "drug_b": "morphine",
        "severity": "WARNING",
        "reason": "联用可能增加中枢抑制和呼吸抑制风险",
    },
    {
        "drug_a": "gabapentin",
        "drug_b": "clonazepam",
        "severity": "WARNING",
        "reason": "联用可能增加嗜睡和头晕风险",
    },
    {
        "drug_a": "morphine",
        "drug_b": "clonazepam",
        "severity": "CRITICAL",
        "reason": "阿片类与苯二氮卓类联用显著增加呼吸抑制和死亡风险",
    },
    {
        "drug_a": "morphine",
        "drug_b": "alcohol",
        "severity": "CRITICAL",
        "reason": "酒精与阿片类联用增加呼吸抑制和死亡风险",
    },
    {
        "drug_a": "alcohol",
        "drug_b": "metformin",
        "severity": "CRITICAL",
        "reason": "酒精增加二甲双胍相关乳酸酸中毒风险",
    },
    {
        "drug_a": "alcohol",
        "drug_b": "warfarin",
        "severity": "WARNING",
        "reason": "酒精可能增强华法林抗凝效果，增加出血风险",
    },
    {
        "drug_a": "alcohol",
        "drug_b": "acetaminophen",
        "severity": "CRITICAL",
        "reason": "酒精与对乙酰氨基酚联用显著增加肝损伤风险",
    },
    {
        "drug_a": "spironolactone",
        "drug_b": "lisinopril",
        "severity": "CRITICAL",
        "reason": "联用增加高钾血症风险",
    },
    {
        "drug_a": "spironolactone",
        "drug_b": "potassium supplements",
        "severity": "CRITICAL",
        "reason": "螺内酯阻断钾排泄，补充钾可导致危险的高钾血症",
    },
    {
        "drug_a": "clopidogrel",
        "drug_b": "omeprazole",
        "severity": "WARNING",
        "reason": "奥美拉唑降低氯吡格雷抗血小板活性",
    },
    {
        "drug_a": "clopidogrel",
        "drug_b": "warfarin",
        "severity": "WARNING",
        "reason": "联用增加出血风险，需密切监测",
    },
    {
        "drug_a": "contrast dye",
        "drug_b": "metformin",
        "severity": "CRITICAL",
        "reason": "造影剂诱发乳酸酸中毒，需在造影前停用二甲双胍",
    },
    {
        "drug_a": "contrast dye",
        "drug_b": "furosemide",
        "severity": "WARNING",
        "reason": "造影剂与袢利尿剂联用可能增加肾损伤风险",
    },
    {
        "drug_a": "potassium supplements",
        "drug_b": "lisinopril",
        "severity": "CRITICAL",
        "reason": "ACE 抑制剂减少钾排泄，联用可导致高钾血症",
    },
    {
        "drug_a": "potassium supplements",
        "drug_b": "spironolactone",
        "severity": "CRITICAL",
        "reason": "螺内酯阻断钾排泄，补充钾可导致高钾血症",
    },
]


def _normalize(name: str) -> str:
    return name.strip().lower()


def _find_interactions(medications: list[str]) -> list[dict]:
    normalized = [_normalize(m) for m in medications]
    flag_set: set[tuple[str, str]] = set()
    flags: list[dict] = []

    for interaction in DRUG_INTERACTIONS:
        a = interaction["drug_a"]
        b = interaction["drug_b"]
        found_a = a in normalized
        found_b = b in normalized

        if not (found_a and found_b):
            continue

        pair_key = tuple(sorted([a, b]))
        if pair_key in flag_set:
            continue
        flag_set.add(pair_key)

        flags.append({
            "severity": interaction["severity"],
            "reason": interaction["reason"],
            "interacting_drugs": [a, b],
        })

    return flags


def verify(input_data: dict) -> dict:
    medications = input_data.get("medications", [])
    if not isinstance(medications, list):
        return {"passed": False, "flags": [{"severity": "WARNING", "reason": "medications 必须是字符串列表", "interacting_drugs": []}]}

    if len(medications) < 2:
        return {"passed": True, "flags": []}

    flags = _find_interactions(medications)
    critical_count = sum(1 for f in flags if f["severity"] == "CRITICAL")
    passed = critical_count == 0

    return {"passed": passed, "flags": flags}


def handle_hook_context(context: dict) -> dict:
    args = context.get("arguments", {})
    medications = args.get("medications") or args.get("drugs", [])

    if not medications:
        return {
            "outcome": "success",
            "system_message": "药物安全检查通过：未提供药物列表。",
            "patch": None,
        }

    input_data = {"medications": medications}
    result = verify(input_data)

    flags = result.get("flags", [])
    if not flags:
        return {
            "outcome": "success",
            "system_message": "药物安全检查通过：未发现药物相互作用。",
            "patch": None,
        }

    critical_flags = [f for f in flags if f["severity"] == "CRITICAL"]
    if critical_flags:
        details = "; ".join(
            f"{', '.join(f['interacting_drugs'])}: {f['reason']}"
            for f in critical_flags
        )
        return {
            "outcome": "blocking",
            "system_message": f"药物安全阻断：发现 {len(critical_flags)} 项严重药物相互作用。{details}",
            "patch": None,
        }

    warning_flags = [f for f in flags if f["severity"] == "WARNING"]
    details = "; ".join(
        f"{', '.join(f['interacting_drugs'])}: {f['reason']}"
        for f in warning_flags
    )
    return {
        "outcome": "non_blocking_error",
        "system_message": f"药物安全警告：发现 {len(warning_flags)} 项药物相互作用警告。{details}",
        "patch": None,
    }


def _run_tests() -> None:
    tests: list[tuple[str, dict, bool]] = [
        (
            "华法林 + 阿司匹林 = CRITICAL",
            {"medications": ["warfarin", "aspirin"]},
            False,
        ),
        (
            "二甲双胍 + 造影剂 = CRITICAL",
            {"medications": ["metformin", "contrast dye"]},
            False,
        ),
        (
            "呋塞米 + 地高辛 = CRITICAL",
            {"medications": ["furosemide", "digoxin"]},
            False,
        ),
        (
            "阿托伐他汀 + 克拉霉素 = CRITICAL",
            {"medications": ["atorvastatin", "clarithromycin"]},
            False,
        ),
        (
            "吗啡 + 氯硝西泮 = CRITICAL",
            {"medications": ["morphine", "clonazepam"]},
            False,
        ),
        (
            "单药 = 通过",
            {"medications": ["aspirin"]},
            True,
        ),
        (
            "无相互作用的多药 = 通过",
            {"medications": ["aspirin", "acetaminophen"]},
            True,
        ),
        (
            "三种药物含相互作用",
            {"medications": ["warfarin", "aspirin", "atorvastatin"]},
            False,
        ),
        (
            "空列表 = 通过",
            {"medications": []},
            True,
        ),
        (
            "左氧 + 二甲双胍 + 酒精 = CRITICAL",
            {"medications": ["levofloxacin", "metformin", "alcohol"]},
            False,
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
                print(f"       [{f['severity']}] {', '.join(f['interacting_drugs'])}: {f['reason']}")
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