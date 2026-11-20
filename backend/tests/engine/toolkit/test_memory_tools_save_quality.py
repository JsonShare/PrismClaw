"""T1-3 验收测试（SDD D7）：save 写侧质量控制——type 词表、置信度、来源标记、id 回传。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from prismclaw.engine.toolkit.memory.memory_tools import MemoryTool
from prismclaw.schema.memory.classify import CANONICAL_TYPES, normalize_type
from prismclaw.schema.memory.memory_types import MemoryEntry


def test_normalize_type_normalizes_case_and_defaults() -> None:
    assert normalize_type("FACT") == "fact"
    assert normalize_type("  Procedure ") == "procedure"
    assert normalize_type(None) == "fact"
    assert normalize_type("") == "fact"
    # 非兼容变更：normalize_type 单点强校验，非法值直接 raise
    with pytest.raises(ValueError, match="未知 memory type"):
        normalize_type("workflow")
    with pytest.raises(ValueError, match="未知 memory type"):
        normalize_type("procedures")
    assert normalize_type("auto_sop") == "auto_sop"  # SOP_MEMORY_TYPE 合法
    for canonical in CANONICAL_TYPES:
        assert normalize_type(canonical) == canonical


def test_capture_vocabulary_is_l2_subset() -> None:
    """聊天提取只写 L2，词表必须是受控词表的真子集且不含 procedure。"""
    from prismclaw.engine.memory.capture import _CHAT_CAPTURE_TYPES

    assert set(_CHAT_CAPTURE_TYPES) < set(CANONICAL_TYPES)
    assert "procedure" not in _CHAT_CAPTURE_TYPES


def test_capture_prompt_uses_controlled_vocabulary() -> None:
    from prismclaw.engine.memory.capture import _CHAT_CAPTURE_TYPES, build_extraction_prompt

    prompt = build_extraction_prompt([{"user": "u", "assistant": "a"}])
    assert "env_fact" not in prompt
    assert "project_context" not in prompt
    assert "procedure" not in prompt  # L3 类型不得出现在 L2 提取提示中
    for allowed in _CHAT_CAPTURE_TYPES:
        assert allowed in prompt


def test_parse_facts_clamps_unknown_types() -> None:
    from prismclaw.engine.memory.capture import parse_facts

    facts = parse_facts(
        '[{"fact":"环境是 pyclaw","type":"env_fact","time_scope":"permanent"},'
        '{"fact":"选定 Postgres","type":"DECISION","time_scope":"permanent"}]'
    )
    # 未知词归入 fact；大小写归一生效
    assert [f["type"] for f in facts] == ["fact", "decision"]


def test_save_schema_type_enum_matches_vocabulary() -> None:
    assert sorted(CANONICAL_TYPES) == MemoryTool.parameters["properties"]["type"]["enum"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("FACT", "fact"),
        ("  Procedure ", "procedure"),
        ("auto_sop", "auto_sop"),  # 系统内部类型
        ("decision", "decision"),
        ("preference", "preference"),
    ],
)
def test_memory_entry_normalizes_case(raw: str, expected: str) -> None:
    """MemoryEntry 做大小写/空白归一，并接受系统内部类型。"""
    entry = MemoryEntry(
        id="e1", layer="L2", type=raw, content="c", created_at=0.0, updated_at=0.0
    )
    assert entry.type == expected


@pytest.mark.parametrize("raw", ["procedural", "workflow", "env_fact"])
def test_memory_entry_rejects_unknown_types(raw: str) -> None:
    """未上线、无存量：词表外取值在构造期直接失败，不做静默改写。"""
    with pytest.raises(ValidationError):
        MemoryEntry(
            id="e1", layer="L2", type=raw, content="c", created_at=0.0, updated_at=0.0
        )


def test_classify_scope_uses_normalized_type() -> None:
    from prismclaw.schema.memory.classify import classify_scope

    assert classify_scope("PREFERENCE") == "user"
    assert classify_scope("preference") == "user"
    assert classify_scope("decision") == "project"


class _FakeStore:
    def __init__(self) -> None:
        self.stored: list[MemoryEntry] = []

    async def store(self, session_key: str, entry: MemoryEntry) -> None:
        self.stored.append(entry)


@pytest.mark.asyncio
async def test_save_rejects_unknown_type() -> None:
    tool = MemoryTool(memory_store=_FakeStore(), session_store=None)  # type: ignore[arg-type]
    result = await tool._save(
        "call-1",
        {"content": "x", "type": "weird_vocab"},
        SimpleNamespace(session_id="web:test"),
    )
    assert "weird_vocab" in str(result)


@pytest.mark.asyncio
async def test_save_writes_quality_controlled_entry() -> None:
    store = _FakeStore()
    tool = MemoryTool(memory_store=store, session_store=None)  # type: ignore[arg-type]
    result = await tool._save(
        "call-2",
        {"content": "部署前必须迁移 schema", "type": "FACT"},
        SimpleNamespace(session_id="web:test"),
    )
    assert len(store.stored) == 1
    entry = store.stored[0]
    assert entry.type == "fact"  # 大小写归一
    assert entry.layer == "L2"  # 由 type 派生
    assert entry.confidence == 0.7
    assert entry.origin_stage == "memorize_tool"
    assert entry.source_session_id == "web:test"
    assert entry.id[:8] in str(result)  # id 回传


@pytest.mark.parametrize(
    ("args_type", "expected_type", "expected_layer"),
    [
        (None, "fact", "L2"),  # 缺省 type → fact/L2
        ("preference", "preference", "L2"),
        ("procedure", "procedure", "L3"),
        ("PROCEDURE", "procedure", "L3"),  # 大小写归一后同样进 L3
    ],
)
@pytest.mark.asyncio
async def test_save_derives_layer_from_type(
    args_type: str | None, expected_type: str, expected_layer: str
) -> None:
    store = _FakeStore()
    tool = MemoryTool(memory_store=store, session_store=None)  # type: ignore[arg-type]
    args = {"content": "x"}
    if args_type is not None:
        args["type"] = args_type
    await tool._save("call-3", args, SimpleNamespace(session_id="web:test"))
    assert store.stored[0].type == expected_type
    assert store.stored[0].layer == expected_layer


def test_save_schema_does_not_expose_layer() -> None:
    """layer 由 type 派生，模型侧不再暴露该参数。"""
    assert "layer" not in MemoryTool.parameters["properties"]