"""parser manifest 级缓存（问题 D 的根因修复）回归测试。

覆盖三条契约：
1. 文件不变时重复解析只产生一次告警 / 一次构建；
2. 每次返回独立深拷贝，调用方原地修改不污染缓存；
3. 文件内容变化后缓存自动失效（按 mtime_ns + size 的内容身份）。
"""
from __future__ import annotations

import logging
from pathlib import Path

from prismclaw.runtime.skills import parser as skill_parser


def _write_skill(path: Path, body: str = "正文", extra: str = "") -> None:
    path.write_text(
        "---\n"
        "name: cache-probe\n"
        "description: 缓存测试\n"
        f"{extra}"
        "---\n"
        f"{body}\n",
        encoding="utf-8",
    )


def test_repeated_parse_warns_once_per_file_version(
    tmp_path: Path, caplog
) -> None:
    path = tmp_path / "SKILL.md"
    _write_skill(path, extra="unknown_key: 1\n")

    with caplog.at_level(logging.WARNING, logger=skill_parser.__name__):
        for _ in range(20):
            skill_parser.parse_skill_file(path)

    warnings = [
        record for record in caplog.records
        if "unknown frontmatter keys" in record.getMessage()
    ]
    assert len(warnings) == 1


def test_manifest_cache_returns_independent_copies(tmp_path: Path) -> None:
    path = tmp_path / "SKILL.md"
    _write_skill(path)

    first = skill_parser.parse_skill_file(path)
    first.name = "mutated"
    first.allowed_tools = ["x"]

    second = skill_parser.parse_skill_file(path)
    assert second.name == "cache-probe"
    assert second.allowed_tools != ["x"]


def test_manifest_cache_invalidated_on_content_change(tmp_path: Path) -> None:
    path = tmp_path / "SKILL.md"
    _write_skill(path, body="v1")

    assert skill_parser.parse_skill_file(path).body.strip() == "v1".strip()

    _write_skill(path, body="v2-longer-body")
    assert skill_parser.parse_skill_file(path).body.strip() == "v2-longer-body"


def test_frontmatter_only_and_full_body_are_cached_separately(
    tmp_path: Path,
) -> None:
    path = tmp_path / "SKILL.md"
    _write_skill(path, body="正文")

    light = skill_parser.parse_skill_frontmatter(path)
    assert light.body is None

    full = skill_parser.parse_skill_file(path)
    assert full.body is not None and full.body.strip() == "正文"
    # 两种模式使用不同缓存键，不得互相覆盖
    assert skill_parser.parse_skill_frontmatter(path).body is None
