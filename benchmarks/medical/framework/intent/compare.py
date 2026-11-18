"""Compare two same-case score files without dropping failed evaluations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .run import DATA
from .schema import digest, read_jsonl, verify_bundle


def compare(cases: list[dict], left: list[dict], right: list[dict]) -> dict:
    known = {c["id"]: c for c in cases}

    def index(rows):
        result = {}
        for row in rows:
            cid = row["id"]
            if cid not in known or cid in result:
                raise ValueError("Unknown or duplicate scored case")
            if row.get("status") == "ok" and row.get("case_sha256") != digest(known[cid]):
                raise ValueError("A successful score is bound to a different case")
            result[cid] = row
        return result

    maps = [index(left), index(right)]
    items = []
    for case in cases:
        item = {"id": case["id"], "role": case["evaluation_role"], "domain": case["domain"],
                "task": case.get("analysis", {}).get("kind", case["expected_response_type"])}
        for label, rows in zip(("left", "right"), maps, strict=True):
            row = rows.get(case["id"], {})
            valid = row.get("status") == "ok"
            grade = row.get("grade", {})
            item[label] = {"status": row.get("status", "missing"),
                           "pass": grade.get("evidence_conditioned_pass") if valid else None,
                           "facts_pass": all(f["ok"] for f in grade["facts"]) if valid and grade.get("facts") else None,
                           "fact_fraction": grade.get("fact_fraction") if valid else None,
                           "criterion_fraction": grade.get("criterion_fraction") if valid else None}
        items.append(item)
    groups = {}
    for role in ("capability", "regression"):
        selected = [i for i in items if i["role"] == role]
        joint = [i for i in selected if all(i[x]["status"] == "ok" for x in ("left", "right"))]
        result = {"selected": len(selected), "joint_valid": len(joint),
                  "left_only_pass": [i["id"] for i in joint if i["left"]["pass"] and not i["right"]["pass"]],
                  "right_only_pass": [i["id"] for i in joint if i["right"]["pass"] and not i["left"]["pass"]],
                  "left_only_facts_pass": [i["id"] for i in joint if i["left"]["facts_pass"] is True and i["right"]["facts_pass"] is False],
                  "right_only_facts_pass": [i["id"] for i in joint if i["right"]["facts_pass"] is True and i["left"]["facts_pass"] is False]}
        for label in ("left", "right"):
            result[label] = {"confirmed_pass": sum(i[label]["pass"] is True for i in selected),
                             "valid_reviews": sum(i[label]["status"] == "ok" for i in selected),
                             "unresolved": sum(i[label]["status"] != "ok" for i in selected)}
        groups[role] = result
    return {"cases_sha256": digest(cases), "groups": groups, "items": items,
            "scope": "Exploratory development calibration; unresolved judgments stay in the selected denominator. Not a product-harness ranking."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--left", type=Path, required=True)
    parser.add_argument("--right", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    verify_bundle(args.data)
    if args.out.resolve().is_relative_to(args.data.resolve()):
        parser.error("Keep calibration results outside the frozen fixtures")
    result = compare(read_jsonl(args.data / "development.jsonl"), read_jsonl(args.left), read_jsonl(args.right))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result["groups"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
