"""Skill graduation — promotes high-frequency SOPs from L3 procedures to SKILL.md files."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prismclaw.schema.shared.async_utils import await_or_abort
from prismclaw.schema.llm.request import PackedLLMRequest

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")
_AUTO_GENERATED_RE = re.compile(r"^auto_generated:\s*true\s*$", re.MULTILINE)


def _is_auto_generated(skill_text: str) -> bool:
    return bool(_AUTO_GENERATED_RE.search(skill_text))


def _content_body_unchanged(old_text: str, new_text: str) -> bool:
    old_body = _strip_frontmatter(old_text)
    new_body = _strip_frontmatter(new_text)
    return old_body.strip() == new_body.strip()


def _nested(settings: Any, attr: str, default: Any) -> Any:
    """从完整 Settings 或扁平 section settings 取属性。

    调用方可能传完整 Settings（字段在 ``.skills`` 下）或直接传 SkillSettings。
    """
    nested = getattr(settings, attr, None) if settings is not None else None
    return nested if nested is not None else (settings if settings is not None else default)


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    newline_after_open = text.find("\n", 3)
    if newline_after_open == -1:
        return text
    close_pos = text.find("\n---", newline_after_open)
    if close_pos == -1:
        return text
    end_of_close_line = text.find("\n", close_pos + 1)
    if end_of_close_line == -1:
        return ""
    return text[end_of_close_line:]


def _backup_previous_version(skill_file: Path) -> Path:
    history_dir = skill_file.parent / "skill_history"
    history_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_name = f"SKILL.md.{timestamp}"
    backup_path = history_dir / backup_name
    backup_path.write_text(skill_file.read_text(encoding="utf-8"), encoding="utf-8")
    return backup_path


def parse_sop_content(content: str) -> tuple[str, str, str] | None:
    """Parse L3 SOP content into (name, description, procedure).

    The stored format is: ``name\\ndescription\\nprocedure``
    (see ``sop_extraction._format_sop_content``).

    Returns None if content format is unparseable or name is invalid.
    """
    if not content or not content.strip():
        return None

    parts = content.split("\n", 2)
    if len(parts) < 3:
        logger.warning("SOP content has < 3 lines, cannot parse for graduation")
        return None

    name = parts[0].strip()
    description = parts[1].strip()
    procedure = parts[2].strip()

    if not _NAME_RE.match(name):
        logger.warning(
            "SOP name '%s' doesn't match kebab-case pattern, skipping graduation",
            name,
        )
        return None

    if not procedure:
        logger.warning("SOP '%s' has empty procedure, skipping graduation", name)
        return None

    return name, description, procedure


def generate_skill_md_template(
    name: str,
    description: str,
    procedure: str,
    session_key: str,
) -> str:
    """Generate SKILL.md content using pure template (no LLM)."""
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    safe_desc = description.replace('"', '\\"')

    return f'''---
name: {name}
description: "{safe_desc}"
auto_generated: true
lifecycle: active
generated_at: "{now_iso}"
source_session: "{session_key}"
---

# {name}

{description}

## Procedure

{procedure}
'''


async def generate_skill_md_enrich(
    name: str,
    description: str,
    procedure: str,
    session_key: str,
    llm_client: Any,
    model: str | None,
    *,
    abort_event: asyncio.Event | None = None,
) -> str:
    """Generate enriched SKILL.md via LLM. Falls back to template on failure."""
    prompt = (
        "将以下 SOP 扩展为完整的 SKILL.md 格式。保持原有步骤不变，"
        "但添加 When to Use、Prerequisites、Common Issues 等 section。\n\n"
        f"SOP Name: {name}\n"
        f"Description: {description}\n"
        f"Procedure:\n{procedure}\n\n"
        "输出完整的 SKILL.md 内容（含 frontmatter）。frontmatter 必须包含:\n"
        f"name: {name}\n"
        "description: (你的改进版)\n"
        "auto_generated: true\n"
        "lifecycle: active\n"
        "generated_at: (当前时间 ISO8601)\n"
        f'source_session: "{session_key}"\n'
    )

    try:
        # WP-2.3 — race the LLM call against the abort_event so a
        # user-initiated cancel propagates immediately. Timeout remains
        # as a backstop.
        response = await await_or_abort(
            llm_client.complete(
                request=PackedLLMRequest(
                    messages=[{"role": "user", "content": prompt}],
                    system="",
                    context_payload=None,
                    cache_strategy={},
                ),
                model=model,
            ),
            abort_event,
            timeout=15.0,
        )
        if response and response.text and len(response.text) > 100:
            return response.text
    except (TimeoutError, Exception) as exc:
        logger.warning(
            "Skill enrich LLM failed for '%s': %s, falling back to template",
            name,
            exc,
        )

    return generate_skill_md_template(name, description, procedure, session_key)


def graduate_single_sop(
    entry_id: str,
    content: str,
    session_key: str,
    workspace_base_dir: Path,
    mode: str = "template",
    *,
    skill_discoverer: Any | None = None,
    settings: Any | None = None,
) -> tuple[bool, str | None]:
    """Attempt to graduate a single SOP to SKILL.md.

    Returns (success: bool, skill_path: str | None).
    Does NOT update DB — caller handles that.

    ``session_key`` 格式固定为 ``web:{uid}:{wid}``（ids.build_web_session_key），
    真实 workspace 目录是两层嵌套：
      ``{workspace_base_dir}/web_{uid}/{wid}``
    而非一层 ``web_{uid}_{wid}``。这里严格按 session_key 解析，杜绝 flat replace
    导致 graduation 落进不存在的旁支目录。
    """
    parsed = parse_sop_content(content)
    if parsed is None:
        return False, None

    name, description, procedure = parsed

    # —— 从 session_key 解析真实 workspace 路径（两层嵌套）——
    # session_key 预期 "web:{uid}:{wid}"；降级时整段当 workspace_id 拼。
    segments = session_key.split(":")
    uid, wid = segments[1], segments[2] if len(segments) > 2 else segments[-1]
    workspace_path = (workspace_base_dir / f"web_{uid}" / wid) if len(segments) >= 3 else workspace_base_dir

    # —— 能力目录字段从 settings 取（双来源的 workspace 级落点）——
    from prismclaw.infra.settings import SkillSettings
    skill_settings = _nested(settings, "skills", SkillSettings())
    rel_dir = getattr(skill_settings, "workspace_skills_dir", ".work/skills") or ".work/skills"
    skill_dir = workspace_path / rel_dir / name
    skill_file = skill_dir / "SKILL.md"

    if skill_file.exists():
        existing_text = skill_file.read_text(encoding="utf-8")
        if not _is_auto_generated(existing_text):
            logger.debug(
                "SKILL.md at %s is user-authored, skipping overwrite", skill_file
            )
            return False, None
        new_content = generate_skill_md_template(
            name, description, procedure, session_key
        )
        if _content_body_unchanged(existing_text, new_content):
            logger.debug("SKILL.md at %s unchanged, skipping", skill_file)
            return False, None
        backup_path = _backup_previous_version(skill_file)
        logger.info(
            "Upgrading auto-generated SKILL.md '%s' (backed up to %s)",
            name,
            backup_path,
        )
        try:
            skill_file.write_text(new_content, encoding="utf-8")
        except OSError as exc:
            logger.error("Failed to overwrite SKILL.md at %s: %s", skill_file, exc)
            return False, None
        return True, str(skill_file)

    # Cross-layer collision check: skip if skill name exists from another source.
    # discover_skills 是 keyword-only；不要用位置参数调它。
    if skill_discoverer is not None:
        try:
            existing_skills = skill_discoverer(
                project_locations=[workspace_path],
                settings=skill_settings,
            )
            if any(s.name == name for s in existing_skills):
                logger.info("Skill '%s' already exists from another source, skipping graduation", name)
                return False, None
        except Exception:
            pass  # Discovery failure should not block graduation

    skill_content = generate_skill_md_template(name, description, procedure, session_key)
    try:
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file.write_text(skill_content, encoding="utf-8")
    except OSError as exc:
        logger.error("Failed to write SKILL.md at %s: %s", skill_file, exc)
        return False, None

    if not skill_file.exists():
        logger.error("SKILL.md verification failed at %s", skill_file)
        return False, None

    logger.info("Graduated SOP '%s' → %s", name, skill_file)
    return True, str(skill_file)
