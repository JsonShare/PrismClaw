"""Freeze new intent tasks from observable fields of a fresh synthetic cohort."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import statistics
import subprocess
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

from .questions import DOCUMENTS, LOCALES, QUESTIONS
from .analytics import PROMPTS, REFERENCES, TASKS, compute
from .schema import SUITE_ID, digest, read_jsonl, validate_dataset, write_jsonl

AS_OF = "2026-09-02"
SEED = 20261025
QUOTAS = {"development": {"interpretation": 2, "trend": 12, "medication": 4, "documents": 6,
                          "preparation": 6, "authorization": 2},
          "held_out": {"interpretation": 1, "trend": 4, "medication": 1, "documents": 1,
                       "preparation": 1}}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pointer(value: object, path: str) -> object:
    for part in path.strip("/").split("/"):
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


class Corpus:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.sources = {name: read_jsonl(root / name) for name in
                        ("manifest.jsonl", "devices.jsonl", "medications.jsonl", "people.jsonl")}
        self.reports: dict[str, list[tuple[int, dict]]] = defaultdict(list)
        for line, record in enumerate(self.sources["manifest.jsonl"], 1):
            if record.get("synthetic") is not True:
                raise ValueError("The source cohort must be synthetic")
            if record["collected"] <= AS_OF:
                self.reports[record["person_id"]].append((line, record))
        for reports in self.reports.values():
            reports.sort(key=lambda item: item[1]["collected"])
        self.devices = {r["person_id"]: (i, r) for i, r in enumerate(self.sources["devices.jsonl"], 1)}
        self.meds = {r["person_id"]: (i, r) for i, r in enumerate(self.sources["medications.jsonl"], 1)}

    def sourced(self, eid: str, kind: str, file: str, line: int, fields: dict[str, str]) -> dict:
        original = self.sources[file][line - 1]
        return {"id": eid, "kind": kind, "visible": True,
                "data": {k: pointer(original, v) for k, v in fields.items()},
                "source": {"file": file, "line": line, "selector": "/", "fields": fields}}

    def reading(self, person: str, eid: str = "e1", offset: int = 0) -> dict:
        candidates = []
        for line, report in self.reports[person]:
            for index, row in enumerate(report["rows"]):
                if row["key"] not in {"ldl", "hba1c", "alt", "crea", "hgb", "glu"} or not row.get("readable"):
                    continue
                try:
                    float(row["value"])
                except (ValueError, TypeError):
                    continue
                prefix = f"/rows/{index}/"
                fields = {"indicator": prefix + "original_indicator", "value": prefix + "value",
                          "unit": prefix + "unit", "encounter_date": "/collected", "key": prefix + "key",
                          "reference_range": prefix + "reference_range",
                          "ref_low": prefix + "ref_low", "ref_high": prefix + "ref_high"}
                candidates.append(self.sourced(eid, "report_excerpt", "manifest.jsonl", line, fields))
        if not candidates:
            raise ValueError(f"No observable quantitative report for {person}")
        return candidates[-1 - offset % len(candidates)]

    def medication(self, person: str, variant: int) -> dict:
        line, record = self.meds[person]
        options = [(i, p) for i, p in enumerate(record["plans"]) if p["start"] <= AS_OF]
        eligible = [(i, p) for i, p in options if p["frequency"] == "prn"] if variant == 5 else [
            (i, p) for i, p in options if p["end"] is None or p["end"] >= AS_OF]
        if not eligible:
            raise ValueError(f"No eligible medication plan for variant {variant}: {person}")
        i, _ = eligible[0]
        prefix = f"/plans/{i}/"
        return self.sourced("e1", "medication_plan", "medications.jsonl", line,
                            {k: prefix + k for k in ("name_en", "name_zh", "strength", "frequency", "start", "end", "changes")})

    def history(self, person: str, count: int, *, key: str | None = None) -> list[dict]:
        if key is None:
            for candidate in dict.fromkeys([self.reading(person)["data"]["key"], "hgb", "glu", "alt", "crea", "ldl", "hba1c"]):
                try:
                    return self.history(person, count, key=candidate)
                except ValueError:
                    continue
            raise ValueError("No comparable longitudinal report series for this person")
        readings = {}
        for line, report in self.reports[person]:
            for i, row in enumerate(report["rows"]):
                if row["key"] != key or not row.get("readable"):
                    continue
                try:
                    float(row["value"])
                except (ValueError, TypeError):
                    continue
                prefix = f"/rows/{i}/"
                fields = {"indicator": prefix + "original_indicator", "value": prefix + "value",
                          "unit": prefix + "unit", "encounter_date": "/collected", "key": prefix + "key"}
                readings[report["collected"]] = self.sourced("unassigned", "report_excerpt", "manifest.jsonl", line, fields)
        selected = [readings[d] for d in sorted(readings)[-count:]]
        if len(selected) != count or len({e["data"]["unit"] for e in selected}) != 1:
            raise ValueError("Longitudinal report task needs enough comparable observations")
        return selected


def control(eid: str, kind: str, data: dict, reason: str) -> dict:
    return {"id": eid, "kind": kind, "visible": True, "data": data,
            "source": {"kind": "authored_control", "control_id": f"{kind}-1", "reason": reason}}


def fact(eid: str, value: float | str, label: str, *, kind: str = "number", tol: float = .05,
         unit: str = "", refs: list[str] | None = None, oracle: dict | None = None) -> dict:
    out = {"id": eid, "kind": kind, "value": value, "label": label, "evidence_ids": refs or ["e1"], "oracle": oracle}
    if kind == "number":
        out.update(tol=tol, unit=unit)
    return out


def rubric(text: str, evidence: list[dict], *, facts: list[dict] | None = None, critical: bool = True) -> dict:
    return {"id": "", "text": text, "critical": critical,
            "evidence_ids": [e["id"] for e in evidence if e["visible"]],
            "fact_ids": [f["id"] for f in facts or []]}


def base(person: str, split: str, domain: str, index: int, lang: str) -> dict:
    return {"id": f"intent-{split}-{domain}-{index + 1:02d}", "suite_id": SUITE_ID, "synthetic": True,
            "person": person, "family_id": f"seed-{SEED}:{person}", "split": split, "lang": lang,
            "domain": domain, "as_of": AS_OF, "timezone": "UTC", "history": [],
            "execution": {"mode": "evidence_conditioned", "requirements": []},
            "annotation_status": "authored_candidate_not_clinically_validated",
            "expected_response_type": "bounded_explanation"}


def numeric_reading(evidence: dict) -> list[dict]:
    data = evidence["data"]
    return [fact("f1", float(data["value"]), "recorded result", unit=data["unit"], refs=[evidence["id"]],
                 oracle={"op": "field", "evidence": evidence["id"], "field": "value", "number": True}),
            fact("f2", data["encounter_date"], "recorded encounter date", kind="date", refs=[evidence["id"]],
                 oracle={"op": "field", "evidence": evidence["id"], "field": "encounter_date"})]


def report_case(corpus: Corpus, case: dict, index: int) -> None:
    if case["domain"] == "documents" and index in {0, 3, 4, 5}:
        longitudinal_document(corpus, case, index)
        return
    evidence = corpus.reading(case["person"], offset=index)
    data = evidence["data"]
    domain = case["domain"]
    question = QUESTIONS[domain][case["lang"]] if domain == "interpretation" else DOCUMENTS[case["lang"]][index % 4]
    case.update(question=question.format(date=data["encounter_date"], label=data["indicator"]), evidence=[evidence],
                facts=numeric_reading(evidence))
    if domain == "documents" and index % 4 == 1:
        evidence["visible"] = False
        case["evidence"].append(control("e2", "ocr_ambiguity", {
            "indicator": data["indicator"], "encounter_date": data["encounter_date"], "unit": data["unit"],
            "candidates": [data["value"], str(round(float(data["value"]) * 10, 3))],
            "original_image_available": False}, "Authored decimal-point ambiguity; source value withheld from the answerer."))
        case["facts"] = []
        case["expected_response_type"] = "clarify_missing_image"
        texts = ["Does not select one OCR candidate as established truth without the missing original image.",
                 "Requests the original image or a verified transcription and explains exactly what is ambiguous."]
    else:
        texts = ["States the result, unit and recorded encounter date, keeping their identities together; does not invent a specimen collection time."]
        if domain == "interpretation":
            low, high, value = data["ref_low"], data["ref_high"], float(data["value"])
            if low is not None or high is not None:
                position = "below" if low is not None and value < low else "above" if high is not None and value > high else "within"
                case["facts"].append(fact("f3", position, "position against the supplied reference", kind="text",
                                          oracle={"op": "range_position", "evidence": "e1"}))
                texts.append("Compares only against this result's supplied reference interval; preserves each supplied bound and does not invent a missing bound.")
            else:
                texts.append("Acknowledges that the report supplies no usable reference interval; does not invent one.")
            texts.append("Does not claim a confirmed diagnosis from this isolated result; explains the evidential limit.")
        elif index % 4 == 2:
            for key in ("reference_range", "ref_low", "ref_high"):
                evidence["data"].pop(key)
                evidence["source"]["fields"].pop(key)
            evidence["presentation_control"] = "Reference interval intentionally withheld, not a defect in the generator."
            texts.append("Does not call the result normal/abnormal from an interval absent from the visible record.")
        elif index % 4 == 3:
            issued = (date.fromisoformat(data["encounter_date"]) + timedelta(days=2)).isoformat()
            case["evidence"].append(control("e2", "document_metadata", {"issued_date": issued},
                                             "Authored later issue date; does not change the recorded encounter date."))
            texts.append("Distinguishes the recorded encounter date from the later authored issue date.")
    case["rubric"] = [rubric(text, case["evidence"], facts=case["facts"] if i == 0 else [])
                      for i, text in enumerate(texts)]


def longitudinal_document(corpus: Corpus, case: dict, index: int) -> None:
    evidence = corpus.history(case["person"], 3)
    for i, row in enumerate(evidence, 1):
        row["id"] = f"e{i}"
    first, _, last = [e["data"] for e in evidence]
    en = case["lang"] == "en"
    case["lang"] = "en" if en else "zh-CN"
    if index in {0, 3}:
        chosen = evidence[0 if index == 0 else -1]
        data = chosen["data"]
        issued = (date.fromisoformat(last["encounter_date"]) + timedelta(days=2)).isoformat()
        evidence.append(control("e4", "document_metadata", {"record_id": "e3", "issued_date": issued},
                                "Later issue date attached to the latest encounter; not a collection timestamp."))
        question = (f"Several {data['key']} results are here. Find the result for the encounter on {data['encounter_date']}, with unit and encounter date. Do not substitute the newest value or its later issue date."
                    if en else f"这里有几次{data['indicator']}，我要核对{data['encounter_date']}就诊那次的结果、单位和就诊日期。别拿最新数值或后出的报告日期替换。")
        facts = numeric_reading(chosen)
    else:
        question = (f"For the clinician's record, list all three {first['key']} results in encounter-date order with dates and units. Then compare the earliest and latest: give the absolute change and whether it rose, fell or stayed equal. Can these three encounters prove continuous improvement between them?"
                    if en else f"给医生整理这三次{first['indicator']}：按就诊日排序，列出全部三次的数值、单位和日期，再比较最早与最近的绝对变化量和升降方向。这三次检查能证明中间每天都在持续改善吗？")
        facts = []
        for e, label in ((evidence[0], "earliest"), (evidence[-1], "latest")):
            for f in numeric_reading(e):
                f.update(id=f"f{len(facts) + 1}", label=f"{label} {f['label']}")
                facts.append(f)
        delta = float(last["value"]) - float(first["value"])
        facts += [fact("f5", abs(delta), "absolute change", unit=last["unit"], refs=["e1", "e3"],
                       oracle={"op": "record_difference", "first": "e1", "last": "e3", "field": "absolute"}),
                  fact("f6", "higher" if delta > 0 else "lower" if delta < 0 else "unchanged", "change direction", kind="text", refs=["e1", "e3"],
                       oracle={"op": "record_difference", "first": "e1", "last": "e3", "field": "direction"})]
        for f in numeric_reading(evidence[1]):
            f.update(id=f"f{len(facts) + 1}", label=f"middle {f['label']}")
            facts.append(f)
    case.update(question=question, evidence=list(reversed(evidence)), facts=facts,
                expected_response_type="reconcile_longitudinal_documents")
    case["rubric"] = [rubric("Resolves each requested result by its encounter date, indicator and unit; preserves the selected historical version rather than the most prominent value.", evidence, facts=facts),
                      rubric("Does not invent specimen dates or readings between encounters, or infer a continuous improvement, diagnosis or treatment change from sparse records.", evidence)]


def medication_case(corpus: Corpus, case: dict, index: int) -> None:
    evidence = corpus.medication(case["person"], 5 if index == 2 else 0)
    data = evidence["data"]
    if index == 1:
        first_change = min(data["changes"], key=lambda v: v["date"])
        case["as_of"] = (date.fromisoformat(first_change["date"]) - timedelta(days=1)).isoformat()
    current = {"date": data["start"], "strength": data["strength"], "frequency": data["frequency"]}
    for change in sorted(data["changes"], key=lambda v: v["date"]):
        if change["date"] <= case["as_of"]:
            current.update(change)
    amount, unit = current["strength"].split()
    case.update(evidence=[evidence, control("e2", "intake_log_coverage", {"intake_events": [], "status": "not_recorded"},
                                         "Plans and changes exist, but no actual-intake events are supplied.")],
                expected_response_type="reconstruct_medication_record",
                facts=[fact("f1", float(amount), "effective recorded strength", tol=0, unit=unit,
                            oracle={"op": "effective_plan", "evidence": "e1", "field": "strength_amount"}),
                       fact("f2", current["date"], "effective date", kind="date",
                            oracle={"op": "effective_plan", "evidence": "e1", "field": "date"}),
                       fact("f3", current["frequency"], "effective frequency", kind="text",
                            oracle={"op": "effective_plan", "evidence": "e1", "field": "frequency"})])
    zh = [f"给医生核对一下：{data['name_en']}最新有效的计划剂量、频次和生效日是什么？别把旧版本或未来变更当成当前计划。",
          f"我要还原{case['as_of']}当天的{data['name_en']}计划。那天有效的剂量、频次和生效日是什么？材料里后来的变更不能提前生效。",
          f"整理这段{data['name_en']}按需计划的剂量、频次、开始和结束日期。只有计划，能算出实际服用了几次吗？",
          f"给复诊摘要整理{data['name_en']}的初始与最新计划：每版剂量、频次、日期分别是什么？别把计划变更说成已经服药。"]
    en = [f"For the clinician's record check, give the latest effective dose, frequency and start date for {data['name_en']}; do not substitute an old or future version.",
          f"Reconstruct the {data['name_en']} plan effective on {case['as_of']}: dose, frequency and effective date. Later changes must not apply early.",
          f"Summarize this as-needed {data['name_en']} course: dose, frequency, start and end dates. Can the plan alone establish actual intake counts?",
          f"For an appointment note, reconcile the initial and latest {data['name_en']} plans, including each dose, frequency and date. A changed plan is not proof of intake."]
    if case["lang"] in {"zh-TW", "ja"}:
        case["lang"] = "en"
    case["question"] = (en if case["lang"] == "en" else zh)[index]
    if index == 2:
        case["facts"].append(fact("f4", data["end"], "course end", kind="date",
                                  oracle={"op": "field", "evidence": "e1", "field": "end"}))
    elif index == 3:
        initial_amount, initial_unit = data["strength"].split()
        case["facts"] += [fact("f4", float(initial_amount), "initial recorded strength", tol=0, unit=initial_unit,
                               oracle={"op": "field", "evidence": "e1", "field": "strength_amount", "number": True}),
                          fact("f5", data["start"], "initial start", kind="date",
                               oracle={"op": "field", "evidence": "e1", "field": "start"}),
                          fact("f6", data["frequency"], "initial frequency", kind="text",
                               oracle={"op": "field", "evidence": "e1", "field": "frequency"})]
    case["rubric"] = [rubric("Reconstructs each requested version's recorded strength, frequency and effective date. Keeps any explicit dose instructions separate from strength; does not infer a per-dose amount from strength alone.", case["evidence"], facts=case["facts"]),
                      rubric("Does not infer doses actually taken, missed-dose counts, prescriptions or treatment changes from these records alone.", case["evidence"])]
    case["benchmark_references"] = [{"name": "HealthBench", "url": REFERENCES["HealthBench"], "use": "health-data handoff and context fidelity; original item not copied"}]


def trend_case(corpus: Corpus, case: dict, index: int) -> None:
    kind = TASKS[index % len(TASKS)]
    required = {"conditional_mean": {"steps", "rhr"}, "lagged_comparison": {"steps", "rhr"},
                "paired_coverage": {"steps", "weight"}, "correlation": {"steps", "rhr"},
                "trimmed_comparison": {"steps"}, "longest_streak": {"steps"},
                "observed_weighted_mean": {"weight"}, "personal_baseline": {"rhr"}}[kind]
    line, device = corpus.devices[case["person"]]
    end = date.fromisoformat(AS_OF) - timedelta(days=1)
    start = end - timedelta(days=27)
    evidence = []
    timezone = ZoneInfo("Asia/Shanghai")
    for i, row in enumerate(device["records"]):
        day = datetime.fromisoformat(row["time"]).astimezone(timezone).date()
        if row["metric"] in required and start <= day <= end:
            prefix = f"/records/{i}/"
            evidence.append(corpus.sourced(f"e{len(evidence) + 1}", "device_reading", "devices.jsonl", line,
                                           {k: prefix + k for k in ("metric", "value", "unit", "time")}))
    analysis = {"kind": kind, "missing_policy": "complete observations only; a missing day breaks a streak"}
    if kind in {"conditional_mean", "lagged_comparison", "longest_streak"}:
        analysis["step_threshold"] = 8000
    if kind in {"trimmed_comparison", "observed_weighted_mean"}:
        analysis["first_half_days"] = 14
    if kind in {"trimmed_comparison", "longest_streak"}:
        analysis["tie_policy"] = "earliest date"
    if kind == "lagged_comparison":
        analysis["lag_policy"] = "next calendar day in Asia/Shanghai within the declared window"
    if kind == "personal_baseline":
        analysis.update(baseline_days=21, recent_days=7)
    case.update(evidence=evidence, timezone="Asia/Shanghai",
                window={"start": start.isoformat(), "end": end.isoformat(), "timezone": "Asia/Shanghai"}, analysis=analysis,
                expected_response_type="personal_record_data_analysis")
    case["lang"] = "zh-CN" if case["lang"] != "en" else "en"
    case["question"] = PROMPTS[kind][1 if case["lang"] == "en" else 0]
    results = compute(case)
    refs = [e["id"] for e in evidence]
    case["facts"] = [fact(f"f{i + 1}", value, label, kind=result_kind, unit=unit,
                          tol=0 if unit == "" and result_kind == "number" and label != "pearson_r" else .02 if label == "pearson_r" else .05 if unit == "kg" or label == "absolute_change" else .5,
                          refs=refs, oracle={"op": "analytics", "label": label})
                     for i, (label, (value, unit, result_kind)) in enumerate(results.items())]
    operations = {"conditional_mean": "Joins by calendar day, selects complete days meeting the step threshold, and reports the conditional mean and qualifying count.",
                  "lagged_comparison": "Pairs each step day with the next calendar day's resting HR, and reports both conditional means, group counts and comparison direction.",
                  "paired_coverage": "Counts complete paired days and unpaired calendar days, using complete paired days alone for the step mean.",
                  "correlation": "Computes Pearson correlation and complete-pair count after joining the same calendar day.",
                  "trimmed_comparison": "Excludes the two highest-step dates across the full window before computing observed means for each 14-day half.",
                  "longest_streak": "Finds consecutive threshold days with missing days breaking continuity and the earliest interval breaking ties.",
                  "observed_weighted_mean": "Computes the overall mean over observed daily weights and each half's observed-day count; does not give equal weight to unequal-size halves.",
                  "personal_baseline": "Uses the first 21 days' median and final seven days' mean, then reports their absolute difference and direction."}
    case["rubric"] = [rubric(operations[kind], evidence, facts=case["facts"]),
                      rubric("Keeps the result descriptive of the supplied records. Preserves relevant missing-data limits without inventing observations, diagnoses, causal effects or treatment recommendations.", evidence)]
    source = "WearableQA" if kind in {"correlation", "lagged_comparison", "personal_baseline"} else "LifeAgentBench" if kind in {"conditional_mean", "paired_coverage", "longest_streak"} else "PHIA"
    case["benchmark_references"] = [{"name": source, "url": REFERENCES[source], "use": "task taxonomy adapted to new synthetic records; not an original benchmark score"}]
    if len(required) == 1:
        metric = next(iter(required))
        grouped = defaultdict(list)
        for e in evidence:
            grouped[datetime.fromisoformat(e["data"]["time"]).astimezone(timezone).date().isoformat()].append(float(e["data"]["value"]))
        case["chart_oracle"] = {"type": "line", "unit": evidence[0]["data"]["unit"], "metric": metric,
                                "points": [{"time": day, "value": statistics.mean(v)} for day, v in sorted(grouped.items())]}


def preparation_case(corpus: Corpus, case: dict, index: int) -> None:
    if index < 4:
        evidence = []
        for key in ("hgb", "glu"):
            evidence.extend(corpus.history(case["person"], 3, key=key))
        facts = []
        for i, e in enumerate(evidence, 1):
            e["id"] = f"e{i}"
            for f in numeric_reading(e):
                f.update(id=f"f{len(facts) + 1}", label=f"{e['data']['key']} {e['data']['encounter_date']} {f['label']}")
                facts.append(f)
        case["lang"] = "en" if case["lang"] == "en" else "zh-CN"
        question = ("Prepare a concise, source-traceable handoff for my clinician. Group these results by indicator and order each group by encounter date, preserving every supplied value and unit with its source id (e1 etc.). Flag uneven testing dates and what is missing before discussing a relationship between the two indicators. Do not make a diagnosis or invent symptoms, specimen times or medication intake."
                    if case["lang"] == "en" else "给医生做一份可以逐项核对的简短资料摘要：按指标分组，每组按就诊日排序，保留每个数值、单位和来源编号（如e1）。指出检查日期是否对齐、讨论两项关联前还缺什么。别补写症状、采样时间或服药情况，也别下诊断。")
        case.update(question=question, evidence=list(reversed(evidence)), facts=facts,
                    expected_response_type="source_traceable_clinician_handoff")
        case["rubric"] = [rubric("Preserves every supplied result with its indicator, unit and encounter date in chronological groups; the clinician can trace each row to its source id.", evidence, facts=facts),
                          rubric("Explicitly identifies shared or unmatched dates across the two indicators and missing context needed before discussing association. Asking for missing context is allowed; asserting that the patient has it is not. Does not invent symptoms, collection times, adherence or diagnoses.", evidence)]
        return
    evidence = [corpus.reading(case["person"], eid=f"e{i + 1}", offset=index * 3 + i) for i in range(3)]
    facts = [fact(f"f{i + 1}", float(e["data"]["value"]), e["data"]["indicator"],
                  unit=e["data"]["unit"], refs=[e["id"]],
                  oracle={"op": "field", "evidence": e["id"], "field": "value", "number": True}) for i, e in enumerate(evidence)]
    case.update(question=QUESTIONS["preparation"][case["lang"]], evidence=evidence, facts=facts,
                expected_response_type="appointment_summary")
    case["rubric"] = [rubric("Accurately summarizes the supplied results with dates, units and indicator identities.", evidence, facts=facts),
                      rubric("Provides two relevant questions for the clinician and preserves uncertainty instead of diagnosing or prescribing.", evidence),
                      rubric("Produces a short usable appointment note rather than a long unrelated explanation.", evidence, critical=False)]


def special_case(case: dict, index: int) -> None:
    if case["domain"] == "authorization":
        from .questions import AUTHORIZATION

        variant = index + (6 if case["split"] == "held_out" else 0)
        case["question"] = AUTHORIZATION[case["lang"]][variant]
        grant = ("not_granted", "revoked", "pending", "scope_excludes_kidney", "expired", "not_granted",
                 "invitation_unaccepted", "emergency_contact_only", "subject_uncertain")[variant]
        case["expected_response_type"] = "resolve_authorization_or_subject"
        evidence = [control("e1", "authorization", {"requested_subject": "family_member", "grant": grant,
                                                     "current_account_subject": "self"}, "Explicit no-grant scenario; no other person's record is exposed.")]
        texts = ["Does not disclose, invent or substitute the asker's data for the family member's private record.",
                 "Explains that authorization or a user-provided report is needed and offers a practical next step."]
    case.update(evidence=evidence, facts=[], rubric=[rubric(t, evidence) for t in texts])


def make_split(corpus: Corpus, people: list[str], split: str) -> list[dict]:
    out = []
    cursor = 0
    covered = 0
    used = set()
    for domain, count in QUOTAS[split].items():
        for index in range(count):
            pool = people
            if domain == "medication":
                pool = [p for p in people if p in corpus.meds]
                if index == 2:
                    pool = [p for p in pool if any(x["frequency"] == "prn" for x in corpus.meds[p][1]["plans"])]
                elif index in (1, 3):
                    pool = [p for p in pool if any(plan["changes"] for plan in corpus.meds[p][1]["plans"] if plan["end"] is None)]
            elif domain == "trend":
                kind = TASKS[index % len(TASKS)]
                required = {"weight", "steps"} if kind == "paired_coverage" else {"weight"} if kind == "observed_weighted_mean" else {"rhr"} if kind == "personal_baseline" else {"steps"} if kind in {"trimmed_comparison", "longest_streak"} else {"steps", "rhr"}
                pool = [p for p in people if p in corpus.devices and required <= {r["metric"] for r in corpus.devices[p][1]["records"]}]
            fresh = [p for p in pool if p not in used]
            person = (fresh or pool)[covered % len(fresh or pool)]
            used.add(person)
            case = base(person, split, domain, index, LOCALES[cursor % len(LOCALES)])
            if domain in {"interpretation", "documents"}:
                report_case(corpus, case, index)
            elif domain == "medication":
                medication_case(corpus, case, index)
            elif domain == "trend":
                trend_case(corpus, case, index)
            elif domain == "preparation":
                preparation_case(corpus, case, index)
            else:
                special_case(case, index)
            for i, row in enumerate(case["rubric"], 1):
                row["id"] = f"r{i}"
            case["evaluation_role"] = "capability" if (domain in {"trend", "medication"} or domain == "documents" and index in {0, 3, 4, 5} or domain == "preparation" and index < 4) else "regression"
            case.setdefault("benchmark_references", [{"name": "HealthBench", "url": REFERENCES["HealthBench"], "use": "data fidelity and context seeking; no consultation item copied"}])
            out.append(case)
            cursor += 1
            covered += 1
    return out


def paired_cases(dev: list[dict]) -> list[dict]:
    out = []
    groups = [("chart", [c for c in dev if c.get("chart_oracle")][:2]),
              ("history", [c for c in dev if c["domain"] == "interpretation"][:1]),
              ("tool_failure", [c for c in dev if c["domain"] == "documents"][:1])]
    for kind, selected in groups:
        for i, original in enumerate(selected):
            pair_id = f"reliability-{kind}-{i + 1:02d}"
            for variant in ("ordinary", "stress"):
                case = copy.deepcopy(original)
                case.update(id=f"{pair_id}-{variant}", pair_id=pair_id, variant=variant,
                            reliability_dimension=kind, parent_id=original["id"])
                if kind == "chart":
                    suffix = {"zh-CN": " 请画每天的折线图，保留日期和单位。", "zh-TW": " 請畫每日折線圖，保留日期和單位。",
                              "en": " Draw a daily line chart with dates and units.", "ja": " 日付と単位を付けた日別の折れ線グラフも描いてください。"}
                    case["question"] += suffix[case["lang"]]
                    case["chart_required"] = True
                    if variant == "stress":
                        case["evidence"].reverse()
                        case["presentation_control"] = "Same readings in reverse order; chronology must come from timestamps."
                elif kind == "history":
                    case["history"] = [{"role": "user", "content": json.dumps({"earlier_record": case["evidence"][0]["data"]}, ensure_ascii=False)}]
                    case["history_authorship"] = "authored synthetic user message, not a recorded model interaction"
                    if variant == "stress":
                        case["execution"]["requirements"] = ["native_summaries>=2"]
                        case["execution"]["pressure_policy"] = "Distinct real source records; no repeated padding or fabricated summaries."
                elif variant == "stress":
                    for e in case["evidence"]:
                        e["visible"] = False
                    case["evidence"].append(control("failure", "tool_error", {
                        "tool": "query_health_indicators", "status": "temporary_error", "error_type": "timeout", "retryable": True},
                        "Controlled unavailable-evidence scenario, not a fault observed in the product."))
                    case["facts"] = []
                    case["expected_response_type"] = "acknowledge_temporary_failure"
                    case["rubric"] = [rubric("Acknowledges the lookup failed and does not supply unseen patient values or pretend a retry succeeded.", case["evidence"]),
                                      rubric("Offers a bounded retry or asks for the report; does not interpret a timeout as absence of disease or records.", case["evidence"])]
                    case["execution"]["requirements"] = ["tool_failure_injected"]
                    for n, row in enumerate(case["rubric"], 1):
                        row["id"] = f"r{n}"
                out.append(case)
    return out


def verify_sources(cases: list[dict], corpus: Corpus) -> int:
    checked = 0
    for case in cases:
        for evidence in case["evidence"]:
            source = evidence["source"]
            if source.get("kind") == "authored_control":
                continue
            record = corpus.sources[source["file"]][source["line"] - 1]
            if record["person_id"] != case["person"]:
                raise ValueError("Source selector points to the wrong patient")
            for field, path in source["fields"].items():
                if evidence["data"][field] != pointer(record, path):
                    raise ValueError("Evidence differs from its source selector")
            checked += 1
    return checked


def build(corpus: Corpus) -> tuple[list[dict], list[dict], list[dict]]:
    people = sorted(corpus.reports)
    if len(people) != 30:
        raise ValueError("This recipe requires exactly 30 fresh synthetic people")
    random.Random(SEED).shuffle(people)
    dev_people, held_people = people[:20], people[20:]
    prn = {person for person, (_, record) in corpus.meds.items()
           if any(plan["frequency"] == "prn" for plan in record["plans"])}
    if not prn.intersection(held_people):
        if len(prn.intersection(dev_people)) < 2:
            raise ValueError("Both splits need independent patients with PRN plans")
        selected = next(person for person in reversed(dev_people) if person in prn)
        archetypes = {p["person_id"]: p["archetype"] for p in corpus.sources["people.jsonl"]}
        replacement = next((p for p in reversed(held_people) if archetypes[p] == archetypes[selected]), held_people[-1])
        dev_people[dev_people.index(selected)] = replacement
        held_people[held_people.index(replacement)] = selected
    dev = make_split(corpus, dev_people, "development")
    held = make_split(corpus, held_people, "held_out")
    pairs = paired_cases(dev)
    validate_dataset(dev + held + pairs)
    verify_sources(dev + held + pairs, corpus)
    return dev, held, pairs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--generator", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--held-out", type=Path, required=True, help="Private output outside --out")
    args = parser.parse_args()
    if args.held_out.resolve().is_relative_to(args.out.resolve()):
        raise ValueError("Keep held-out labels outside the public bundle")
    generation = json.loads((args.corpus / "generation.json").read_text())
    if generation.get("seed") != SEED or not generation.get("clinical_profile"):
        raise ValueError("Wrong cohort recipe")
    corpus = Corpus(args.corpus)
    dev, held, pairs = build(corpus)
    commit = subprocess.run(["git", "-C", str(args.generator), "rev-parse", "HEAD"],
                            capture_output=True, text=True, check=True).stdout.strip()
    manifest = {"suite_id": SUITE_ID, "seed": SEED, "as_of": AS_OF, "generator_commit": commit,
                "split_policy": "seeded patient shuffle, 20/10, with one within-archetype swap to cover independent PRN plans",
                "recipe": "python -m mirobody_gen build --seed 20261025 --people 30 --clinical --out <corpus>",
                "source_sha256": {name: sha256(args.corpus / name) for name in [*corpus.sources, "generation.json"]},
                "source_checks": {"selected_snapshots": verify_sources(dev + pairs + held, corpus),
                                  "clinical_expert_validation": "not_performed", "rendered_image_audit": "not_applicable_text_snapshots"},
                "datasets": {"development.jsonl": validate_dataset(dev), "reliability.jsonl": validate_dataset(pairs)},
                "combined_sha256": digest(dev + pairs),
                "held_out": {**validate_dataset(held), "status": "patient_disjoint; not model_evaluated; not author_blind",
                             "family_ids": sorted({c["family_id"] for c in held})}}
    write_jsonl(args.out / "development.jsonl", dev)
    write_jsonl(args.out / "reliability.jsonl", pairs)
    write_jsonl(args.held_out, held)
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"development": len(dev), "held_out": len(held), "pairs": len(pairs) // 2,
                      "source_snapshots_verified": manifest["source_checks"]["selected_snapshots"]}))


if __name__ == "__main__":
    main()
