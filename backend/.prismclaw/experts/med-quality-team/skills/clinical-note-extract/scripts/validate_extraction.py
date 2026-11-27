#!/usr/bin/env python3
"""校验病历抽取结果的出处、断言与字段检查（纯标准库）。

用法：
    python validate_extraction.py --note note.txt --result result.json
    python validate_extraction.py --note note.txt --result result.json --schema schema.json
    python validate_extraction.py --note note.txt --result result.json --schema schema.json --json
    python validate_extraction.py --note note.txt --result result.json --quiet

校验项：
    1. 每个非空 span 能否在原文中检索到（忽略空白与全半角差异）
    2. 每个 null 是否带合法 null_reason
    3. 断言三轴取值是否合法
    4. schema 字段的 check（range / date / pattern / enum / terminology）
    5. schema 字段的 type 与单位是否吻合

退出码：0 = 无 FAIL（警告不影响退出码）；1 = 存在 FAIL。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import unicodedata
from pathlib import Path

PRESENCE = {"present", "absent", "possible"}
TEMPORALITY = {"current", "historical", "hypothetical"}
EXPERIENCER = {"patient", "family_member", "other"}
NULL_REASONS = {"not_mentioned", "illegible", "redacted", "ambiguous", "out_of_scope"}
CHECK_KINDS = {"range", "date", "pattern", "enum", "terminology"}

_DATE_SEP = str.maketrans({"年": "-", "月": "-", "日": "", "/": "-", ".": "-"})


def normalize(text: str) -> str:
    """去空白 + 全角转半角，用于 span 检索对齐。"""
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", "", text)


def normalize_unit(unit: str) -> str:
    unit = unicodedata.normalize("NFKC", str(unit)).strip().lower()
    return re.sub(r"\s+", "", unit)


def parse_date(value: object) -> bool:
    """按原文给出的精度判断是否为合法日期，不接受无依据的补全。"""
    if not isinstance(value, str):
        return False
    text = unicodedata.normalize("NFKC", value).strip().translate(_DATE_SEP).strip("-")
    text = re.sub(r"\s+", " ", text)

    m = re.fullmatch(r"(\d{4})", text)
    if m:
        return 1900 <= int(m.group(1)) <= 2200

    m = re.fullmatch(r"(\d{4})-(\d{1,2})", text)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        return 1900 <= year <= 2200 and 1 <= month <= 12

    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?", text)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            dt.date(year, month, day)
        except ValueError:
            return False
        if m.group(4) is None:
            return True
        return 0 <= int(m.group(4)) <= 23 and 0 <= int(m.group(5)) <= 59

    return False


def check_field(
    name: str, fld: dict, note_norm: str, spec: dict | None
) -> tuple[list[str], list[str], dict]:
    """返回 (errors, warnings, attachments)。"""
    errs: list[str] = []
    warns: list[str] = []
    attach: dict = {}

    value = fld.get("value")
    span = fld.get("span")

    presence = fld.get("presence", "present")
    temporality = fld.get("temporality", "current")
    experiencer = fld.get("experiencer", "patient")
    if presence not in PRESENCE:
        errs.append(f"presence 非法：{presence}")
    if temporality not in TEMPORALITY:
        errs.append(f"temporality 非法：{temporality}")
    if experiencer not in EXPERIENCER:
        errs.append(f"experiencer 非法：{experiencer}")

    if value is None:
        reason = fld.get("null_reason")
        if not reason:
            errs.append("空值缺 null_reason")
        elif reason not in NULL_REASONS:
            errs.append(f"null_reason 非法：{reason}")
        if span:
            errs.append("空值不应带 span")
        attach["span_verified"] = None
        return errs, warns, attach

    if not span:
        errs.append("有值但缺 span（无法核对出处）")
        attach["span_verified"] = False
    elif normalize(str(span)) not in note_norm:
        errs.append(f"span 在原文中检索不到：「{span}」")
        attach["span_verified"] = False
    else:
        attach["span_verified"] = True

    if not spec:
        return errs, warns, attach

    expected_type = spec.get("type")
    if expected_type == "number" and not isinstance(value, (int, float)):
        errs.append(f"类型应为 number，实际 {type(value).__name__}")
    if expected_type == "string" and not isinstance(value, str):
        errs.append(f"类型应为 string，实际 {type(value).__name__}")

    check = spec.get("check") or {}
    kind = check.get("kind")
    if not kind:
        return errs, warns, attach
    if kind not in CHECK_KINDS:
        warns.append(f"未知 check.kind：{kind}（本字段仅做 span 核验）")
        return errs, warns, attach

    if kind == "range":
        if not isinstance(value, (int, float)):
            warns.append(f"range 校验需要数值，实际 {type(value).__name__}")
        else:
            lo, hi = check.get("min"), check.get("max")
            flag = "ok"
            if lo is not None and value < lo:
                errs.append(f"数值 {value} 低于下限 {lo}")
                flag = "low"
            if hi is not None and value > hi:
                errs.append(f"数值 {value} 高于上限 {hi}")
                flag = "high"
            attach["range_flag"] = flag
        expected_unit = check.get("unit")
        actual_unit = fld.get("unit")
        if expected_unit and actual_unit is not None:
            if normalize_unit(expected_unit) != normalize_unit(actual_unit):
                warns.append(
                    f"单位不符：原文 {actual_unit}，schema 期望 {expected_unit}（标记 unit_suspect）"
                )
                attach["unit_suspect"] = True
                if "range_flag" in attach:
                    attach["range_flag"] = "unit_mismatch"

    elif kind == "date":
        ok = parse_date(value)
        attach["date_ok"] = ok
        if not ok:
            errs.append(f"日期无法按原文精度解析：{value!r}")

    elif kind == "pattern":
        if not isinstance(value, str):
            warns.append("pattern 校验需要字符串，已跳过")
        else:
            ok = re.search(check.get("pattern", ""), value) is not None
            attach["check_ok"] = ok
            if not ok:
                errs.append(f"值不匹配模式 {check.get('pattern')}")

    elif kind == "enum":
        allowed = check.get("values") or []
        if allowed:
            ok = value in allowed
            attach["check_ok"] = ok
            if not ok:
                errs.append(f"值 {value} 不在枚举 {allowed}")

    elif kind == "terminology":
        via = check.get("via") or "未命名连接器"
        attach["code_status"] = "unvalidated"
        warns.append(f"术语校验需连接器 {via}，本环境未提供，标记 unvalidated")

    return errs, warns, attach


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} 不是合法 JSON：{exc}") from exc
    except OSError as exc:
        raise SystemExit(f"读不到 {path}：{exc}") from exc


def main() -> int:
    ap = argparse.ArgumentParser(description="校验病历抽取结果")
    ap.add_argument("--note", required=True, help="原始病历文本文件")
    ap.add_argument("--result", required=True, help="抽取结果 JSON")
    ap.add_argument("--schema", help="字段定义 JSON（可选，用于 check/type/单位校验）")
    ap.add_argument("--quiet", action="store_true", help="只输出汇总与失败项")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON（含逐字段附件与完整性汇总）")
    args = ap.parse_args()

    try:
        note_norm = normalize(Path(args.note).read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"读不到病历：{exc}") from exc

    result = load_json(Path(args.result))
    fields = result.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise SystemExit("result.json 缺少非空的 fields 对象")

    schema = load_json(Path(args.schema)) if args.schema else {}
    if not schema:
        print(
            "注意：未提供 --schema，只核验 span/null_reason/断言三轴，"
            "各字段的 check 与 type 未执行。",
            file=sys.stderr,
        )

    requested = len(set(schema) | set(fields)) if schema else len(fields)
    populated = 0
    null_by_reason: dict[str, int] = {}
    span_total = 0
    span_ok = 0
    check_stats: dict[str, dict[str, int]] = {}
    field_report: dict[str, dict] = {}
    failures: list[tuple[str, list[str]]] = []

    for name, fld in fields.items():
        if not isinstance(fld, dict):
            failures.append((name, ["字段值不是对象"]))
            continue
        errs, warns, attach = check_field(name, fld, note_norm, schema.get(name))

        if fld.get("value") is None:
            reason = fld.get("null_reason") or "缺失"
            null_by_reason[reason] = null_by_reason.get(reason, 0) + 1
        else:
            populated += 1
            span_total += 1
            if attach.get("span_verified"):
                span_ok += 1

        kind = (schema.get(name) or {}).get("check", {}).get("kind")
        if kind:
            stat = check_stats.setdefault(kind, {"ok": 0, "flagged": 0})
            flagged = bool(errs) or bool(warns)
            stat["flagged" if flagged else "ok"] += 1

        field_report[name] = {
            "status": "fail" if errs else ("warn" if warns else "ok"),
            "errors": errs,
            "warnings": warns,
            "checks": attach,
            "value": fld.get("value"),
        }
        if errs:
            failures.append((name, errs))
        elif not args.quiet and not args.json:
            state = "空值" if fld.get("value") is None else "有值"
            print(f"OK   {name:24s} {state}" + ("  ⚠" if warns else ""))

    summary = {
        "requested": requested,
        "populated": populated,
        "null": sum(null_by_reason.values()),
        "not_returned": max(0, requested - len(fields)),
        "null_by_reason": null_by_reason,
        "span": {"checked": span_total, "verified": span_ok},
        "checks": check_stats,
        "failed_fields": [n for n, _ in failures],
    }

    if args.json:
        print(
            json.dumps(
                {"note": args.note, "summary": summary, "fields": field_report},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if failures else 0

    print()
    print(f"通过 {len(fields) - len(failures)} / 共 {len(fields)}；失败 {len(failures)}")
    if null_by_reason:
        detail = "、".join(f"{k} {v}" for k, v in sorted(null_by_reason.items()))
        print(f"完整性：请求 {requested} · 有值 {populated} · 空 {summary['null']}（{detail}）")
    else:
        print(f"完整性：请求 {requested} · 有值 {populated} · 空 0")
    if summary["not_returned"]:
        print(f"未返回字段：{summary['not_returned']}（schema 里有、结果里没有，按缺失处理）")
    print(f"出处核验：{span_ok}/{span_total} 条 span 命中原文")
    for kind, stat in sorted(check_stats.items()):
        print(f"校验 {kind}：通过 {stat['ok']} · 标记 {stat['flagged']}")
    for name, errs in failures:
        print(f"FAIL {name}")
        for e in errs:
            print(f"     - {e}")
    for name, rep in field_report.items():
        for w in rep["warnings"]:
            print(f"WARN {name}: {w}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
