#!/usr/bin/env python3
# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0
"""药物安全验证器 — 内置 50+ 危险药物相互作用知识库。

用法:
    python3 drug_safety_verifier.py --selftest
    python3 drug_safety_verifier.py --input input.json
    echo '{"medications": ["warfarin", "aspirin"]}' | python3 drug_safety_verifier.py

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


CN_DRUG_ALIASES: dict[str, str] = {
    # 通用名
    "对乙酰氨基酚": "acetaminophen", "扑热息痛": "acetaminophen", "退热净": "acetaminophen",
    "酒精": "alcohol", "乙醇": "alcohol", "饮酒": "alcohol",
    "胺碘酮": "amiodarone",
    "阿莫西林": "amoxicillin",
    "阿司匹林": "aspirin", "乙酰水杨酸": "aspirin",
    "阿托伐他汀": "atorvastatin",
    "头孢菌素": "cephalosporins", "头孢": "cephalosporins", "头孢曲松": "cephalosporins",
    "头孢呋辛": "cephalosporins", "头孢克肟": "cephalosporins", "头孢唑林": "cephalosporins",
    "头孢他啶": "cephalosporins",
    "克拉霉素": "clarithromycin",
    "氯硝西泮": "clonazepam",
    "氯吡格雷": "clopidogrel",
    "造影剂": "contrast dye", "碘造影剂": "contrast dye", "含碘对比剂": "contrast dye",
    "地西泮": "diazepam", "安定": "diazepam",
    "地高辛": "digoxin",
    "依那普利": "enalapril",
    "呋塞米": "furosemide", "速尿": "furosemide",
    "加巴喷丁": "gabapentin",
    "吉非罗齐": "gemfibrozil", "吉非贝齐": "gemfibrozil",
    "西柚汁": "grapefruit juice", "葡萄柚汁": "grapefruit juice", "柚子汁": "grapefruit juice",
    "布洛芬": "ibuprofen",
    "赖诺普利": "lisinopril",
    "二甲双胍": "metformin",
    "吗啡": "morphine",
    "烟酸": "niacin",
    "奥美拉唑": "omeprazole",
    "苯乙肼": "phenelzine",
    "补钾制剂": "potassium supplements", "氯化钾": "potassium supplements", "补钾": "potassium supplements",
    "奎尼丁": "quinidine",
    "利福平": "rifampin",
    "舍曲林": "sertraline",
    "辛伐他汀": "simvastatin",
    "螺内酯": "spironolactone", "安体舒通": "spironolactone",
    "曲马多": "tramadol",
    "维拉帕米": "verapamil",
    "维生素k": "vitamin k", "维生素 k": "vitamin k",
    "华法林": "warfarin", "华法令": "warfarin",
}

# 常见剂型/盐型后缀，剥离后再查别名
_CN_DOSAGE_SUFFIXES = ("缓释片", "肠溶片", "注射液", "口服液", "胶囊", "颗粒", "片", "针", "钠", "钾盐")


def _normalize(name: str) -> str:
    key = name.strip().lower()
    for _ in range(3):
        hit = CN_DRUG_ALIASES.get(key)
        if hit:
            return hit
        for suffix in _CN_DOSAGE_SUFFIXES:
            if key.endswith(suffix) and len(key) > len(suffix):
                key = key[: -len(suffix)]
                break
        else:
            break
    return key


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


def _run_tests() -> None:
    tests: list[tuple[str, dict, bool]] = [
        (
            "中文药名 华法林 + 阿司匹林 = CRITICAL",
            {"medications": ["华法林", "阿司匹林"]},
            False,
        ),
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
