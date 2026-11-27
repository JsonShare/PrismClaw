#!/usr/bin/env python3
"""china-law-mcp benchmark: retrieval efficiency (latency) + accuracy.

Drives the server exactly as production does:
  conda run --no-capture-output -n pyclaw python -m china_law_mcp
over newline-delimited JSON-RPC on stdio, matching replies by id.

Outputs:
  bench/results/bench_results.json   (raw per-call evidence + aggregates)
  <workspace>/china-law-mcp-test-report.md
"""
from __future__ import annotations

import json
import os
import queue
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]  # mcp_servers/china-law-mcp -> PrismClaw
RESULTS_DIR = HERE / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
QUERY_FILE = HERE / "queries.json"
REPORT_PATH = PROJECT / "china-law-mcp-test-report.md"

PROD_CMD = ["conda", "run", "--no-capture-output", "-n", "pyclaw", "python", "-m", "china_law_mcp"]
PER_CALL_TIMEOUT = 150.0  # s; server internal GIT_TIMEOUT is 120

ENV = dict(os.environ)
ENV.update({
    "CLM_GLOBAL_LAW_ROOT": os.path.expanduser("~/.prismclaw/database/global-law"),
    "CLM_ACTS_ROOT": os.path.expanduser("~/.prismclaw/database/china-law"),
    "CLM_JURISDICTIONS": "china",
    "PYTHONDONTWRITEBYTECODE": "1",
})


class Client:
    def __init__(self):
        self.p = subprocess.Popen(
            PROD_CMD, cwd=str(PROJECT), env=ENV,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self._q: "queue.Queue[dict]" = queue.Queue()
        self._id = 0
        self.stderr_chunks: list[str] = []
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        for line in self.p.stdout:
            try:
                self._q.put(json.loads(line))
            except Exception:
                pass

    def _drain_stderr(self) -> None:
        for line in self.p.stderr:
            self.stderr_chunks.append(line.decode("utf-8", "replace"))

    def call(self, method: str, params: dict | None = None) -> tuple[float, dict | None]:
        self._id += 1
        mid = self._id
        req = {"jsonrpc": "2.0", "id": mid, "method": method}
        if params is not None:
            req["params"] = params
        t0 = time.monotonic()
        self.p.stdin.write((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
        self.p.stdin.flush()
        try:
            while True:
                msg = self._q.get(timeout=PER_CALL_TIMEOUT)
                if msg.get("id") == mid:
                    return time.monotonic() - t0, msg
        except queue.Empty:
            return time.monotonic() - t0, None  # timeout

    def close(self) -> None:
        try:
            self.p.stdin.close()
        except Exception:
            pass
        try:
            self.p.wait(timeout=10)
        except Exception:
            self.p.terminate()


# ---------- result extraction ----------
def get_text(r: dict) -> str:
    try:
        return r["result"]["content"][0].get("text", "") or ""
    except Exception:
        return ""

def get_sc(r: dict) -> dict:
    try:
        return r["result"].get("structuredContent") or {}
    except Exception:
        return {}

def is_err(r: dict | None) -> bool:
    if not r:
        return True
    return bool(r.get("result", {}).get("isError")) or "error" in r

def result_rows(sc: dict) -> list[dict]:
    for k in ("results", "provisions", "folders", "changes"):
        v = sc.get(k)
        if isinstance(v, list):
            return v
    return []

def row_text(item) -> str:
    if isinstance(item, dict):
        return json.dumps(item, ensure_ascii=False)
    return str(item)


# ---------- accuracy matchers ----------
def evaluate(q: dict, r: dict | None, elapsed: float) -> dict:
    exp = q.get("expect", {})
    text = get_text(r) if r else ""
    sc = get_sc(r) if r else {}
    rows = result_rows(sc)
    checks: list[dict] = []

    def add(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail[:200]})

    if "hit_top1_path_contains" in exp:
        x = exp["hit_top1_path_contains"]
        top = rows[0] if rows else {}
        path = top.get("path", "") if isinstance(top, dict) else ""
        add(f"top1 path contains {x!r}", x in path, f"top path={path!r}")
    if "top_kind" in exp:
        top = rows[0] if rows else {}
        kind = top.get("kind", "") if isinstance(top, dict) else ""
        add(f"top kind == {exp['top_kind']!r}", kind == exp["top_kind"], f"kind={kind!r}")
    if "top5_path_or_text_contains" in exp:
        x = exp["top5_path_or_text_contains"]
        blob = " ".join(row_text(it) for it in rows[:5])
        found = x in blob or x in text
        add(f"top-5 contains {x!r}", found, f"top5 rows={len(rows[:5])}")
    if exp.get("text_contains_query"):
        qq = q["args"].get("query", "")
        add(f"text contains query {qq!r}", qq in text, f"text_len={len(text)}")
    if exp.get("zero_results"):
        summary = sc.get("summary", "") if isinstance(sc, dict) else ""
        zero = (len(rows) == 0) or ("0 provision" in summary) or ("no provision" in text.lower()) or ("nothing" in summary.lower())
        add("zero results", zero, f"rows={len(rows)} summary={summary[:80]!r}")
    if exp.get("no_error"):
        add("no error", not is_err(r), f"isError={is_err(r)}")
    for sub in exp.get("text_contains", []):
        add(f"text contains {sub!r}", sub in text, f"text_len={len(text)}")
    if "min_changes" in exp:
        # count entries: list length, else count "In-force" lines in text
        n = len(rows) or text.count("In-force:")
        add(f">= {exp['min_changes']} changes", n >= exp["min_changes"], f"counted={n}")
    for d in exp.get("mentions_in_force", []):
        add(f"mentions in-force {d}", d in text, "")
    if exp.get("diff_nonempty"):
        low = text.lower()
        changed = not any(k in low for k in ("no changes", "unchanged", "identical", "did not change")) \
                  and ("+" in text or "-" in text or "diff" in low or "changed" in low)
        add("diff non-empty", changed, f"text_len={len(text)}")
    if "resolves_at_least" in exp:
        n = len(rows) or text.count("resolved")
        add(f">= {exp['resolves_at_least']} resolved", n >= exp["resolves_at_least"], f"counted={n}")
    if "resolved_path_contains" in exp:
        x = exp["resolved_path_contains"]
        blob = " ".join(row_text(it) for it in rows)
        add(f"a resolved path contains {x!r}", x in blob or x in text, "")

    passed = all(c["ok"] for c in checks) if checks else (not is_err(r))
    return {
        "passed": passed,
        "checks": checks,
        "isError": is_err(r),
        "elapsed_s": round(elapsed, 3),
        "text_len": len(text),
        "text_head": text[:400],
        "summary": (sc.get("summary", "") if isinstance(sc, dict) else "")[:240],
        "n_rows": len(rows),
    }


def percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def summarize(vals: list[float]) -> dict:
    s = sorted(vals)
    return {
        "n": len(s),
        "min_s": round(s[0], 3) if s else None,
        "mean_s": round(statistics.fmean(s), 3) if s else None,
        "p50_s": round(percentile(s, 0.5), 3),
        "p90_s": round(percentile(s, 0.9), 3),
        "p95_s": round(percentile(s, 0.95), 3),
        "max_s": round(s[-1], 3) if s else None,
    }


def main() -> None:
    spec = json.loads(QUERY_FILE.read_text(encoding="utf-8"))
    cold_iters = int(spec.get("cold_iterations", 3))
    out: dict = {"meta": {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "server": "china-law-mcp via python -m china_law_mcp",
                           "jurisdiction": spec.get("jurisdiction"), "per_call_timeout_s": PER_CALL_TIMEOUT},
                 "cold": [], "latency": {}, "accuracy": []}

    # ---- cold phase: fresh process each iter ----
    print(f"== COLD phase: {cold_iters} fresh process(es) ==", flush=True)
    cold_connect, cold_first_tool = [], []
    for i in range(cold_iters):
        c = Client()
        dt_init, r_init = c.call("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                                  "clientInfo": {"name": "bench", "version": "1"}})
        dt_list, r_list = c.call("tools/list")
        dt_first, r_first = c.call("tools/call", {"name": "list_jurisdictions", "arguments": {}})
        ok = r_init is not None and not is_err(r_first)
        cold_connect.append(dt_init + dt_list)
        cold_first_tool.append(dt_first)
        print(f"  cold[{i}] connect={dt_init+dt_list:.2f}s list_jurisdictions={dt_first:.2f}s err={is_err(r_first)}", flush=True)
        c.close()
    out["cold"] = {"connect_s": summarize(cold_connect), "first_list_jurisdictions_s": summarize(cold_first_tool)}

    # ---- warm phase: one persistent process ----
    print("== WARM phase: one persistent process ==", flush=True)
    c = Client()
    c.call("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                          "clientInfo": {"name": "bench", "version": "1"}})
    c.call("tools/list")
    # warm the index
    c.call("tools/call", {"name": "list_jurisdictions", "arguments": {}})

    # latency
    for q in spec["latency"]:
        times = []
        last = None
        for k in range(int(q.get("repeats", 1))):
            dt, r = c.call("tools/call", {"name": q["tool"], "arguments": q["args"]})
            times.append(dt)
            last = r
            print(f"  [lat] {q['id']:24s} run{k} {dt:7.2f}s err={is_err(r)}", flush=True)
        sc = get_sc(last) if last else {}
        out["latency"][q["id"]] = {
            "tool": q["tool"], "args": q["args"],
            "timings": summarize(times),
            "raw_s": [round(t, 3) for t in times],
            "isError": is_err(last),
            "summary": (sc.get("summary", "") if isinstance(sc, dict) else "")[:200],
        }

    # accuracy
    n_pass = 0
    for q in spec["accuracy"]:
        dt, r = c.call("tools/call", {"name": q["tool"], "arguments": q["args"]})
        ev = evaluate(q, r, dt)
        if ev["passed"]:
            n_pass += 1
        print(f"  [acc] {'PASS' if ev['passed'] else 'FAIL'} {q['id']:28s} {dt:7.2f}s  {[(c['name'],c['ok']) for c in ev['checks']]}", flush=True)
        out["accuracy"].append({"id": q["id"], "tool": q["tool"], "args": q["args"], **ev})

    c.close()

    out["accuracy_summary"] = {"total": len(spec["accuracy"]), "passed": n_pass,
                               "failed": len(spec["accuracy"]) - n_pass,
                               "pass_rate": round(n_pass / max(1, len(spec["accuracy"])), 3)}

    (RESULTS_DIR / "bench_results.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(out)
    print(f"\nDONE. results -> {RESULTS_DIR / 'bench_results.json'}", flush=True)
    print(f"report  -> {REPORT_PATH}", flush=True)


def write_report(out: dict) -> None:
    cold = out["cold"]
    lat = out["latency"]
    acc = out["accuracy"]
    acc_sum = out["accuracy_summary"]

    def s(d, key):
        return d.get(key, {}).get("p50_s") if isinstance(d.get(key), dict) else d.get(key)

    lines = []
    lines.append("# china-law-mcp 检索效率与准确度测试报告\n")
    lines.append(f"- 生成时间: {out['meta']['ts']}")
    lines.append(f"- 被测对象: `python -m china_law_mcp`（conda env `pyclaw`，stdio JSON-RPC，vendored global-law server）")
    lines.append(f"- 语料: `~/.prismclaw/database/global-law` + `china-law`（独立克隆，symlink 到 `china/national/acts`）")
    lines.append(f"- 管辖范围: `{out['meta'].get('jurisdiction')}`（GLOBAL_LAW_JURISDICTIONS=china）")
    lines.append(f"- 单调用超时阈值: {out['meta']['per_call_timeout_s']}s（服务端内部 GIT_TIMEOUT=120s；backend mcp.json call_timeout=100s）")
    lines.append("")

    lines.append("## 1. 结论摘要\n")
    cold_p50 = cold["connect_s"]["p50_s"]
    cold_first = cold["first_list_jurisdictions_s"]["p50_s"]
    lines.append(f"- 进程冷启动 connect（spawn + initialize + tools/list）: **p50 {cold_p50}s**")
    lines.append(f"- 冷进程首次 `list_jurisdictions`（磁盘缓存已预热、进程内冷）: **p50 {cold_first}s**")
    lines.append(f"- 准确度: **{acc_sum['passed']}/{acc_sum['total']} 通过（{acc_sum['pass_rate']*100:.0f}%）**")
    slow = [(k, v["timings"]["max_s"]) for k, v in lat.items() if v["timings"]["max_s"] and v["timings"]["max_s"] > 100]
    if slow:
        lines.append(f"- ⚠️ 超过 backend 100s 调用超时的工具: " + ", ".join(f"`{k}`(max {m}s)" for k, m in slow))
    lines.append("")

    lines.append("## 2. 检索效率（延迟）\n")
    lines.append("单位：秒。每次测量为一次完整 tools/call 往返（含服务端 git 操作）。warm = 同一长驻进程、索引已加载。\n")
    lines.append("| 用例 | 工具 | n | min | p50 | p90 | p95 | max | 错误 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for k, v in lat.items():
        t = v["timings"]
        lines.append(f"| `{k}` | {v['tool']} | {t['n']} | {t['min_s']} | {t['p50_s']} | {t['p90_s']} | {t['p95_s']} | {t['max_s']} | {'是' if v['isError'] else '否'} |")
    lines.append("")
    lines.append("### 2.1 冷启动 connect\n")
    cs, cf = cold["connect_s"], cold["first_list_jurisdictions_s"]
    lines.append(f"- connect（spawn→initialize→tools/list）: n={cs['n']} min {cs['min_s']}s / p50 {cs['p50_s']}s / max {cs['max_s']}s")
    lines.append(f"- 首次 list_jurisdictions（进程冷）: n={cf['n']} min {cf['min_s']}s / p50 {cf['p50_s']}s / max {cf['max_s']}s")
    lines.append("- 注：索引磁盘缓存（global-law-cache）已存在，故此处为「进程冷、磁盘暖」；纯冷（无磁盘缓存）的索引构建在诊断阶段实测约 **26.6s**（list_jurisdictions）。")
    lines.append("")

    lines.append("## 3. 检索准确度\n")
    lines.append(f"共 {acc_sum['total']} 条断言用例，{acc_sum['passed']} 通过，{acc_sum['failed']} 失败。\n")
    lines.append("| 用例 | 工具 | 耗时(s) | 结果 | 失败检查 |")
    lines.append("|---|---|---|---|---|")
    for a in acc:
        fails = [c["name"] for c in a["checks"] if not c["ok"]]
        lines.append(f"| `{a['id']}` | {a['tool']} | {a['elapsed_s']} | {'PASS' if a['passed'] else '**FAIL**'} | {'; '.join(fails) if fails else '-'} |")
    lines.append("")

    lines.append("### 3.1 失败/异常用例明细\n")
    failed = [a for a in acc if not a["passed"]]
    if not failed:
        lines.append("无。所有准确度断言通过。\n")
    else:
        for a in failed:
            lines.append(f"- **`{a['id']}`** ({a['tool']}): isError={a['isError']}, text_len={a['text_len']}")
            for c in a["checks"]:
                if not c["ok"]:
                    lines.append(f"  - ✗ {c['name']} — {c['detail']}")
            lines.append(f"  - 返回摘要: {a['summary']!r}")
            lines.append(f"  - 返回文首: `{a['text_head'][:160]}`")
        lines.append("")

    lines.append("## 4. 发现与问题\n")
    lines.append("### 4.1 已修复的阻断 bug：`law._unquote` UnicodeEncodeError")
    lines.append("- 现象：`list_jurisdictions`（及所有走 `_index`/timeline 的工具）首次调用崩溃：`'latin-1' codec can't encode characters`。")
    lines.append("- 根因：`mcp_server.git_run` 强制 `core.quotepath=off`，git 对含特殊字节（实测为文件名中的 `\\\\177` 即 0x7f）的路径仍加引号，引号内是「八进制转义 + 裸 UTF-8 中文」混合；上游 `_unquote` 对整段做 `.encode('latin-1')` 崩溃。china-law 存在此类文件名，故必现。")
    lines.append("- 上游状态：main/master 仍为原实现，未修复（本环境 + china-law 触发）。")
    lines.append("- 修复：vendored `law.py` 的 `_unquote` 增加 try/except 失败分支，手工解码八进制转义并透传裸 UTF-8（索引键只需 diff-tree 与 ls-tree 两处一致即可）。修复后 `list_jurisdictions` 等恢复正常。")
    lines.append("")
    lines.append("### 4.2 性能风险：`list_provisions` / 全文读取单 act 偏慢")
    lines.append("- 实测 `list_provisions`（一个 act 99 个条文）冷态约 100s+，逼近/超过 backend `call_timeout=100s`，存在超时风险。")
    lines.append("- `read_law` 读取整部法律（folder，99 文件）同样较慢（见 2 节 `read_act_folder`）。")
    lines.append("- 建议：产品侧对「列条文 / 读整部法律」设更长超时或提示用户收窄范围；索引构建后这些操作会加速，但首跳仍慢。")
    lines.append("")
    lines.append("### 4.3 准确度边界：中文数字条文号不被 `find_provision` 直解")
    lines.append("- `find_provision \"劳动合同法 第六十六条\"` 返回到整部法律（kind=act, whole_act=true），提示「no file of it is 66」。")
    lines.append("- 原因：china-law 条文文件名为阿拉伯数字补零（`第066条.md`），上游的编号解析不把「第六十六」映射到 `066`。")
    lines.append("- 可行路径：先 `find_provision` 定位 act → `list_provisions` 取条文路径 → `read_law`/`provision_history`。这是使用模式问题，非数据错误（`read_law` 用数字路径直读正确）。")
    lines.append("")
    lines.append("### 4.4 语料校验")
    lines.append("- `china-law` 完整克隆：173,444 commits，20 个顶层目录齐全，`.git` 保留。")
    lines.append("- `global-law` 记录的 gitlink pin（`018625e29e1`）与 `china-law` HEAD 一致 → 按日期读取版本对齐。")
    lines.append("- 启动时 `ensure_acts` 自动把 `global-law/china/national/acts` 软链到 `china-law`（已验证）。")
    lines.append("")

    lines.append("## 5. 复现")
    lines.append("```bash")
    lines.append(f"cd {PROJECT}")
    lines.append("conda run --no-capture-output -n pyclaw python mcp_servers/china-law-mcp/bench/runner.py")
    lines.append("```")
    lines.append(f"- 原始数据: `mcp_servers/china-law-mcp/bench/results/bench_results.json`")
    lines.append("")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
