"""Versioned evidence and split contracts for synthetic intent tasks."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from datetime import date, timedelta
from pathlib import Path, PurePosixPath

SUITE_ID = "intent-20261025"
LANGUAGES = {"zh-CN", "zh-TW", "en", "ja"}
DOMAINS = {"interpretation", "medication", "trend", "documents", "preparation", "authorization"}


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
                            for row in rows), encoding="utf-8")


def _ids(rows: list[dict], label: str) -> set[str]:
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValueError(f"Invalid {label} entries")
    found = [row.get("id") for row in rows]
    if any(not isinstance(i, str) or not i for i in found) or len(found) != len(set(found)):
        raise ValueError(f"Duplicate or missing {label} ids")
    return set(found)


def _finite(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def validate_case(case: dict) -> None:
    if case.get("synthetic") is not True or case.get("suite_id") != SUITE_ID:
        raise ValueError("Only versioned synthetic cases are accepted")
    for key in ("id", "family_id", "question", "as_of"):
        if not isinstance(case.get(key), str) or not case[key].strip():
            raise ValueError(f"Missing {key}")
    date.fromisoformat(case["as_of"])
    if case.get("lang") not in LANGUAGES or case.get("domain") not in DOMAINS:
        raise ValueError("Invalid locale or domain")
    if case.get("split") not in {"development", "held_out"}:
        raise ValueError("Invalid split")
    evidence = case.get("evidence", [])
    evidence_ids = _ids(evidence, "evidence")
    for row in evidence:
        if type(row.get("visible")) is not bool or not isinstance(row.get("data"), dict):
            raise ValueError("Evidence needs explicit visibility and structured data")
        source = row.get("source", {})
        if source.get("kind") == "authored_control":
            if not source.get("control_id") or not source.get("reason"):
                raise ValueError("Authored scenario controls need provenance")
        else:
            rel = PurePosixPath(source.get("file", ""))
            if not source.get("file") or rel.is_absolute() or ".." in rel.parts:
                raise ValueError("Invalid source path")
            if type(source.get("line")) is not int or source["line"] < 1:
                raise ValueError("Invalid source line")
    visible = {e["id"] for e in evidence if e["visible"]}
    facts = case.get("facts", [])
    fact_ids = _ids(facts, "fact")
    for fact in facts:
        refs = fact.get("evidence_ids", [])
        if not refs or not set(refs) <= visible:
            raise ValueError("Answer facts require visible evidence")
        kind, value = fact.get("kind"), fact.get("value")
        if kind == "number":
            if not _finite(value):
                raise ValueError("Invalid fact number")
            if not _finite(fact.get("tol")) or fact["tol"] < 0:
                raise ValueError("Invalid numeric tolerance")
        elif kind in {"date", "text"}:
            if not isinstance(value, str) or not value:
                raise ValueError("Invalid fact text")
            if kind == "date":
                date.fromisoformat(value)
        else:
            raise ValueError("Unknown fact kind")
    rubric = case.get("rubric", [])
    _ids(rubric, "rubric")
    if not rubric:
        raise ValueError("Each task needs an explicit rubric")
    for row in rubric:
        if not row.get("text") or type(row.get("critical")) is not bool:
            raise ValueError("Invalid rubric criterion")
        if not set(row.get("fact_ids", [])) <= fact_ids or not set(row.get("evidence_ids", [])) <= evidence_ids:
            raise ValueError("Unknown rubric evidence or fact")
    for message in case.get("history", []):
        if message.get("role") not in {"user", "assistant"} or not isinstance(message.get("content"), str):
            raise ValueError("Invalid authored conversation history")
    if case.get("execution", {}).get("mode") != "evidence_conditioned":
        raise ValueError("Declare the evaluation scope explicitly")
    digest(case)
    if any("oracle" in fact for fact in facts) or case.get("chart_required"):
        from .oracles import validate_oracles

        validate_oracles(case)


def validate_dataset(cases: list[dict]) -> dict:
    _ids(cases, "case")
    families: dict[str, str] = {}
    pairs: dict[str, list[dict]] = {}
    for case in cases:
        validate_case(case)
        if families.setdefault(case["family_id"], case["split"]) != case["split"]:
            raise ValueError("A patient family leaks across splits")
        if case.get("pair_id"):
            pairs.setdefault(case["pair_id"], []).append(case)
    for pair in pairs.values():
        if len(pair) != 2 or {c.get("variant") for c in pair} != {"ordinary", "stress"}:
            raise ValueError("Each reliability pair needs ordinary and stress variants")
        if len({c["family_id"] for c in pair}) != 1:
            raise ValueError("A reliability pair changes the patient family")
    return {"cases": len(cases), "families": len(families), "pairs": len(pairs),
            "domains": dict(Counter(c["domain"] for c in cases)),
            "locales": dict(Counter(c["lang"] for c in cases)), "sha256": digest(cases)}


def answer_packet(case: dict) -> dict:
    validate_case(case)
    packet = {"question": case["question"], "language": case["lang"], "as_of": case["as_of"],
            "history": case.get("history", []),
            "evidence": [{"id": e["id"], "kind": e["kind"], "data": e["data"]}
                         for e in case["evidence"] if e["visible"]]}
    if case.get("window"):
        start = date.fromisoformat(case["window"]["start"])
        window = {**case["window"],
            "aggregation": "Daily means in the declared account timezone, then the requested operation over observed days; missing days omitted, never zero."}
        kind = case.get("analysis", {}).get("kind")
        if kind in {"trimmed_comparison", "observed_weighted_mean"}:
            window.update(first_half=[start.isoformat(), (start + timedelta(days=13)).isoformat()],
                          second_half=[(start + timedelta(days=14)).isoformat(), case["window"]["end"]])
        elif kind == "personal_baseline":
            window.update(baseline_period=[start.isoformat(), (start + timedelta(days=20)).isoformat()],
                          recent_week=[(start + timedelta(days=21)).isoformat(), case["window"]["end"]])
        packet["comparison_window"] = window
    if case.get("analysis"):
        packet["analysis_parameters"] = case["analysis"]
    return packet


def verify_bundle(directory: Path) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text())
    combined = []
    for name, expected in manifest["datasets"].items():
        path = directory / name
        if path.parent != directory or not path.is_file():
            raise ValueError("Dataset file is missing or outside the bundle")
        rows = read_jsonl(path)
        actual = validate_dataset(rows)
        if actual != expected:
            raise ValueError(f"Dataset manifest mismatch: {name}")
        combined.extend(rows)
    summary = validate_dataset(combined)
    if summary["sha256"] != manifest["combined_sha256"]:
        raise ValueError("Combined manifest mismatch")
    return {**summary, "held_out": manifest["held_out"]}
