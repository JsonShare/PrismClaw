"""Inline referenced documents into skill body text.

Moved from contracts.services.skills to break the contracts→engine
dependency cycle. This is an I/O function (reads the filesystem) and
belongs in the engine layer.
"""
from __future__ import annotations

import logging as _logging
import re as _re
from pathlib import Path as _Path

from prismclaw.schema.shared.utils import estimate_tokens as _estimate_tokens

_preload_logger = _logging.getLogger(__name__)

_REF_PATH_PATTERN = _re.compile(r"references/[\w/.-]+\.md")


def _inline_caps() -> tuple[int, int]:
    """惰性读取内联预算；加载失败回退旧默认 2500/7500。"""
    try:
        from prismclaw.infra.settings import load_settings
        caps = load_settings().agent.context.budget.subcaps
        return caps.doc_inline_min_chars, caps.doc_inline_max_chars
    except Exception:
        return 2500, 7500


def _inline_referenced_docs(body: str | None, skill_file_path: str | _Path) -> str:
    """Scan body 找 references/*.md 相对路径并内嵌内容,避免 LLM 后续调 read 失败。

    使用 token 估算上限（CJK 感知）替代字节上限，防止 CJK 内容消耗过多上下文。
    """
    if not body or not skill_file_path:
        return body or ""
    skill_dir = _Path(skill_file_path).parent
    if not skill_dir.is_dir():
        return body
    ref_paths = sorted(set(_REF_PATH_PATTERN.findall(body)))
    if not ref_paths:
        return body
    skill_dir_resolved = skill_dir.resolve()
    sections: list[str] = [
        body,
        "",
        "---",
        "# 已加载的参考文档",
        "",
        "以下参考文档已自动加载到你的上下文中。"
        "不要用 `read` 工具读取它们——内容已在下方提供。",
        "",
    ]
    total_tokens = 0
    loaded_count = 0
    skipped: list[str] = []
    _max_single, _max_total = _inline_caps()
    for rel in ref_paths:
        ref_file = skill_dir / rel
        try:
            ref_file.resolve().relative_to(skill_dir_resolved)
        except ValueError:
            skipped.append(f"{rel} (path escape)")
            continue
        if not ref_file.is_file():
            skipped.append(f"{rel} (not found)")
            continue
        try:
            content = ref_file.read_text(encoding="utf-8")
        except Exception as exc:
            _preload_logger.warning("skill_ref_load_failed path=%s err=%s", rel, exc)
            skipped.append(f"{rel} (read error)")
            continue
        file_tokens = _estimate_tokens(content)
        if file_tokens > _max_single:
            skipped.append(f"{rel} (too large: ~{file_tokens} tokens)")
            continue
        if total_tokens + file_tokens > _max_total:
            skipped.append(f"{rel} (budget exceeded)")
            continue
        total_tokens += file_tokens
        sections.append(f"## {rel}")
        sections.append("")
        sections.append(content)
        sections.append("")
        loaded_count += 1
    if skipped:
        sections.append("---")
        sections.append(
            "以下参考文档未能自动加载（超出内联预算或文件不存在）。如需使用，"
            "请用 `read` 工具读取“技能目录”下的对应相对路径——"
            "技能目录位于 `~/.prismclaw/assets` 运行时只读目录，`read` 工具可以直接访问。"
        )
        sections.append("")
        for s in skipped:
            sections.append(f"- {s}")
        sections.append("")
    if loaded_count == 0 and not skipped:
        return body
    _preload_logger.info(
        "skill_refs_inlined skill_dir=%s loaded=%d skipped=%d total_tokens=%d",
        skill_dir,
        loaded_count,
        len(skipped),
        total_tokens,
    )
    return "\n".join(sections)
