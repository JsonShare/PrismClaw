#!/usr/bin/env python3
"""ClinicalTrials.gov API v2 检索工具（纯标准库，无第三方依赖）。

用法：
    python query_trials.py search --condition "proton therapy" --status RECRUITING --limit 5
    python query_trials.py search --condition "非小细胞肺癌" --phase PHASE2 --json
    python query_trials.py detail NCT04613596
    python query_trials.py detail NCT04613596 --json

API 文档：https://clinicaltrials.gov/data-api/api
速率限制：约 50 请求/分钟/IP
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://clinicaltrials.gov/api/v2"
TIMEOUT = 30
UA = "PrismClaw-medical-plugin/0.1"

VALID_STATUS = {
    "RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION",
    "ACTIVE_NOT_RECRUITING", "SUSPENDED", "TERMINATED", "COMPLETED", "WITHDRAWN",
    "UNKNOWN",
}
VALID_PHASE = {"EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4", "NA"}


def _get(path: str, params: dict[str, str]) -> dict:
    url = f"{BASE_URL}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"接口返回 HTTP {exc.code}：{body}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"网络不可达：{exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit("接口返回的不是合法 JSON") from exc


def _phase_filter(phases: list[str]) -> str:
    expr = " OR ".join(f"AREA[Phase]{p}" for p in phases)
    return f"({expr})" if len(phases) > 1 else expr


def search(args: argparse.Namespace) -> dict:
    params: dict[str, str] = {"pageSize": str(max(1, min(args.limit, 200)))}
    if args.condition:
        params["query.cond"] = args.condition
    if args.intervention:
        params["query.intr"] = args.intervention
    if args.sponsor:
        params["query.spons"] = args.sponsor
    if args.location:
        params["query.locn"] = args.location
    if args.term:
        params["query.term"] = args.term
    if args.status:
        params["filter.overallStatus"] = ",".join(args.status)
    advanced = []
    if args.phase:
        advanced.append(_phase_filter(args.phase))
    if args.with_results:
        advanced.append("HAS[RESULTS]")
    if advanced:
        params["filter.advanced"] = " AND ".join(advanced)
    params["sort"] = args.sort
    return _get("/studies", params)


def detail(nct_id: str) -> dict:
    return _get(f"/studies/{urllib.parse.quote(nct_id)}", {})


def _first(seq, default=None):
    return seq[0] if seq else default


def summarize(study: dict) -> dict:
    p = study.get("protocolSection", {})
    ident = p.get("identificationModule", {})
    status = p.get("statusModule", {})
    design = p.get("designModule", {})
    desc = p.get("descriptionModule", {})
    sponsor = p.get("sponsorCollaboratorsModule", {})
    cond = p.get("conditionsModule", {})
    arms = p.get("armsInterventionsModule", {})

    locations = p.get("contactsLocationsModule", {}).get("locations", []) or []
    by_country: dict[str, int] = {}
    cn_sites: list[str] = []
    for loc in locations:
        country = (loc.get("country") or "").strip()
        city = (loc.get("city") or "").strip()
        if not country:
            continue
        by_country[country] = by_country.get(country, 0) + 1
        if country in {"China", "Taiwan, China", "Hong Kong, China", "Macao, China"} and city:
            cn_sites.append(city)
    ordered = sorted(by_country.items(), key=lambda kv: (kv[0] not in {"China", "Taiwan, China", "Hong Kong, China", "Macao, China"}, -kv[1]))

    return {
        "nct_id": ident.get("nctId"),
        "title": ident.get("briefTitle") or ident.get("officialTitle"),
        "status": status.get("overallStatus"),
        "phase": design.get("phases", []),
        "enrollment": (design.get("enrollmentInfo") or {}).get("count"),
        "start_date": (status.get("startDateStruct") or {}).get("date"),
        "primary_completion": (status.get("primaryCompletionDateStruct") or {}).get("date"),
        "last_update": (status.get("lastUpdatePostDateStruct") or {}).get("date"),
        "conditions": cond.get("conditions", []),
        "interventions": [i.get("name") for i in arms.get("interventions", []) or [] if i.get("name")],
        "lead_sponsor": (sponsor.get("leadSponsor") or {}).get("name"),
        "countries": [f"{c}（{n} 个中心）" if n > 1 else c for c, n in ordered],
        "china_sites": list(dict.fromkeys(cn_sites))[:10],
        "brief_summary": (desc.get("briefSummary") or "").strip()[:800],
    }


def _print_zh(rows: list[dict], total: int | None) -> None:
    if total is not None:
        print(f"命中 {total} 项，展示 {len(rows)} 项\n")
    for i, r in enumerate(rows, 1):
        print(f"[{i}] {r['nct_id']}  {r['status']}  {'/'.join(r['phase']) or '未标注期别'}")
        print(f"    {r['title']}")
        meta = []
        if r["enrollment"]:
            meta.append(f"入组 {r['enrollment']} 例")
        if r["start_date"]:
            meta.append(f"启动 {r['start_date']}")
        if r["last_update"]:
            meta.append(f"更新 {r['last_update']}")
        if meta:
            print(f"    {' · '.join(meta)}")
        if r["conditions"]:
            print(f"    适应症：{'、'.join(r['conditions'][:4])}")
        if r["interventions"]:
            print(f"    干预：{'、'.join(r['interventions'][:4])}")
        if r["countries"]:
            shown = "、".join(r["countries"][:6])
            more = f" 等 {len(r['countries'])} 个国家/地区" if len(r["countries"]) > 6 else ""
            print(f"    国家/地区：{shown}{more}")
        if r["china_sites"]:
            print(f"    中国中心：{'、'.join(r['china_sites'])}")
        if r["lead_sponsor"]:
            print(f"    申办方：{r['lead_sponsor']}")
        if r["brief_summary"]:
            s = " ".join(r["brief_summary"].split())
            print(f"    摘要：{s[:300]}{'…' if len(s) > 300 else ''}")
        print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="query_trials.py",
        description="检索 ClinicalTrials.gov 临床试验（API v2，免密钥）",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("search", help="按条件检索临床试验")
    sp.add_argument("--condition", help="疾病/适应症，如 proton therapy、非小细胞肺癌")
    sp.add_argument("--intervention", help="干预/药物，如 pembrolizumab")
    sp.add_argument("--sponsor", help="申办方")
    sp.add_argument("--location", help="地点，如 China、Beijing")
    sp.add_argument("--term", help="自由词全文检索")
    sp.add_argument("--status", nargs="+", metavar="STATUS",
                    help="试验状态，可多值：" + ",".join(sorted(VALID_STATUS)))
    sp.add_argument("--phase", nargs="+", metavar="PHASE",
                    help="期别，可多值：" + ",".join(sorted(VALID_PHASE)))
    sp.add_argument("--with-results", action="store_true", help="仅返回已公布结果的试验")
    sp.add_argument("--limit", type=int, default=10, help="返回条数（默认 10，上限 200）")
    sp.add_argument("--sort", default="LastUpdatePostDate:desc", help="排序字段")
    sp.add_argument("--json", action="store_true", help="输出原始 JSON")
    sp.set_defaults(func=search)

    dp = sub.add_parser("detail", help="按 NCT 号取详情")
    dp.add_argument("nct_id", help="NCT 编号，如 NCT04613596")
    dp.add_argument("--json", action="store_true", help="输出原始 JSON")
    dp.set_defaults(func=lambda a: detail(a.nct_id))

    return parser


def _validate(args: argparse.Namespace) -> None:
    if args.cmd != "search":
        return
    for s in args.status or []:
        if s.upper() not in VALID_STATUS:
            raise SystemExit(f"无效状态 {s}；可选：{', '.join(sorted(VALID_STATUS))}")
    for p in args.phase or []:
        if p.upper() not in VALID_PHASE:
            raise SystemExit(f"无效期别 {p}；可选：{', '.join(sorted(VALID_PHASE))}")
    if not any([args.condition, args.intervention, args.sponsor, args.location, args.term]):
        raise SystemExit("至少提供一个检索条件：--condition / --intervention / --sponsor / --location / --term")


def main() -> int:
    args = build_parser().parse_args()
    if args.cmd == "search":
        args.status = [s.upper() for s in args.status] if args.status else None
        args.phase = [p.upper() for p in args.phase] if args.phase else None
    _validate(args)

    if args.cmd == "search":
        for value in (args.condition, args.intervention, args.term):
            if value and any("\u4e00" <= ch <= "\u9fff" for ch in value):
                print(
                    "提示：ClinicalTrials.gov 为英文库，中文检索词通常返回空结果，"
                    "请改用英文病名（如 非小细胞肺癌 → non-small cell lung cancer）。",
                    file=sys.stderr,
                )
                break

    data = args.func(args)

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    if "protocolSection" in data:
        _print_zh([summarize(data)], None)
        return 0

    studies = data.get("studies", []) or []
    rows = [summarize(s) for s in studies]
    if not rows:
        print("未命中试验。建议放宽条件：去掉 --phase 或 --status，或改用更通用的疾病名。")
        return 0
    _print_zh(rows, data.get("totalCount"))
    if token := data.get("nextPageToken"):
        print(f"还有更多结果，下一页 token：{token}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
