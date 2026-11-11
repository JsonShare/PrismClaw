"""Fact Sheet — 结构化事实层，永不压缩、永不丢弃。

核心思想：对话历史层可压缩（有损），事实层只追加/更新（无损）。
每次压缩后，完整 Fact Sheet 被注入 LLM 上下文，确保关键事实
在任意压缩深度下都可恢复。

设计决策：
  - 提取：用 LLM 从用户消息中提取 key-value 事实（格式约束为 JSON）
  - 存储：通过 CustomEntry("fact_sheet") 持久化到 ConversationTree
  - 注入：压缩时 + 查询时完整注入，不依赖摘要继承
"""
from __future__ import annotations

import json
import logging
from typing import Any

from prismclaw.schema.session import (
    CustomEntry,
    SessionTree,
    _CustomPayloadFactSheet,
    generate_entry_id,
)

logger = logging.getLogger(__name__)

# P3-FUS-01: L3 Fact Sheet 推送前脱敏
from prismclaw.schema.shared.sanitize_injection import sanitize_before_context_injection

# ── Fact Sheet 提取 prompt ──────────────────────────────────────

_FACT_EXTRACTION_SYSTEM = """\
你是一个事实提取引擎。你的唯一职责是从用户最新消息中提取结构化事实，并以 JSON 对象输出。

规则：
1. 只提取明确陈述的事实——即用户作为当前状态或指令陈述的内容。
2. 键名应简洁、可识别（例如 "通知时间"、"降糖药"、"随访频率"）。
3. 值只保留当前/最新取值。若某事实已更新，使用新值。
4. 若某事实被明确作废/删除/撤销，将值设为 "VOID"。
5. 若无法提取任何事实，输出 {}（空对象）。
6. 绝不编造事实。绝不推断未陈述的信息。
7. 只输出 JSON 对象，不要解释，不要 markdown。

示例：
  用户："时间改到周六14:00" → {"通知时间": "周六14:00"}
  用户："停二甲双胍" → {"降糖药": "VOID"}
  用户："加用阿托伐他汀20mg qn" → {"降脂药": "阿托伐他汀20mg qn"}
  用户："帮我整理一下" → {}
"""

_FACT_EXTRACTION_USER_TEMPLATE = """\
当前事实表：
{existing_facts_json}

用户最新消息：
{user_message}

提取到的事实（仅 JSON）："""


# ── Fact Sheet 读写 ─────────────────────────────────────────────

def load_fact_sheet(tree: SessionTree) -> dict[str, str]:
    """从 SessionTree 中加载最新的 Fact Sheet。

    遍历 tree.entries，找到所有 fact_sheet 类型的 CustomEntry，
    合并其 facts（后者覆盖前者，保证最新值）。
    """
    facts: dict[str, str] = {}
    for entry_id in tree.order:
        entry = tree.entries.get(entry_id)
        if not isinstance(entry, CustomEntry):
            continue
        if getattr(entry, "custom_type", "") != "fact_sheet":
            continue
        # get_typed_data() for fact_sheet returns the 'data' dict
        # which contains {'custom_type': 'fact_sheet', 'facts': {...}}
        raw = entry.data
        if not isinstance(raw, dict):
            continue
        entry_facts = raw.get("facts")
        if not isinstance(entry_facts, dict):
            continue
        # VOID 标记的键从事实表中移除（已确认作废）
        for k, v in entry_facts.items():
            if v == "VOID":
                facts.pop(k, None)
            else:
                facts[k] = v
    return facts


def save_fact_sheet(tree: SessionTree, facts: dict[str, str], parent_id: str | None = None) -> str:
    """将 Fact Sheet 增量保存到 SessionTree。

    只追加变更项（与现有事实表的 diff），避免全量重复。
    返回新 entry 的 ID。
    """
    existing = load_fact_sheet(tree)
    # 计算增量
    diff: dict[str, str] = {}
    for k, v in facts.items():
        if existing.get(k) != v:
            diff[k] = v
    # 移除已不存在的键
    for k in existing:
        if k not in facts:
            diff[k] = "VOID"

    if not diff:
        return ""  # 无变更

    payload = _CustomPayloadFactSheet(custom_type="fact_sheet", facts=diff)
    existing_ids = set(tree.entries.keys())
    entry_id = generate_entry_id(existing_ids)
    entry = CustomEntry(
        id=entry_id,
        type="custom",
        custom_type="fact_sheet",
        data=payload.model_dump(),
        parent_id=parent_id or tree.leaf_id or "",
    )
    tree.append(entry)
    return entry.id


def render_fact_sheet_text(facts: dict[str, str]) -> str:
    """将 Fact Sheet 渲染为可读文本，供 LLM 注入。

    P3-FUS-01: L3 Fact Sheet 推送前对每个值执行 Safe Harbor 脱敏，
    防止事实层将 PHI（姓名、MRN、SSN）直接暴露给 LLM。
    """
    if not facts:
        return ""
    lines = ["[FACT SHEET — 不可变事实，必须完整保留]"]
    for k, v in sorted(facts.items()):
        # 对 value 脱敏，保留 key 不变（key 是结构化字段名，不包含 PHI）
        sanitized_value, _ = sanitize_before_context_injection(
            str(v), layer="L3", source="fact_sheet"
        )
        lines.append(f"  {k}: {sanitized_value}")
    raw = "\n".join(lines)
    # 再整体扫一次防止 header 被污染
    final, _ = sanitize_before_context_injection(raw, layer="L3", source="fact_sheet")
    return final


# ── Fact Sheet 提取（LLM 调用）──────────────────────────────────

async def extract_facts_from_message(
    user_message: str,
    existing_facts: dict[str, str],
    llm_adapter: Any,
) -> dict[str, str]:
    """用 LLM 从用户消息中提取结构化事实。

    Args:
        user_message: 用户的最新消息
        existing_facts: 当前 Fact Sheet（提供上下文）
        llm_adapter: LLM 适配器（需支持简单调用接口）

    Returns:
        提取到的事实 dict（仅变更项），如果提取失败返回空 dict
    """
    import re as _re

    existing_json = json.dumps(existing_facts, ensure_ascii=False) if existing_facts else "{}"
    user_content = _FACT_EXTRACTION_USER_TEMPLATE.format(
        existing_facts_json=existing_json,
        user_message=user_message,
    )

    try:
        # 调用 LLM（通过适配器的简单接口）
        response = await llm_adapter.chat(
            system=_FACT_EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
            temperature=0.0,
            max_tokens=512,
        )
        raw = response.strip()

        # 尝试解析 JSON
        # 容错：LLM 可能包裹在 ```json ... ``` 中
        json_match = _re.search(r'\{[^{}]*\}', raw, _re.DOTALL)
        if not json_match:
            return {}
        parsed = json.loads(json_match.group())

        # 验证格式：必须是 dict[str, str]
        if not isinstance(parsed, dict):
            return {}
        clean: dict[str, str] = {}
        for k, v in parsed.items():
            if isinstance(k, str) and isinstance(v, str):
                clean[k] = v
        return clean

    except (json.JSONDecodeError, Exception) as e:
        logger.warning("fact extraction failed: %s", e)
        return {}
