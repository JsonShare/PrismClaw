"""dream 写入链路：存储层必须由 type 派生，不接受 LLM 单独指定的 layer。"""

from __future__ import annotations

import pytest

from prismclaw.engine.dream.compiler import _normalize_candidate_memories
from prismclaw.engine.dream.publishing import build_publishable_memory
from prismclaw.schema.dream.dream_types import DreamMemoryCandidate

_CANDIDATE = DreamMemoryCandidate(
    candidate_id="c1",
    session_key="web:test",
    session_id="s1",
    kind="memory",
    content="c",
)


@pytest.mark.parametrize(
    ("raw_type", "expected_type", "expected_layer"),
    [
        ("preference", "preference", "L2"),
        ("decision", "decision", "L2"),
        ("procedure", "procedure", "L3"),
        ("PROCEDURE", "procedure", "L3"),
        ("workflow", "fact", "L2"),  # 未知词在 LLM 边界归入 fact
    ],
)
def test_compiler_normalizes_type_and_derives_layer(
    raw_type: str, expected_type: str, expected_layer: str
) -> None:
    normalized = _normalize_candidate_memories([{"type": raw_type, "content": "c", "layer": "L2"}])

    assert normalized[0]["type"] == expected_type
    assert normalized[0]["layer"] == expected_layer


@pytest.mark.parametrize(
    ("payload_type", "expected_layer"),
    [("fact", "L2"), ("procedure", "L3"), ("workflow", "L2")],
)
def test_publishable_layer_follows_type(payload_type: str, expected_layer: str) -> None:
    publishable = build_publishable_memory(
        _CANDIDATE,
        {"type": payload_type, "content": "c", "layer": "L2"},  # 显式 layer 必须被忽略
    )

    assert publishable.layer == expected_layer
