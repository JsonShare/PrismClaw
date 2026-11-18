"""用 mirobody 的 intent 数据集评测 PrismClaw 医疗 Agent。

数据集与评测框架在 benchmarks/medical/data/ 和 benchmarks/medical/framework/。

流程：读取 development.jsonl 中每条任务的可见证据包（answer_packet），
作为用户消息发给 PrismClaw 的产品对话接口（/api/v1/chat/completions），
再用 mirobody 自带的 review_messages 让另一个模型打分，最后用其 grade() 做确定性校验。

    python benchmarks/medical/eval_intent.py --limit 4      # 小样本试跑
    python benchmarks/medical/eval_intent.py               # 全量 32 条

结果写入 eval/mirobody-intent/<时间戳>/：answers.jsonl、score.jsonl、report.md。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA = Path(__file__).resolve().parent / "data" / "intent" / "development.jsonl"
DEFAULT_OUT = PROJECT_ROOT / "eval" / "mirobody-intent"
SERVER_CONFIG = PROJECT_ROOT / "backend" / "configs" / "server.yaml"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def mint_token(user_id: str, secret: str, ttl: int = 7200) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    now = int(time.time())
    payload = _b64(json.dumps({"sub": user_id, "iat": now, "exp": now + ttl}).encode())
    sig = _b64(hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


def read_jwt_secret(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"^\s*jwt_secret:\s*[\"']?([^\"'\n]+)", text, re.M)
    if not match:
        raise SystemExit(f"无法从 {path} 读取 jwt_secret")
    return match.group(1).strip()


def call_agent(client: httpx.Client, base: str, token: str, content: str,
               model: str, timeout: float) -> tuple[str, str | None, float]:
    started = time.time()
    try:
        resp = client.post(
            f"{base}/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": model, "stream": False,
                  "messages": [{"role": "user", "content": content}]},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        return "", f"{type(exc).__name__}: {exc}", time.time() - started
    latency = time.time() - started
    if resp.status_code != 200:
        return "", f"HTTP {resp.status_code}: {resp.text[:500]}", latency
    body = resp.json()
    choices = body.get("choices") or []
    if not choices:
        return "", f"空 choices: {json.dumps(body, ensure_ascii=False)[:500]}", latency
    return choices[0].get("message", {}).get("content", ""), None, latency


def extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start < 0:
        raise ValueError("judge 输出中没有 JSON 对象")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("judge JSON 括号不闭合")


def call_judge(client: httpx.Client, url: str, model: str, api_key: str,
               messages: list[dict], timeout: float) -> str:
    resp = client.post(url, headers={"Authorization": f"Bearer {api_key}"},
                       json={"model": model, "messages": messages,
                             "temperature": 0, "max_tokens": 8192,
                             "chat_template_kwargs": {"enable_thinking": False}},
                       timeout=timeout)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def judge_case(client: httpx.Client, review_messages, grade, case: dict, answer: str,
               url: str, model: str, api_key: str, timeout: float) -> tuple[dict | None, str | None, dict | None]:
    messages = review_messages(case, answer)
    last_error = ""
    for attempt in range(3):
        try:
            raw = call_judge(client, url, model, api_key, messages, timeout)
            verdict = extract_json(raw)
            graded = grade(case, answer, verdict)
            return verdict, None, graded
        except Exception as exc:  # noqa: BLE001 — judge 输出不可信，逐条记录失败
            last_error = f"{type(exc).__name__}: {exc}"
            messages = messages + [{"role": "user",
                                    "content": "Your previous output was invalid: "
                                               f"{last_error}. Return ONLY the JSON object, no prose."}]
    return None, last_error, None


def summarize(records: list[dict]) -> dict:
    ok = [r for r in records if r["grade"] is not None]

    def block(rows: list[dict]) -> dict:
        graded = [r for r in rows if r["grade"] is not None]
        if not graded:
            return {"n": len(rows), "graded": 0, "semantic_pass": 0, "semantic_rate": 0.0,
                    "evidence_conditioned_pass": 0, "fact_fraction_avg": None,
                    "criterion_fraction_avg": None,
                    "answer_failures": sum(1 for r in rows if r["status"] == "answer_failure"),
                    "evaluator_failures": sum(1 for r in rows if r["status"] == "evaluator_failure")}
        facts = [r["grade"]["fact_fraction"] for r in graded if r["grade"]["fact_fraction"] is not None]
        return {
            "n": len(rows),
            "graded": len(graded),
            "semantic_pass": sum(r["grade"]["semantic_pass"] for r in graded),
            "semantic_rate": round(sum(r["grade"]["semantic_pass"] for r in graded) / len(graded), 4),
            "evidence_conditioned_pass": sum(r["grade"]["evidence_conditioned_pass"] for r in graded),
            "fact_fraction_avg": round(sum(facts) / len(facts), 4) if facts else None,
            "criterion_fraction_avg": round(
                sum(r["grade"]["criterion_fraction"] for r in graded) / len(graded), 4),
            "answer_failures": sum(1 for r in rows if r["status"] == "answer_failure"),
            "evaluator_failures": sum(1 for r in rows if r["status"] == "evaluator_failure"),
        }

    by = defaultdict(list)
    for r in records:
        by[("role", r["evaluation_role"])].append(r)
        by[("domain", r["domain"])].append(r)
        by[("lang", r["lang"])].append(r)
    summary = {"overall": block(records), "by": {f"{k[0]}:{k[1]}": block(v) for k, v in sorted(by.items())}}
    summary["latency_s"] = {
        "mean": round(sum(r["latency_s"] for r in records) / len(records), 2) if records else None,
        "max": round(max(r["latency_s"] for r in records), 2) if records else None,
    }
    return summary


def write_report(path: Path, summary: dict, records: list[dict], meta: dict) -> None:
    lines = [
        "# PrismClaw × mirobody intent 评测报告",
        "",
        f"- 时间：{meta['when']}",
        f"- 被测系统：{meta['base']}（agent 模型 `{meta['model']}`）",
        f"- 评分模型：{meta['judge_model']}（mirobody evaluator {meta['evaluator']}）",
        f"- 数据集：{meta['data']}，共 {meta['cases']} 条",
        "",
        "## 总体",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
    ]
    overall = summary["overall"]
    lines += [
        f"| 已评分 / 总数 | {overall['graded']} / {overall['n']} |",
        f"| 语义通过（critical rubric 全满足） | {overall['semantic_pass']}（{overall['semantic_rate']:.1%}） |",
        f"| 证据条件通过（语义 + 数值 + 图表） | {overall['evidence_conditioned_pass']} |",
        f"| 数值事实平均正确率 | {overall['fact_fraction_avg']} |",
        f"| rubric 条目平均满足率 | {overall['criterion_fraction_avg']} |",
        f"| 回答失败 / 评分失败 | {overall['answer_failures']} / {overall['evaluator_failures']} |",
        f"| 单条耗时（均值 / 最大） | {summary['latency_s']['mean']}s / {summary['latency_s']['max']}s |",
        "",
        "## 分组",
        "",
        "| 分组 | n | 已评分 | 语义通过 | 事实正确率 | rubric 满足率 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for key, row in summary["by"].items():
        if not row.get("graded"):
            lines.append(f"| {key} | {row['n']} | 0 | — | — | — |")
            continue
        lines.append(
            f"| {key} | {row['n']} | {row['graded']} | {row['semantic_pass']}（{row['semantic_rate']:.1%}） "
            f"| {row['fact_fraction_avg']} | {row['criterion_fraction_avg']} |")
    lines += ["", "## 逐条结果", "", "| id | 角色 | 语义 | 事实 | rubric | 耗时 |", "| --- | --- | --- | --- | --- | --- |"]
    for r in records:
        if r["grade"] is None:
            lines.append(f"| {r['id']} | {r['evaluation_role']} | {r['status']} | — | — | {r['latency_s']:.1f}s |")
            continue
        g = r["grade"]
        lines.append(
            f"| {r['id']} | {r['evaluation_role']} | {'✓' if g['semantic_pass'] else '✗'} "
            f"| {g['fact_fraction']} | {g['criterion_fraction']:.2f} | {r['latency_s']:.1f}s |")
    failures = [r for r in records if r["grade"] is not None and not r["grade"]["semantic_pass"]]
    if failures:
        lines += ["", "## 未通过条目", ""]
        for r in failures:
            lines += [f"### {r['id']}", "", f"- 问题：{r['question']}", "",
                      f"- 评分理由：{json.dumps(r['verdict'].get('criteria', []), ensure_ascii=False)}", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--model", default="default")
    ap.add_argument("--jwt-secret", default=None)
    ap.add_argument("--judge-url", default="http://192.168.1.113:8000/v1/chat/completions")
    ap.add_argument("--judge-model", default="Qwen3.5-27B")
    ap.add_argument("--answers", type=Path, default=None,
                    help="已有 answers.jsonl 所在目录：只重跑评分，不重新调用被测系统")
    ap.add_argument("--judge-key", default="YOUR_API_KEY")
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--role", default="all", choices=["all", "capability", "regression"])
    ap.add_argument("--domain", default="all", help="按 domain 过滤，如 trend")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    FRAMEWORK_PARENT = Path(__file__).resolve().parent / "framework"
    sys.path.insert(0, str(FRAMEWORK_PARENT))
    from intent.evaluate import VERSION as EVALUATOR_VERSION  # noqa: E402
    from intent.evaluate import grade, review_messages  # noqa: E402
    from intent.schema import answer_packet, digest  # noqa: E402

    cases = [json.loads(line) for line in args.data.open(encoding="utf-8") if line.strip()]
    if args.role != "all":
        cases = [c for c in cases if c["evaluation_role"] == args.role]
    if args.domain != "all":
        cases = [c for c in cases if c["domain"] == args.domain]
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        raise SystemExit("没有选中的任务")

    secret = args.jwt_secret or read_jwt_secret(SERVER_CONFIG)
    out_dir = (args.out or DEFAULT_OUT / time.strftime("%Y%m%d-%H%M%S"))
    out_dir.mkdir(parents=True, exist_ok=True)
    eval_run_id = f"{time.time_ns():x}"
    print(f"{len(cases)} 条任务 -> {out_dir}（运行 ID {eval_run_id}）", flush=True)

    reused: dict[str, dict] = {}
    if args.answers:
        for line in (args.answers / "answers.jsonl").open(encoding="utf-8"):
            row = json.loads(line)
            reused[row["id"]] = row

    records: list[dict] = []
    answers_f = (out_dir / "answers.jsonl").open("w", encoding="utf-8", buffering=1)
    scores_f = (out_dir / "score.jsonl").open("w", encoding="utf-8", buffering=1)
    try:
        with httpx.Client() as client:
            for index, case in enumerate(cases, 1):
                eval_user_id = None
                if args.answers:
                    row = reused[case["id"]]
                    answer, error = row["answer"], row.get("error")
                    latency = float(row["receipts"]["latency_s"])
                    status = row["status"]
                    eval_user_id = row["receipts"].get("eval_user_id")
                else:
                    packet = json.dumps(answer_packet(case), ensure_ascii=False)
                    answer, error, latency = "", None, 0.0
                    for attempt in range(3):
                        eval_user_id = f"eval-{eval_run_id}-{case['id']}-{attempt + 1}"
                        token = mint_token(eval_user_id, secret)
                        answer, error, latency = call_agent(
                            client, args.base, token, packet, args.model, args.timeout)
                        if not error and answer.strip():
                            break
                        print(f"  retry {attempt + 1}/3: {error}", flush=True)
                        time.sleep(5)
                    status = "ok" if not error and answer.strip() else "answer_failure"
                print(f"[{index}/{len(cases)}] {case['id']} {status} {latency:.1f}s", flush=True)

                record = {
                    "id": case["id"], "case_sha256": digest(case), "status": status,
                    "answer": answer, "error": error,
                    "receipts": {"latency_s": round(latency, 2), "agent_model": args.model,
                                 "eval_user_id": eval_user_id},
                    "evaluation_role": case["evaluation_role"], "domain": case["domain"],
                    "lang": case["lang"], "question": case["question"],
                    "latency_s": latency, "grade": None, "verdict": None,
                }
                if status == "ok":
                    try:
                        verdict, judge_error, graded = judge_case(
                            client, review_messages, grade, case, answer,
                            args.judge_url, args.judge_model, args.judge_key, args.timeout)
                    except Exception as exc:  # noqa: BLE001
                        verdict, judge_error, graded = None, f"{type(exc).__name__}: {exc}", None
                    record["verdict"] = verdict
                    if graded is None:
                        record["status"] = "evaluator_failure"
                        record["error"] = judge_error
                    else:
                        record["grade"] = graded
                records.append(record)
                answers_f.write(json.dumps({k: record[k] for k in
                                            ("id", "case_sha256", "status", "answer", "receipts", "error")},
                                           ensure_ascii=False) + "\n")
                scores_f.write(json.dumps({k: record[k] for k in
                                           ("id", "case_sha256", "status", "evaluation_role", "domain", "lang",
                                            "verdict", "grade", "error")}, ensure_ascii=False) + "\n")
    finally:
        answers_f.close()
        scores_f.close()


    summary = summarize(records)
    meta = {"when": time.strftime("%Y-%m-%dT%H:%M:%S"), "base": args.base, "model": args.model,
            "judge_model": args.judge_model, "evaluator": EVALUATOR_VERSION,
            "data": str(args.data), "cases": len(cases)}
    (out_dir / "report.json").write_text(
        json.dumps({"meta": meta, "summary": summary}, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(out_dir / "report.md", summary, records, meta)
    print(json.dumps(summary["overall"], ensure_ascii=False, indent=2))
    print(f"\n报告：{out_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
