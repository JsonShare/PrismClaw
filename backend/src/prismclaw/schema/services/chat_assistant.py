"""Chat assistant service — followup questions and reference source extraction.

Moved from engine.services.chat_assistant_service to break
delivery → engine dependency (G2).

All dependencies are on contracts/kernel layer only.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, TYPE_CHECKING

from prismclaw.schema.llm.types import LLMStreamFn
from prismclaw.schema.storage.storage_types import SessionStore

if TYPE_CHECKING:
    from prismclaw.schema.agent.session import MessageEntry

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════
# Inlined from kernel to break contracts → kernel circular dependency
# ══════════════════════════════════════════════════════════════════

_IMAGE_PLACEHOLDER = "[图片]"
_FILE_PLACEHOLDER = "[附件]"


def _file_reference_text(
    path: str,
    *,
    name: str | None = None,
    mime_type: str | None = None,
) -> str:
    display_name = (name or "").strip() or Path(path).name or "file"
    parts = [f"{_FILE_PLACEHOLDER} {display_name}", f"({path})"]
    if mime_type:
        parts.append(f"[{mime_type}]")
    return " ".join(parts)


def _extract_text_from_content(content: Any) -> str:
    """Extract text from content that may be str, list[ContentBlock], or list[dict].

    Inlined from kernel.utils to break circular dependency.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            btype = block.get("type")
            if btype == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif btype in ("image", "image_url"):
                parts.append(_IMAGE_PLACEHOLDER)
                source_path = block.get("source_path")
                if isinstance(source_path, str) and source_path.strip():
                    parts.append(_file_reference_text(source_path.strip()))
            elif btype == "file":
                path = block.get("path")
                if isinstance(path, str) and path.strip():
                    parts.append(
                        _file_reference_text(
                            path.strip(),
                            name=block.get("name") if isinstance(block.get("name"), str) else None,
                            mime_type=(
                                block.get("mime_type")
                                if isinstance(block.get("mime_type"), str)
                                else None
                            ),
                        )
                    )
            continue

        text_attr = getattr(block, "text", None)
        if isinstance(text_attr, str):
            parts.append(text_attr)
            continue

        btype_attr = getattr(block, "type", None)
        if btype_attr == "image":
            parts.append(_IMAGE_PLACEHOLDER)
            source_path_attr = getattr(block, "source_path", None)
            if isinstance(source_path_attr, str) and source_path_attr.strip():
                parts.append(_file_reference_text(source_path_attr.strip()))
        elif btype_attr == "file":
            path_attr = getattr(block, "path", None)
            if isinstance(path_attr, str) and path_attr.strip():
                parts.append(
                    _file_reference_text(
                        path_attr.strip(),
                        name=getattr(block, "name", None),
                        mime_type=getattr(block, "mime_type", None),
                    )
                )

    return "\n".join(parts)


def _looks_like_chinese_text(text: str) -> bool:
    """Inlined from kernel.text_utils to break circular dependency."""
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            return True
    return False


def _parse_json_string_list(raw: str, *, max_items: int) -> list[str]:
    """Inlined from kernel.text_utils to break circular dependency."""
    text = (raw or "").strip()
    if not text:
        return []
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end < 0 or end <= start:
        return []
    payload = text[start : end + 1]
    try:
        import json

        data = json.loads(payload)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for item in data:
        if isinstance(item, str):
            cleaned = item.strip()
            if cleaned:
                out.append(cleaned)
        if len(out) >= max_items:
            break
    return out


async def load_recent_transcript(
    session_store: SessionStore | None,
    session_id: str,
    *,
    max_turns: int = 5,
    roles: tuple[str, ...] = ("user", "assistant"),
) -> list[Any]:
    """从 session store 加载最近几轮对话的 MessageEntry。

    使用 duck typing (entry.type == 'message') 代替 isinstance 检查，
    避免 contracts 运行时依赖 kernel。
    """
    if session_store is None:
        return []
    try:
        tree = await session_store.load(session_id)
    except Exception:
        return []
    if tree is None:
        return []
    branch = tree.get_branch()
    selected: list[Any] = []
    user_count = 0
    for entry in reversed(branch):
        if getattr(entry, 'type', None) != 'message':
            continue
        if entry.partial:
            continue
        if entry.role not in roles:
            continue
        selected.append(entry)
        if entry.role == "user":
            user_count += 1
            if user_count >= max_turns:
                break
    selected.reverse()
    return selected


def format_transcript_text(
    entries: list[MessageEntry],
    *,
    max_chars_per_entry: int = 1200,
) -> tuple[str, str]:
    """将 MessageEntry 列表格式化为文本。返回 (transcript, last_user_text)。"""
    lines: list[str] = []
    last_user_text = ""
    for entry in entries:
        text = _extract_text_from_content(entry.content).strip()
        if not text:
            continue
        if len(text) > max_chars_per_entry:
            text = text[:max_chars_per_entry].rstrip() + "…"
        role = (
            "User"
            if entry.role == "user"
            else "Assistant"
            if entry.role == "assistant"
            else "Tool"
        )
        lines.append(f"{role}: {text}")
        if entry.role == "user":
            last_user_text = text
    return "\n".join(lines).strip(), last_user_text


async def generate_followup_questions(
    llm: LLMStreamFn,
    session_store: SessionStore | None,
    *,
    session_id: str,
    user_message: str,
    raw_attachments: list[Any],
    assistant_text: str | None = None,
    max_turns: int = 5,
) -> list[str]:
    """基于对话历史生成推荐追问问题。"""
    from prismclaw.schema.llm.request import PackedLLMRequest

    selected = await load_recent_transcript(
        session_store, session_id, max_turns=max_turns
    )
    lines: list[str] = []
    last_user_text = ""
    for entry in selected:
        text = _extract_text_from_content(entry.content).strip()
        if not text:
            continue
        if len(text) > 1200:
            text = text[:1200].rstrip() + "…"
        role = "User" if entry.role == "user" else "Assistant"
        lines.append(f"{role}: {text}")
        if entry.role == "user":
            last_user_text = text

    # 基线任务在请求开始时就跑：此时本轮 user 消息尚未落进 session 树，
    # 新会话的 transcript 为空。补上当前提问，基线任务才有输入可生成。
    current_user_text = user_message.strip()
    if current_user_text and last_user_text[:1200] != current_user_text[:1200]:
        lines.append(f"User: {current_user_text}")
        last_user_text = current_user_text

    if assistant_text:
        lines.append(f"Assistant: {assistant_text[:800]}")
    transcript = "\n".join(lines).strip()
    if not transcript:
        return []

    target_lang = "Chinese" if _looks_like_chinese_text(last_user_text) else "English"
    prompt = (
        "Based on the conversation transcript, generate exactly 3 follow-up questions the user could ask next.\n"
        "Rules:\n"
        "- Use the same language as the user's last message (target language: "
        f"{target_lang}).\n"
        "- Questions must be grounded in the transcript and must not introduce new facts.\n"
        "- Keep each question concise.\n"
        "- Output ONLY a JSON array of 3 strings. No numbering, no extra text.\n\n"
        "Transcript:\n"
        f"{transcript}\n"
    )
    try:
        resp = await llm.complete(
            request=PackedLLMRequest(
                messages=[{"role": "user", "content": prompt}],
                system="",
                context_payload=None,
                cache_strategy={},
            ),
            temperature=0.0,
        )
        full_text = resp.text
    except Exception:
        return []
    return _parse_json_string_list(full_text, max_items=3)


async def extract_reference_sources(
    session_store: SessionStore | None,
    *,
    session_id: str,
    max_turns: int = 5,
) -> list[str]:
    """从对话历史中提取参考 URL 来源。不需要 LLM。"""
    selected = await load_recent_transcript(
        session_store,
        session_id,
        max_turns=max_turns,
        roles=("user", "assistant", "tool"),
    )
    transcript, _ = format_transcript_text(selected)
    if not transcript:
        return []

    url_re = re.compile(r"https?://[^\s)<>\"]+", re.IGNORECASE)
    seen: set[str] = set()
    out: list[str] = []
    for m in url_re.finditer(transcript):
        raw = (m.group(0) or "").strip()
        if not raw:
            continue
        raw = raw.rstrip(").,;]}")
        if not raw.startswith("http://") and not raw.startswith("https://"):
            continue
        if raw in seen:
            continue
        seen.add(raw)
        out.append(raw)
        if len(out) >= 8:
            break
    return out
