"""Validate cases, review their quality, or calibrate evidence-conditioned answers."""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .evaluate import VERSION, answer_lines, audit_messages, grade, review_messages, validate_audit
from .models import PROVIDERS, ModelError, call, request_body
from .schema import answer_packet, digest, read_jsonl, verify_bundle, write_jsonl

DATA = Path(__file__).resolve().parents[2] / "data" / "intent"
ANSWER_SYSTEM = (
    "Answer this SYNTHETIC personal health-record question in the requested language. "
    "Use only the visible record evidence for patient facts; keep dates, indicators and units bound correctly. "
    "Do not infer actual medication intake from plans, missing values from absence, a diagnosis from one result, "
    "or authorization for another person's record. Ask for missing information when needed. "
    "Support personal data management and clinician preparation; explain data limitations clearly. "
    "You have text snapshots only, not image access or actual tools. Never claim that you queried, retried, "
    "opened a source or completed a native context summary. "
    "If a chart is requested, use a ```vis-chart fenced JSON object with type=line, unit, "
    "and data entries containing time (ISO date) and value (number)."
)


def select(cases: list[dict], *, smoke: bool = False, limit: int = 0, role: str = "all") -> list[dict]:
    if role != "all":
        cases = [c for c in cases if c["evaluation_role"] == role]
    if smoke:
        counts: Counter = Counter()
        cases = [c for c in cases if (counts.update([c["domain"]]) or counts[c["domain"]] <= 2)]
    return cases[:limit] if limit else cases


def messages_for(operation: str, case: dict, answer: str = "") -> list[dict]:
    if operation == "audit":
        return audit_messages(case)
    if operation == "score":
        return review_messages(case, answer)
    return [{"role": "system", "content": ANSWER_SYSTEM},
            {"role": "user", "content": json.dumps(answer_packet(case), ensure_ascii=False)}]


def identity(operation: str, case: dict, provider: str, model: str, messages: list[dict], receipts: dict,
             settings: dict | None = None) -> str:
    settings = settings or {}
    return digest({"operation": operation, "case": case, "provider_endpoint": PROVIDERS[provider][0],
                   "body": request_body(provider, model, messages, json_output=operation != "baseline",
                                        **{k: v for k, v in settings.items() if k != "timeout_seconds"}),
                   "transport_timeout_seconds": settings.get("timeout_seconds", 90),
                   "receipts": receipts, "evaluator_version": VERSION})


def one(operation: str, case: dict, provider: str, model: str, env_file: Path | None,
        out: Path, answer_record: dict | None = None, settings: dict | None = None) -> dict:
    answer_record = answer_record or {}
    settings = settings or {}
    if operation == "score" and answer_record.get("status") != "ok":
        return {"id": case["id"], "status": "answer_failure", "error_type": "MissingOrFailedAnswer"}
    if operation == "score" and answer_record.get("case_sha256") != digest(case):
        return {"id": case["id"], "status": "answer_failure", "error_type": "MissingOrMismatchedCaseHash"}
    answer = answer_record.get("answer", "")
    receipts = answer_record.get("receipts", {})
    if operation == "score" and (not isinstance(answer, str) or not answer.strip() or not isinstance(receipts, dict)):
        return {"id": case["id"], "status": "answer_failure", "error_type": "InvalidAnswerOrReceipts"}
    messages = messages_for(operation, case, answer)
    key = identity(operation, case, provider, model, messages, receipts, settings)
    cache = out / "cache" / f"{key}.json"
    if cache.exists():
        saved = json.loads(cache.read_text())
        if saved.get("request_sha256") != key or saved.get("status") != "ok":
            raise ValueError("Invalid cached review identity")
        saved["cache_hit"] = True
        return saved
    start = time.monotonic()
    result = {"id": case["id"], "case_sha256": digest(case), "request_sha256": key,
              "operation": operation, "evaluator_version": VERSION,
              "generation_settings": settings,
              "scope": "synthetic evidence-conditioned calibration; not a Mirobody harness run"}
    try:
        response = call(provider, model, messages, env_file, json_output=operation != "baseline", **settings)
        result["model"] = {k: v for k, v in response.items() if k != "content"}
        result["raw_response"] = response["content"]
        if operation == "baseline":
            result["answer"] = response["content"]
        elif operation == "audit":
            result["audit"] = validate_audit(decode_json(response["content"]), case)
        else:
            verdict = decode_json(response["content"])
            result["answer_sha256"] = digest(answer)
            result["answer_lines"] = answer_lines(answer)
            result["grade"] = grade(case, answer, verdict, receipts)
            result["verdict"] = verdict
        result.update(status="ok", seconds=round(time.monotonic() - start, 3), cache_hit=False)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    except (ModelError, ValueError, TypeError) as exc:
        result.update(status="answer_failure" if operation == "baseline" else "evaluator_failure", error_type=type(exc).__name__,
                      error_category=str(exc),
                      seconds=round(time.monotonic() - start, 3))
    return result


def decode_json(content: str) -> dict:
    fence = re.fullmatch(r"\s*```(?:json)?\s*(.*?)\s*```\s*", content, re.S)
    return json.loads(fence.group(1) if fence else content)


def summarize(operation: str, cases: list[dict], rows: list[dict]) -> dict:
    statuses = Counter(r["status"] for r in rows)
    models = Counter((r.get("model") or {}).get("response_model", "unavailable") for r in rows)
    usage: Counter = Counter()
    for row in rows:
        for name, count in (row.get("model", {}).get("usage") or {}).items():
            if type(count) is int:
                usage[name] += count
    summary = {"operation": operation, "evaluator_version": VERSION, "selected": len(cases),
               "statuses": dict(statuses), "actual_models": dict(models), "usage": dict(usage),
               "scope": "evidence-conditioned; no product-harness, OCR, authorization or native compaction claim",
               "cases_sha256": digest(cases)}
    if operation == "audit":
        summary["issue_cases"] = sum(any(c["status"] == "issue" for c in r.get("audit", {}).get("assessments", [])) for r in rows)
        summary["not_assessable_cases"] = sum(any(c["status"] == "not_assessable" for c in r.get("audit", {}).get("assessments", [])) for r in rows)
    elif operation == "score":
        summary["evidence_conditioned_pass"] = sum(r.get("grade", {}).get("evidence_conditioned_pass") is True for r in rows)
        summary["execution_not_exercised"] = sum(r.get("grade", {}).get("execution_status") == "not_exercised" for r in rows)
        by_id = {r["id"]: r for r in rows}
        summary["roles"] = {}
        for role in ("capability", "regression"):
            selected = [c for c in cases if c.get("evaluation_role") == role]
            results = [by_id.get(c["id"], {}) for c in selected]
            summary["roles"][role] = {"selected": len(selected),
                "pass": sum(r.get("grade", {}).get("evidence_conditioned_pass") is True for r in results),
                "valid_reviews": sum(r.get("status") == "ok" for r in results)}
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["validate", "audit", "baseline", "score"])
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--dataset", choices=["development", "reliability"], default="development")
    parser.add_argument("--provider", choices=PROVIDERS)
    parser.add_argument("--model")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--answers", type=Path)
    parser.add_argument("--smoke", action="store_true", help="First two cases per domain")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--role", choices=["all", "capability", "regression"], default="all")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--max-output-tokens", type=int, default=2400)
    parser.add_argument("--reasoning-effort", choices=["none", "low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--timeout-seconds", type=int, default=90)
    args = parser.parse_args()
    if args.limit < 0 or not 1 <= args.workers <= 8:
        parser.error("Use a nonnegative limit and 1–8 workers")
    if not 1 <= args.max_output_tokens <= 128000 or not 1 <= args.timeout_seconds <= 600:
        parser.error("Use 1–128000 output tokens and a 1–600 second request timeout")
    if args.reasoning_effort is not None and args.provider not in {"openrouter", "openai", "nebula"}:
        parser.error("Reasoning effort requires OpenAI, OpenRouter or Nebula")
    validated = verify_bundle(args.data)
    if args.operation == "validate":
        print(json.dumps(validated, ensure_ascii=False, indent=2))
        return 0
    if not args.provider or not args.model or not args.out:
        parser.error("Model calls require explicit --provider, --model and --out")
    if args.out.resolve().is_relative_to(args.data.resolve()):
        parser.error("Keep model artifacts outside the frozen dataset")
    cases = select(read_jsonl(args.data / f"{args.dataset}.jsonl"), smoke=args.smoke, limit=args.limit, role=args.role)
    answers = {}
    if args.operation == "score":
        if not args.answers:
            parser.error("score requires --answers")
        answer_rows = read_jsonl(args.answers)
        answers = {r["id"]: r for r in answer_rows}
        known = {c["id"] for c in read_jsonl(args.data / f"{args.dataset}.jsonl")}
        if len(answers) != len(answer_rows) or not set(answers) <= known:
            raise ValueError("Duplicate or unknown submitted answers")
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    settings = {"max_output_tokens": args.max_output_tokens, "reasoning_effort": args.reasoning_effort,
                "timeout_seconds": args.timeout_seconds}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending = [pool.submit(one, args.operation, c, args.provider, args.model, args.env_file,
                               args.out, answers.get(c["id"]), settings) for c in cases]
        for future in as_completed(pending):
            rows.append(future.result())
            if len(rows) % 5 == 0 or len(rows) == len(cases):
                print(json.dumps({"completed": len(rows), "selected": len(cases),
                                  "failures": sum(r["status"] != "ok" for r in rows)}), flush=True)
    order = {c["id"]: i for i, c in enumerate(cases)}
    rows.sort(key=lambda row: order[row["id"]])
    name = "answers" if args.operation == "baseline" else args.operation
    write_jsonl(args.out / f"{name}.jsonl", rows)
    summary = summarize(args.operation, cases, rows)
    (args.out / f"{name}-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return int(any(r["status"] != "ok" for r in rows))


if __name__ == "__main__":
    raise SystemExit(main())
