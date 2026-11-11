"""SOP text processing and parsing — pure functions for extraction, parsing, and validation."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import jieba

from prismclaw.schema.agent.session import MessageEntry
from prismclaw.schema.session.types import SessionTree

logger = logging.getLogger(__name__)

# Dangerous patterns indicating prompt injection or PII leakage.
# Note: URL whitelist uses hostname boundary anchors to prevent bypass.
# Bypass attempts caught:
#   - "docs.evil.com" → blocked (only specific docs sites allowed)
#   - "github.com.evil.com" → blocked (must be followed by /:?# or end)
#   - "stackoverflowx.com" → blocked (must be exact stackoverflow.com)
_DANGEROUS_PATTERNS = [
    r"~/\.ssh",
    r"id_rsa",
    r"\.aws/credentials",
    r"sk-[A-Za-z0-9]{20,}",  # API key pattern
    r"curl\s+.*\|\s*sh",  # curl | sh
    r"https?://(?!(?:github\.com|docs\.python\.org"
    r"|docs\.djangoproject\.com|stackoverflow\.com)(?:[/:?#]|$))[^\s]+",
]
_DANGEROUS_RE = re.compile("|".join(_DANGEROUS_PATTERNS), re.IGNORECASE)

_FENCE_RE = re.compile(r"```(?:json)?\s*\n?([\s\S]*?)\n?\s*```")
_JSON_ARRAY_RE = re.compile(r"\[[\s\S]*\]")

# SDD R2′.3（记忆输入侧零截断）：每个工具结果给提取 LLM 的预览上限。
# 旧值 300 字符意味着 300 字符之后的事实（路径/配置值/错误码）永远
# 进不了记忆——与同函数内 "decision-layer inputs are never truncated"
# 的 A2 声明直接矛盾。统一 2000 字符/结果：20 结果 × 2k ≈ 40k 字符，
# 远低于提取 LLM 的输入预算；已落盘的 <persisted-output> 引用跟随
# 落盘文件读取前 2000 字符（比引用自带的 500 字符预览更完整）。
_TOOL_RESULT_PREVIEW_CHARS = 2000
_PERSISTED_PATH_RE = re.compile(r"\[Full output persisted to: (.+?)\]")


def _extract_assistant_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text") or block.get("content") or ""
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _resolve_persisted_path(raw: str) -> Path:
    """把落盘引用里的路径解析为可读的实际文件。

    引用现在给的是**工作区相对**路径（``tool_results/tr_<id>_<hash>.txt``，
    见 ``execution/staging/tool_result_persistence.py``）——模型抄写面积小；
    但本模块是**从后端进程**直接读文件，手里没有工作区上下文，因此按文件名在
    工作区基目录（含 ``sessions/<sid>/`` 会话级布局）下检索：文件名带 sha256
    内容哈希，同内容必然同名，命中即为正确文件。

    绝对路径（旧引用）原样返回；检索不到也回退为原样，由调用方按既有的
    OSError 分支降级为截断预览。
    """
    p = Path(raw)
    if p.is_absolute():
        return p
    try:
        from prismclaw.infra.settings import load_settings

        base = Path(load_settings().workspaces.default).expanduser()
    except Exception:  # noqa: BLE001 - 配置不可用时按原样回退
        return p
    # 布局固定为 {base}/web_{user_id}/{workspace_id}/...；session 私有产物
    # （tool_results/context_blocks）在 sessions/<sid>/ 下。两种层级都尝试，
    # 避免旧引用/新引用任一布局漏检。
    tail = "/".join(p.parts)
    for pattern in (f"web_*/*/sessions/*/{tail}", f"web_*/*/{tail}"):
        for candidate in base.glob(pattern):
            if candidate.is_file():
                return candidate
    return p


def _tool_result_preview(text: str) -> str:
    def _clip(s: str, *, source: str) -> str:
        if len(s) <= _TOOL_RESULT_PREVIEW_CHARS:
            return s
        return (
            s[:_TOOL_RESULT_PREVIEW_CHARS]
            + f"... [{len(s)} chars total in {source}, preview truncated]"
        )

    if text.startswith("<persisted-output>"):
        match = _PERSISTED_PATH_RE.search(text)
        if match:
            try:
                full = _resolve_persisted_path(match.group(1)).read_text(
                    encoding="utf-8", errors="replace"
                )
                return _clip(full, source="persisted output")
            except OSError:
                logger.debug(
                    "sop extraction: persisted output unreadable: %s",
                    match.group(1), exc_info=True,
                )
        return _clip(text, source="persisted-output reference")
    return _clip(text, source="tool result")


def _build_segments(tree: SessionTree, candidate_turn_ids: set[str]) -> str:
    """Walk session tree and build text segments for each matching turn.

    A "turn" = user message + assistant message (whose tool_call.id matches a candidate)
    + the tool results that follow.
    """
    segments: list[str] = []
    if not tree or not tree.order:
        logger.warning(
            "[PROBE R5] _build_segments empty tree: candidates=%d tree=%s order_len=0",
            len(candidate_turn_ids),
            type(tree).__name__ if tree else None,
        )
        return ""

    order_index = {eid: idx for idx, eid in enumerate(tree.order)}
    seen_assistant_ids: set[str] = set()

    # [PROBE R5] diagnose candidate/tree mismatch (post-compaction regression)
    role_counts: dict[str, int] = {}
    all_live_tool_call_ids: set[str] = set()
    assistants_with_tool_calls = 0
    assistants_without_tool_calls = 0
    for eid in tree.order:
        e = tree.entries.get(eid)
        if not isinstance(e, MessageEntry):
            continue
        role_counts[e.role] = role_counts.get(e.role, 0) + 1
        if e.role == "assistant":
            if not e.tool_calls:
                assistants_without_tool_calls += 1
                continue
            assistants_with_tool_calls += 1
            for tc in e.tool_calls:
                if isinstance(tc, dict):
                    tc_id = str(tc.get("id", ""))
                    if tc_id:
                        all_live_tool_call_ids.add(tc_id)
    live_intersection = all_live_tool_call_ids & candidate_turn_ids
    logger.warning(
        "[PROBE R5] _build_segments state: candidates=%d live_tool_call_ids=%d "
        "intersection=%d roles=%s assistants_with_tc=%d assistants_without_tc=%d "
        "missing_candidate_ids=%s",
        len(candidate_turn_ids),
        len(all_live_tool_call_ids),
        len(live_intersection),
        role_counts,
        assistants_with_tool_calls,
        assistants_without_tool_calls,
        list(candidate_turn_ids - all_live_tool_call_ids)[:5],
    )

    for entry_id in tree.order:
        entry = tree.entries.get(entry_id)
        if not isinstance(entry, MessageEntry):
            continue

        if entry.role == "assistant":
            if not entry.tool_calls:
                continue
            tool_call_ids = {
                str((tc or {}).get("id", "")) for tc in entry.tool_calls if isinstance(tc, dict)
            }
            tool_call_ids.discard("")
            # If ANY of this assistant's tool_call ids match a candidate, build a segment
            if not (tool_call_ids & candidate_turn_ids):
                continue
            if entry.id in seen_assistant_ids:
                continue
            seen_assistant_ids.add(entry.id)

            assistant_text = _extract_assistant_text(entry.content)
            tool_lines: list[str] = []
            for tc in entry.tool_calls:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                if isinstance(fn, dict):
                    name = fn.get("name", "")
                    args = fn.get("arguments", "")
                    tool_lines.append(f"  - {name}({args})")

            # Find tool results following this assistant turn (bounded to current turn)
            tool_results: list[str] = []
            tc_ids = tool_call_ids
            start_idx = order_index.get(entry_id)
            if start_idx is None:
                start_idx = len(tree.order)
            else:
                start_idx += 1

            for follow_id in tree.order[start_idx:]:
                follow = tree.entries.get(follow_id)
                if not isinstance(follow, MessageEntry):
                    continue
                if follow.role in ("user", "assistant"):
                    break
                if follow.role == "tool" and follow.tool_call_id in tc_ids:
                    text = _extract_assistant_text(follow.content)
                    if text:
                        tool_results.append(f"    -> {_tool_result_preview(text)}")

            segment_parts: list[str] = [f"# 任务片段 {len(segments) + 1}"]
            if assistant_text:
                # SDD A2 (defect D3): decision-layer inputs are never
                # truncated — the extraction LLM must see the full intent.
                segment_parts.append(f"助手意图（供参考）：{assistant_text}")
            if tool_lines:
                segment_parts.append("工具调用：\n" + "\n".join(tool_lines))
            if tool_results:
                segment_parts.append("工具结果：\n" + "\n".join(tool_results))

            segments.append("\n".join(segment_parts))

    return "\n\n---\n\n".join(segments)


def _parse_llm_output(text: str) -> list[dict[str, Any]] | None:
    """Parse LLM JSON output into a list of SOP dicts.

    Handles: markdown fences, JSON-in-prose, object wrappers ({"sops": [...]}).
    Returns None on unrecoverable parse failure (caller may retry).
    """
    if not text or not text.strip():
        return None

    cleaned = text.strip()

    fence_match = _FENCE_RE.search(cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        array_match = _JSON_ARRAY_RE.search(cleaned)
        if not array_match:
            return None
        try:
            parsed = json.loads(array_match.group(0))
        except json.JSONDecodeError:
            return None

    if isinstance(parsed, dict):
        for key in ("sops", "procedures", "results", "data", "items"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
        else:
            return None

    if not isinstance(parsed, list):
        return None

    return [item for item in parsed if isinstance(item, dict)]


def _parse_sop_tool_calls(tool_calls: Any) -> list[dict[str, Any]] | None:  # noqa: ANN401
    if not isinstance(tool_calls, list) or not tool_calls:
        return None
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        fn = call.get("function")
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        if not isinstance(name, str) or not name:
            continue
        if name != "sop_extractor":
            continue
        args = fn.get("arguments")
        if isinstance(args, dict):
            payload = args
        elif isinstance(args, str):
            payload = json.loads(args)
        else:
            continue
        if not isinstance(payload, dict):
            continue
        sops = payload.get("sops")
        if isinstance(sops, list):
            return [item for item in sops if isinstance(item, dict)]
    return None


def _validate_sop(
    sop: Any,  # noqa: ANN401
    description_max_chars: int = 150,
    procedure_max_chars: int = 5000,
) -> tuple[bool, str]:
    """Validate SOP schema strictly. Returns (is_valid, reason_if_invalid).

    Rejects any SOP that fails schema or contains dangerous patterns.
    Caller should log INFO and skip rejected SOPs (no L3 write).

    `description_max_chars` and `procedure_max_chars` cap the length of the
    description and procedure fields respectively. Defaults match the prompt
    template defaults for backward compatibility, but in production both
    should be passed from EvolutionSettings so the prompt and validator
    stay in sync — the same configured value is injected into the LLM
    prompt to prevent spec drift between what the LLM is told to produce
    and what the validator accepts.
    """
    if not isinstance(sop, dict):
        return False, "not a dict"

    name = sop.get("name")
    if not isinstance(name, str) or not name.strip():
        return False, "missing or invalid 'name'"
    if len(name) > 80:
        return False, "name too long (>80 chars)"

    description = sop.get("description")
    if not isinstance(description, str):
        return False, "'description' not a string"
    if len(description) > description_max_chars:
        return False, f"description too long (>{description_max_chars} chars)"

    procedure = sop.get("procedure")
    if not isinstance(procedure, str):
        return (
            False,
            f"'procedure' MUST be a string (got {type(procedure).__name__})",
        )
    if not procedure.strip():
        return False, "procedure is empty"
    if len(procedure) > procedure_max_chars:
        return False, f"procedure too long (>{procedure_max_chars} chars)"

    full_content = f"{name}\n{description}\n{procedure}"
    if _DANGEROUS_RE.search(full_content):
        return False, "contains dangerous pattern (PII/credentials/external URL)"

    return True, ""


def _format_sop_content(sop: dict[str, Any]) -> str:
    """Format a validated SOP dict into stored content string.

    Assumes input has already passed _validate_sop. All fields are strings.
    """
    name = sop["name"].strip()
    description = sop["description"].strip()
    procedure = sop["procedure"].strip()
    return f"{name}\n{description}\n{procedure}"


def _jaccard_overlap(a: str, b: str) -> float:
    """Jaccard token overlap using jieba.cut_for_search.

    Returns 0.0 if either side has fewer than 2 tokens (Jaccard with
    single-token sets is binary 0/1 and statistically meaningless).
    """
    tokens_a = {t.strip().lower() for t in jieba.cut_for_search(a) if t.strip()}
    tokens_b = {t.strip().lower() for t in jieba.cut_for_search(b) if t.strip()}
    if len(tokens_a) < 2 or len(tokens_b) < 2:
        return 0.0
    union = tokens_a | tokens_b
    if not union:
        return 0.0
    return len(tokens_a & tokens_b) / len(union)
