#!/usr/bin/env python3
"""Read one Markdown file and check supported same-document references.

Supports ATX headings, inline #anchor links, explicit 见/参见/详见 numeric
sections, and quoted numbered titles. Ignores fenced/inline code. Uses common
GitHub-style heading anchors; custom renderer IDs need manual review. Does not
follow links, read other files, modify files, or verify meaning or evidence.
"""

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import unquote


LIMITS = "仅核对支持格式的文内目标；未核对目标语义、外部证据、审批状态、日期或篇幅。"
LEAD = r"(?:参见|详见|见)\s*(?:(?:本文件|本稿)\s*)?"
NUMBER = re.compile(r"^(\d+(?:\.\d+)*)(?=[.、．\s]|$)")
PREFIX = re.compile(r"^(?:[一二三四五六七八九十百零〇]+、|\d+(?:\.\d+)*(?:[.、．]|\s))\s*")
NUM_REF = re.compile(LEAD + r"§?\s*(\d+(?:\.\d+)*)(?![\d.A-Za-z_])")
TITLE_REF = re.compile(LEAD + r'(?:“([^”\n]+)”|"([^"\n]+)"|「([^」\n]+)」)')
ANCHOR_REF = re.compile(r"(?<!!)\[[^\]\n]*\]\(\s*<?#([^\s)>]+)>?\s*\)")


def visible_lines(source):
    """Keep line numbers while omitting fenced code blocks."""
    fence = None
    for number, line in enumerate(source.splitlines(), 1):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
            continue
        if marker:
            fence = marker[1]
            continue
        yield number, line


def plain(text):
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[*`~]", "", text)
    return " ".join(text.split()).strip()


def title_key(text):
    # A trailing explanatory parenthesis is optional in a title reference.
    return re.sub(r"\s*(?:（[^（）]*）|\([^()]*\))$", "", plain(text)).strip()


def slug(text):
    text = plain(text).lower()
    text = "".join(c for c in text if c in "_-" or c.isspace() or unicodedata.category(c)[0] not in "PS")
    return re.sub(r"\s", "-", text)


def check(source):
    lines = list(visible_lines(source))
    headings, numbered, titled, anchors = [], {}, {}, set()
    for line_no, line in lines:
        match = re.match(r"^ {0,3}#{1,6}[ \t]+(.+)$", line)
        if not match:
            continue
        title = plain(re.sub(r"[ \t]+#+[ \t]*$", "", match[1]))
        item = {"line": line_no, "title": title}
        headings.append(item)
        titled.setdefault(title_key(title), []).append(item)
        number = NUMBER.match(title)
        if number:
            numbered.setdefault(number[1], []).append(item)
        base, anchor, suffix = slug(title), slug(title), 0
        while anchor in anchors:
            suffix += 1
            anchor = f"{base}-{suffix}"
        anchors.add(anchor)

    errors, checked = [], 0
    for line_no, line in lines:
        # Matching backtick runs delimit inline code; leave other prose intact.
        line = re.sub(r"(`+)(?!`)(.*?)(?<!`)\1(?!`)", "", line)
        for match in ANCHOR_REF.finditer(line):
            checked += 1
            target = unquote(match[1])
            if target not in anchors:
                errors.append({"line": line_no, "type": "anchor", "target": target, "reason": "无对应标题锚点"})
        for match in NUM_REF.finditer(line):
            checked += 1
            target = match[1]
            found = numbered.get(target, [])
            if len(found) != 1:
                errors.append({"line": line_no, "type": "section", "target": target,
                               "reason": "节号不存在" if not found else "节号对应多个标题，请改用明确链接"})
        for match in TITLE_REF.finditer(line):
            target = next(group for group in match.groups() if group is not None)
            if not PREFIX.match(plain(target)):
                continue
            checked += 1
            key = title_key(target)
            found = titled.get(key, [])
            if len(found) != 1:
                error = {"line": line_no, "type": "title", "target": target,
                         "reason": "编号与标题不匹配" if not found else "同名标题不唯一，请改用明确链接"}
                tail = PREFIX.sub("", key)
                error["possible_headings"] = [h for h in headings if PREFIX.sub("", title_key(h["title"])) == tail]
                errors.append(error)
    return {"heading_count": len(headings), "checked_reference_count": checked,
            "errors": errors, "limitations": LIMITS}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("markdown_file", type=Path, help="仅此文件会被读取；UTF-8 Markdown")
    args = parser.parse_args()
    try:
        source = args.markdown_file.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as error:
        print(json.dumps({"file": str(args.markdown_file), "input_error": str(error)}, ensure_ascii=False))
        return 2
    result = check(source)
    result["file"] = str(args.markdown_file)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
