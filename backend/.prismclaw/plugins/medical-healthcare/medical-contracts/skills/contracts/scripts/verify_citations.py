#!/usr/bin/env python3
"""核验合同问答中的引用是否为原文逐字子串（纯标准库）。

用法：
    python scripts/verify_citations.py --quotes quotes.json --docs-dir ./contracts
    python scripts/verify_citations.py --quotes quotes.json --docs-dir ./contracts --json

quotes.json 两种写法：
    [{"doc": "a.pdf", "quote": "……"}, ...]
    {"quotes": [{"doc": "a.pdf", "quote": "……"}, ...]}

每条引用判定：
    exact        —— 归一化后是某份文档的连续子串（可写进答案的引用列）
    missing      —— 在可读文档里找不到（必须重读原文后改写，或标为「重建」）
    unverifiable —— 引用的文档是 PDF/Word 等二进制文件，未纳入文本核验（需先抽取文本）

doc 可省略；省略时在全部文档里检索，并报出命中的文件。
比较前统一做：NFKC 全半角、空白折叠、弯引号/破折号归一。只放宽比较，不改变原文。

退出码：0 = 无 missing；1 = 存在 missing。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

SUPPORTED_SUFFIXES = {
    ".txt", ".md", ".markdown", ".html", ".htm", ".csv",
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
}

# 这些后缀即便能按字节解码也不是可读文本，必须先抽取文本再核验
_BINARY_SUFFIXES = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt"}

_CHAR_MAP = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u2212": "-", "\u00a0": " ",
    "\u3000": " ", "\u00ad": "",
})


def normalize(text: str) -> str:
    """归一化后去掉全部空白，用于宽松子串比对。"""
    text = unicodedata.normalize("NFKC", str(text)).translate(_CHAR_MAP)
    return re.sub(r"\s+", "", text)


def read_text(path: Path) -> str | None:
    """尽力把文档读成文本；二进制/不可读返回 None。"""
    if path.suffix.lower() in _BINARY_SUFFIXES:
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def collect_docs(docs_dir: Path) -> tuple[dict[str, str], list[str]]:
    """返回 (可核验的文本文档, 因二进制而跳过的文档)。"""
    docs: dict[str, str] = {}
    skipped: list[str] = []
    for path in sorted(docs_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        rel = str(path.relative_to(docs_dir))
        text = read_text(path)
        if text is None:
            skipped.append(rel)
            continue
        docs[rel] = normalize(text)
    return docs, skipped


def resolve_doc(name: str, docs: dict[str, str]) -> list[str]:
    """按相对路径、文件名或文件名主干匹配候选文档。

    主干匹配让 "vendor-a.pdf" 能对上抽取后的 "vendor-a.txt"——
    PDF/Word 先落成文本再核验时，doc 字段可以继续写原始文件名。
    """
    if name in docs:
        return [name]
    target = Path(name)
    hits = [
        d for d in docs
        if d.endswith(name) or Path(d).name == target.name or Path(d).stem == target.stem
    ]
    return hits


def load_quotes(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} 不是合法 JSON：{exc}") from exc
    except OSError as exc:
        raise SystemExit(f"读不到 {path}：{exc}") from exc

    items = data.get("quotes") if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        raise SystemExit("quotes.json 需要非空的引用数组")
    out = []
    for i, item in enumerate(items):
        if not isinstance(item, dict) or not item.get("quote"):
            raise SystemExit(f"第 {i + 1} 条引用缺少 quote 字段")
        out.append(item)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="核验引用是否为原文子串")
    ap.add_argument("--quotes", required=True, help="引用清单 JSON")
    ap.add_argument("--docs-dir", required=True, help="合同文档目录")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = ap.parse_args()

    docs_dir = Path(args.docs_dir)
    if not docs_dir.is_dir():
        raise SystemExit(f"文档目录不存在：{docs_dir}")

    docs, skipped = collect_docs(docs_dir)
    if not docs:
        raise SystemExit(f"{docs_dir} 下没有可核验的文本文件（{len(skipped)} 份二进制文档需先抽取文本）")

    quotes = load_quotes(Path(args.quotes))
    results: list[dict] = []
    missing = 0
    unverifiable = 0

    for item in quotes:
        quote = normalize(item["quote"])
        doc_name = item.get("doc")
        note = None

        if doc_name:
            candidates = resolve_doc(str(doc_name), docs)
            if not candidates:
                status, matched = "unverifiable", []
                note = "文档不在可核验的文本范围内（PDF/Word 请先抽取文本）"
            else:
                matched = [d for d in candidates if quote and quote in docs[d]]
                status = "exact" if matched else "missing"
        else:
            matched = [d for d, text in docs.items() if quote and quote in text]
            status = "exact" if matched else "missing"

        if status == "missing":
            missing += 1
        elif status == "unverifiable":
            unverifiable += 1

        results.append({
            "label": item.get("label"),
            "doc": doc_name,
            "quote": item["quote"],
            "status": status,
            "matched_in": matched,
            "note": note,
        })

    summary = {
        "total": len(results),
        "exact": len(results) - missing - unverifiable,
        "missing": missing,
        "unverifiable": unverifiable,
        "docs_checked": len(docs),
        "docs_skipped_binary": len(skipped),
    }

    # 一条都没核验成功（全是不在文本范围内的二进制文档）不算通过——
    # 否则「没核验」会被当成「核验通过」，这正是要拦的静默失效。
    nothing_verified = summary["exact"] == 0 and missing == 0

    if args.json:
        print(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2))
        return 1 if (missing or nothing_verified) else 0

    for r in results:
        label = f"[{r['label']}] " if r.get("label") else ""
        if r["status"] == "exact":
            where = "/".join(r["matched_in"]) if r["matched_in"] else (r["doc"] or "(未指定)")
            print(f"OK    {label}{where}")
        elif r["status"] == "missing":
            print(f"MISS  {label}{r['doc'] or '(未指定文档)'}：{r['quote'][:60]}…")
        else:
            print(f"UNVR  {label}{r['doc']}：{r['note']}")

    print()
    print(
        f"引用核验：{summary['exact']}/{summary['total']} 命中原文；"
        f"{missing} 条未命中；{unverifiable} 条无法核验"
    )
    if skipped:
        print(f"另有 {len(skipped)} 份二进制文档未纳入核验（先抽取文本再核验）。")
    if missing:
        print("未命中的引用不得写进答案表格：重读原文后逐字摘录，或明确标为「重建」。")
    if nothing_verified:
        print("没有任何引用通过核验（全部无法核验），本次不算通过：先把文档抽取成文本再核验。")

    return 1 if (missing or nothing_verified) else 0


if __name__ == "__main__":
    sys.exit(main())
