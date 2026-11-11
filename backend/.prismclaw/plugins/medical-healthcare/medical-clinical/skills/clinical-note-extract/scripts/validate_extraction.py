#!/usr/bin/env python3
"""校验病历抽取结果的出处与断言合法性（纯标准库）。

用法：
    python validate_extraction.py --note note.txt --result result.json
    python validate_extraction.py --note note.txt --result result.json --schema schema.json
    python validate_extraction.py --note note.txt --result result.json --quiet

退出码：0 = 全部通过；1 = 存在 FAIL。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

PRESENCE = {"present", "absent", "possible"}
TEMPORALITY = {"current", "historical", "hypothetical"}
EXPERIENCER = {"patient", "family_member", "other"}
NULL_REASONS = {"not_mentioned", "illegible", "redacted", "ambiguous"}


def normalize(text: str) -> str:
    """去空白 + 全角转半角，用于 span 检索对齐。"""
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", "", text)


def check_field(name: str, fld: dict, note_norm: str, spec: dict | None) -> list[str]:
    errs: list[str] = []
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
        return errs

    if not span:
        errs.append("有值但缺 span（无法核对出处）")
    elif normalize(str(span)) not in note_norm:
        errs.append(f"span 在原文中检索不到：「{span}」")

    if spec:
        check = spec.get("check") or {}
        kind = check.get("kind")
        if kind == "range" and isinstance(value, (int, float)):
            lo, hi = check.get("min"), check.get("max")
            if lo is not None and value < lo:
                errs.append(f"数值 {value} 低于下限 {lo}")
            if hi is not None and value > hi:
                errs.append(f"数值 {value} 高于上限 {hi}")
        elif kind == "pattern" and isinstance(value, str):
            if not re.search(check.get("pattern", ""), value):
                errs.append(f"值不匹配模式 {check.get('pattern')}")
        elif kind == "enum":
            allowed = check.get("values") or []
            if allowed and value not in allowed:
                errs.append(f"值 {value} 不在枚举 {allowed}")
        expected_type = spec.get("type")
        if expected_type == "number" and not isinstance(value, (int, float)):
            errs.append(f"类型应为 number，实际 {type(value).__name__}")
        if expected_type == "string" and not isinstance(value, str):
            errs.append(f"类型应为 string，实际 {type(value).__name__}")

    return errs


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
    ap.add_argument("--schema", help="字段定义 JSON（可选，用于范围/模式校验）")
    ap.add_argument("--quiet", action="store_true", help="只输出汇总与失败项")
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

    ok_count = 0
    failures: list[tuple[str, list[str]]] = []
    for name, fld in fields.items():
        if not isinstance(fld, dict):
            failures.append((name, ["字段值不是对象"]))
            continue
        errs = check_field(name, fld, note_norm, schema.get(name))
        if errs:
            failures.append((name, errs))
        else:
            ok_count += 1
            if not args.quiet:
                status = "空值" if fld.get("value") is None else "有值"
                print(f"OK   {name:24s} {status}")

    print()
    print(f"通过 {ok_count} / 共 {len(fields)}；失败 {len(failures)}")
    for name, errs in failures:
        print(f"FAIL {name}")
        for e in errs:
            print(f"     - {e}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
