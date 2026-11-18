"""Recompute answer keys from the visible snapshot, independently of question builders."""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo


def daily_points(case: dict) -> tuple[list[dict], str]:
    days: dict[str, list[float]] = defaultdict(list)
    units = set()
    timezone = ZoneInfo(case["window"].get("timezone", "UTC"))
    for evidence in case["evidence"]:
        if evidence["visible"] and evidence["kind"] == "device_reading":
            data = evidence["data"]
            day = datetime.fromisoformat(data["time"]).astimezone(timezone).date().isoformat()
            if case["window"]["start"] <= day <= case["window"]["end"]:
                days[day].append(float(data["value"]))
                units.add(data["unit"])
    if len(units) != 1:
        raise ValueError("A daily oracle needs one observed unit")
    return [{"time": day, "value": statistics.mean(values)} for day, values in sorted(days.items())], units.pop()


def recompute(case: dict, rule: dict) -> tuple[object, str]:
    evidence = {e["id"]: e["data"] for e in case["evidence"] if e["visible"]}
    operation = rule["op"]
    if operation == "field":
        record = evidence[rule["evidence"]]
        value = record["strength"].split()[0] if rule["field"] == "strength_amount" else record[rule["field"]]
        if rule["field"] == "strength_amount":
            return float(value), record["strength"].split()[1]
        return (float(value), record["unit"]) if rule.get("number") else (value, "")
    if operation == "range_position":
        record = evidence[rule["evidence"]]
        value, low, high = float(record["value"]), record["ref_low"], record["ref_high"]
        return ("below" if low is not None and value < low else "above" if high is not None and value > high else "within"), ""
    if operation == "effective_plan":
        record = evidence[rule["evidence"]]
        versions = [{"date": record["start"], "strength": record["strength"]}, *record["changes"]]
        applicable = [v for v in versions if v["date"] <= case["as_of"]]
        latest = {"frequency": record["frequency"]}
        for version in sorted(applicable, key=lambda item: item["date"]):
            latest.update(version)
        if rule["field"] == "strength_amount":
            amount, unit = latest["strength"].split()
            return float(amount), unit
        if rule["field"] == "date":
            return latest["date"], ""
        if rule["field"] == "frequency":
            return latest["frequency"], ""
        raise ValueError("Unknown effective plan field")
    if operation == "analytics":
        from .analytics import compute

        value, unit, _ = compute(case)[rule["label"]]
        return value, unit
    if operation == "record_difference":
        first, last = evidence[rule["first"]], evidence[rule["last"]]
        if first["unit"] != last["unit"]:
            raise ValueError("Cannot subtract incompatible report units")
        delta = float(last["value"]) - float(first["value"])
        return (abs(delta), last["unit"]) if rule["field"] == "absolute" else ("higher" if delta > 0 else "lower" if delta < 0 else "unchanged", "")
    raise ValueError("Unknown answer-key oracle")


def validate_oracles(case: dict) -> None:
    for fact in case["facts"]:
        if "oracle" not in fact:
            raise ValueError("A source-derived fact is missing its oracle")
        try:
            value, unit = recompute(case, fact["oracle"])
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise ValueError("Cannot recompute fact from visible evidence") from exc
        matches = abs(float(value) - fact["value"]) < 1e-9 if fact["kind"] == "number" else value == fact["value"]
        if not matches or (fact["kind"] == "number" and unit != fact["unit"]):
            raise ValueError("Answer key differs from visible-evidence oracle")
    if case.get("chart_required"):
        points, unit = daily_points(case)
        if points != case["chart_oracle"]["points"] or unit != case["chart_oracle"]["unit"]:
            raise ValueError("Chart oracle differs from visible source points")
