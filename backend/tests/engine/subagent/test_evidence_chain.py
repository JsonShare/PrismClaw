"""Evidence Chain 端到端自动化测试。

覆盖 evidence 收集、结构化解析、SessionState 持久化、跨轮 prior_evidence、
handover 渲染和 Prompt 规则。多 Agent 聚合只投影独立 results[]，不自动
去重证据、合并结论或计算全体最低置信度；主 Agent 负责显式综合。
SessionState 自身已有的 evidence 去重行为保持独立验证。
"""

from __future__ import annotations

import json
from typing import Any

from prismclaw.schema.agent.session import (
    SessionContextItem,
    SessionContextState,
    SessionStateUpdate,
)
from prismclaw.schema.content.subagent import (
    EvidenceRef,
    SubagentResultEnvelope,
)

# ═══════════════════════════════════════════════════════════════
# Helpers: 构造模拟数据
# ═══════════════════════════════════════════════════════════════


def _make_read_messages(
    file_path: str, content: str, offset: int | None = None
) -> list[dict[str, Any]]:
    """构造 read 工具调用的 assistant + tool result 消息对。"""
    tc_id = "tc_read_001"
    args: dict[str, Any] = {"file_path": file_path}
    if offset:
        args["offset"] = offset
        args["limit"] = 50
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": tc_id,
                    "type": "function",
                    "function": {
                        "name": "read",
                        "arguments": json.dumps(args),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": tc_id,
            "content": content,
        },
    ]


def _make_grep_messages(pattern: str, content: str) -> list[dict[str, Any]]:
    tc_id = "tc_grep_001"
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": tc_id,
                    "type": "function",
                    "function": {
                        "name": "grep",
                        "arguments": json.dumps({"pattern": pattern}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": tc_id,
            "content": content,
        },
    ]


def _make_web_fetch_messages(url: str, content: str) -> list[dict[str, Any]]:
    tc_id = "tc_web_001"
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": tc_id,
                    "type": "function",
                    "function": {
                        "name": "web_fetch",
                        "arguments": json.dumps({"url": url}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": tc_id,
            "content": content,
        },
    ]


def _make_bash_messages(command: str, output: str) -> list[dict[str, Any]]:
    tc_id = "tc_bash_001"
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": tc_id,
                    "type": "function",
                    "function": {
                        "name": "bash",
                        "arguments": json.dumps({"command": command}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": tc_id,
            "content": output,
        },
    ]


def _make_session_state(
    *,
    facts: list[str] | None = None,
    evidence: list[dict] | None = None,
) -> SessionContextState:
    """构造一个最小化的 SessionContextState。"""
    state = SessionContextState(session_id="test-session-001")
    if facts:
        for f in facts:
            state.facts.append(
                SessionContextItem(
                    id=f"fac_{hash(f) % 10000:04d}",
                    kind="fact",
                    content=f,
                )
            )
    if evidence:
        state.evidence = evidence
    return state


# ═══════════════════════════════════════════════════════════════
# R01: 单 researcher 成功返回, evidence 包含 read/grep/web_fetch
# ═══════════════════════════════════════════════════════════════


def test_r01_collect_evidence_from_multiple_tools():
    from prismclaw.engine.subagent.core._lifecycle_mixin import _collect_evidence_from_messages

    messages = []
    messages.extend(_make_read_messages("/src/main.py", "def main(): pass"))
    messages.extend(_make_grep_messages("TODO", "main.py:10: TODO: fix this"))
    messages.extend(_make_web_fetch_messages("https://docs.example.com", "API reference content"))

    evidence = _collect_evidence_from_messages(messages)
    assert len(evidence) == 3

    assert evidence[0].source_type == "file"
    assert evidence[0].source_path == "/src/main.py"
    assert evidence[0].confidence == "high"

    assert evidence[1].source_type == "tool_output"
    assert evidence[1].source_path == "TODO"

    assert evidence[2].source_type == "web"
    assert evidence[2].source_path == "https://docs.example.com"


# ═══════════════════════════════════════════════════════════════
# R02: 并行结果保留各 Agent 的独立 evidence，不自动合并去重
# ═══════════════════════════════════════════════════════════════


def test_r02_parallel_results_preserve_independent_evidence():
    from prismclaw.engine.subagent.tool import SubAgentTool
    from prismclaw.schema.content.content_types import (
        SubagentEnvelopeArtifact,
        TextBlock,
        ToolResult,
    )

    evidence = EvidenceRef(
        source_type="file",
        source_path="/src/main.py",
        quoted_text="def main(): pass",
        confidence="high",
    )

    def raw_result(name: str) -> dict:
        envelope = SubagentResultEnvelope(summary=name, evidence=[evidence])
        tool_result = ToolResult(
            tool_call_id="call-r02",
            content=[TextBlock(text=envelope.model_dump_json())],
            subagent_envelope=SubagentEnvelopeArtifact(result_envelope=envelope),
        )
        return {"agent_name": name, "outcome": "success", "tool_result": tool_result}

    aggregated = SubAgentTool._aggregate_results(
        None,
        [raw_result("explorer-1"), raw_result("explorer-2")],
        "call-r02",
    )

    assert aggregated.subagent_envelope is not None
    assert aggregated.subagent_envelope.result_envelope is None
    items = aggregated.subagent_envelope.results
    assert len(items) == 2
    assert [item.envelope.evidence[0].source_path for item in items] == [
        "/src/main.py",
        "/src/main.py",
    ]


# ═══════════════════════════════════════════════════════════════
# R03: researcher 超时退出, evidence 仍被收集
# ═══════════════════════════════════════════════════════════════


def test_r03_evidence_collected_on_non_success_outcome():
    """验证 _collect_evidence_from_messages 不依赖 outcome，
    在 _lifecycle_mixin.py 中已移到 outcome 分支前。"""
    from prismclaw.engine.subagent.core._lifecycle_mixin import _collect_evidence_from_messages

    messages = _make_read_messages("/src/config.yaml", "timeout: 30")
    evidence = _collect_evidence_from_messages(messages)
    # 即使 outcome 是 timeout/error，messages 中有工具调用就应该提取到 evidence
    assert len(evidence) == 1
    assert evidence[0].source_path == "/src/config.yaml"

    # 模拟非 success 时创建 envelope（_lifecycle_mixin.py L304-311 逻辑）
    if evidence:
        envelope = SubagentResultEnvelope(
            summary="[timeout] Agent exceeded time budget",
            evidence=evidence,
            confidence_assessment="low",
        )
        assert envelope.confidence_assessment == "low"
        assert len(envelope.evidence) == 1


# ═══════════════════════════════════════════════════════════════
# R04: subagent error 退出, 部分 evidence 被保留
# ═══════════════════════════════════════════════════════════════


def test_r04_partial_evidence_on_error():
    from prismclaw.engine.subagent.core._lifecycle_mixin import _collect_evidence_from_messages

    # 模拟：先成功一次 read，然后 error
    messages = []
    messages.extend(_make_read_messages("/src/main.py", "def main(): pass"))
    messages.append({"role": "assistant", "content": "I encountered an error"})
    # 没有更多 tool call

    evidence = _collect_evidence_from_messages(messages)
    assert len(evidence) == 1
    assert evidence[0].source_path == "/src/main.py"


# ═══════════════════════════════════════════════════════════════
# R05: 每个 Agent 的 confidence 独立保留，不计算全体最低值
# ═══════════════════════════════════════════════════════════════


def test_r05_confidence_remains_scoped_to_each_agent():
    from prismclaw.engine.subagent.tool import SubAgentTool
    from prismclaw.schema.content.content_types import (
        SubagentEnvelopeArtifact,
        TextBlock,
        ToolResult,
    )

    def raw_result(name: str, confidence: str) -> dict:
        envelope = SubagentResultEnvelope(
            summary=name,
            confidence_assessment=confidence,
        )
        tool_result = ToolResult(
            tool_call_id="call-r05",
            content=[TextBlock(text=envelope.model_dump_json())],
            subagent_envelope=SubagentEnvelopeArtifact(result_envelope=envelope),
        )
        return {"agent_name": name, "outcome": "success", "tool_result": tool_result}

    aggregated = SubAgentTool._aggregate_results(
        None,
        [raw_result("high", "high"), raw_result("low", "low")],
        "call-r05",
    )

    assert aggregated.subagent_envelope is not None
    assert aggregated.subagent_envelope.result_envelope is None
    assert [
        item.envelope.confidence_assessment for item in aggregated.subagent_envelope.results
    ] == ["high", "low"]


# ═══════════════════════════════════════════════════════════════
# R10: 跨轮次 prior_evidence 传递
# ═══════════════════════════════════════════════════════════════


def test_r10_prior_evidence_cross_round():
    from prismclaw.engine.subagent.core.contract import _collect_prior_evidence

    ev_dict = {
        "source_type": "file",
        "source_path": "/src/main.py",
        "source_location": "L10",
        "quoted_text": "def main(): pass",
        "confidence": "high",
    }
    state = _make_session_state(evidence=[ev_dict])

    prior = _collect_prior_evidence(state)
    assert len(prior) == 1
    assert prior[0].source_path == "/src/main.py"
    assert prior[0].confidence == "high"


# ═══════════════════════════════════════════════════════════════
# R11: 动态上限——facts > 6 时 findings limit 为 12
# ═══════════════════════════════════════════════════════════════


def test_r11_dynamic_findings_limit():
    from prismclaw.engine.subagent.core.contract import build_context_snapshot

    # 构造 10 个 facts
    facts = [f"fact_{i}" for i in range(10)]
    state = _make_session_state(facts=facts)

    # 构造最小化的 parent_ctx mock
    class MockTaskSpec:
        goal = "test"
        constraints = []
        execution_mode = "sync"
        risk_level = None

    class MockCtx:
        task_spec = MockTaskSpec()
        session_state = state
        system_prompt_sections = {}
        invoked_skill_state = None
        task_id = "t1"
        request_id = "r1"
        decision_ledger_tail = []

    snapshot = build_context_snapshot(task="test task", parent_ctx=MockCtx())
    # facts 有 10 个 > 6, 所以 limit 应该是 12
    # key_findings 应包含所有 10 个（在 limit 内）
    assert len(snapshot.key_findings) == 10


# ═══════════════════════════════════════════════════════════════
# R12: builder 成功执行, evidence 包含 bash
# ═══════════════════════════════════════════════════════════════


def test_r12_bash_evidence():
    from prismclaw.engine.subagent.core._lifecycle_mixin import _collect_evidence_from_messages

    messages = _make_bash_messages("python -c 'print(42)'", "42")
    evidence = _collect_evidence_from_messages(messages)
    assert len(evidence) == 1
    assert evidence[0].source_type == "tool_output"
    assert evidence[0].source_path == "python -c 'print(42)'"
    assert "42" in evidence[0].quoted_text


# ═══════════════════════════════════════════════════════════════
# R13: auditor 验证通过, confidence_assessment 为 high
# ═══════════════════════════════════════════════════════════════


def test_r13_auditor_confidence_high():
    envelope = SubagentResultEnvelope(
        summary="VERIFICATION: PASSED",
        findings=["[PASSED] 步骤1: 文件符合计划"],
        confidence_assessment="high",
    )
    assert envelope.confidence_assessment == "high"
    assert len(envelope.findings) == 1


# ═══════════════════════════════════════════════════════════════
# R14: _render_subagent_result_handover 输出包含 evidence 行
# ═══════════════════════════════════════════════════════════════


def test_r14_render_handover_includes_evidence():
    from prismclaw.engine.execution.output.tool_result_rendering import (
        _render_subagent_result_handover,
    )

    envelope = SubagentResultEnvelope(
        summary="test",
        findings=["[HIGH] fact1"],
        evidence=[
            EvidenceRef(
                source_type="file",
                source_path="/a.py",
                source_location="L10",
                quoted_text="hello",
                confidence="high",
            ),
        ],
        confidence_assessment="high",
    )
    rendered = _render_subagent_result_handover(envelope)
    assert "evidence:" in rendered
    assert "[high] file: /a.py (L10)" in rendered
    assert "confidence: high" in rendered


# ═══════════════════════════════════════════════════════════════
# R15: apply_update 去重——相同 source_path + quoted_text 不重复追加
# ═══════════════════════════════════════════════════════════════


def test_r15_apply_update_evidence_dedup():
    ev1 = {
        "source_type": "file",
        "source_path": "/a.py",
        "quoted_text": "hello",
        "confidence": "high",
    }
    ev2 = {
        "source_type": "file",
        "source_path": "/a.py",
        "quoted_text": "hello",
        "confidence": "high",
    }
    ev3 = {
        "source_type": "file",
        "source_path": "/b.py",
        "quoted_text": "world",
        "confidence": "medium",
    }

    state = _make_session_state(evidence=[ev1])
    update = SessionStateUpdate(evidence=[ev2, ev3])

    state.apply_update(update)
    # ev1 和 ev2 相同去重，ev3 新增
    assert len(state.evidence) == 2


# ═══════════════════════════════════════════════════════════════
# R16: normalize_state_payload 透传 evidence
# ═══════════════════════════════════════════════════════════════


def test_r16_normalize_state_payload_evidence():
    from prismclaw.engine.state.state_protocol import normalize_state_payload

    ev = {
        "source_type": "file",
        "source_path": "/a.py",
        "quoted_text": "hello",
        "confidence": "high",
    }
    state = _make_session_state(evidence=[ev])

    payload = normalize_state_payload(state)
    assert "evidence" in payload
    assert len(payload["evidence"]) == 1
    assert payload["evidence"][0]["source_path"] == "/a.py"


# ═══════════════════════════════════════════════════════════════
# R17: merge_state_payloads 去重合并 evidence
# ═══════════════════════════════════════════════════════════════


def test_r17_merge_state_payloads_evidence():
    from prismclaw.engine.state.state_protocol import merge_state_payloads

    ev1 = {
        "source_type": "file",
        "source_path": "/a.py",
        "quoted_text": "hello",
        "confidence": "high",
    }
    ev2 = {
        "source_type": "file",
        "source_path": "/a.py",
        "quoted_text": "hello",
        "confidence": "high",
    }
    ev3 = {
        "source_type": "web",
        "source_path": "https://x.com",
        "quoted_text": "info",
        "confidence": "medium",
    }

    p1 = {"evidence": [ev1]}
    p2 = {"evidence": [ev2, ev3]}

    merged = merge_state_payloads([p1, p2])
    assert len(merged["evidence"]) == 2


# ═══════════════════════════════════════════════════════════════
# R18: render_result_envelope_handover 包含 confidence 行
# ═══════════════════════════════════════════════════════════════


def test_r18_render_envelope_handover_confidence():
    from prismclaw.engine.subagent.core.contract import render_result_envelope_handover

    envelope = SubagentResultEnvelope(
        summary="test",
        findings=["finding1"],
        evidence=[
            EvidenceRef(source_type="file", source_path="/a.py", confidence="high"),
        ],
        confidence_assessment="medium",
    )
    rendered = render_result_envelope_handover(envelope)
    assert "evidence:" in rendered
    assert "confidence: medium" in rendered


# ═══════════════════════════════════════════════════════════════
# R19: 子代理规则归属固定段 <subagents>
# ═══════════════════════════════════════════════════════════════


def test_r19_delegation_section_contains_evidence_rules():
    from prismclaw.engine.execution.request._prompt_types import PromptInputs
    from prismclaw.engine.execution.request.system_prompt import delegation_section

    result = delegation_section(
        PromptInputs(
            session_id="test-session",
            workspace_id="test-ws",
            agent_id="test-agent",
            tools=[("sub_agent", "delegate")],
            subagent_registry_size=2,
        )
    )
    assert result is not None
    assert "证据" in result
    assert "主 Agent 直接比较各 Agent 的结果" in result
    assert "不机械采用全体最低置信度" in result
    assert "冲突" in result
    assert "subagent_control.send" in result
    assert "不重叠的修改范围" in result


def test_r19_handoff_advisory_is_protocol_only():
    """动态 handoff_advisory 只保留协议变体，不重复编排/证据规则。"""
    from prismclaw.engine.execution.request._prompt_advisory import handoff_advisory_section
    from prismclaw.engine.execution.request._prompt_types import PromptInputs

    inputs = PromptInputs(
        session_id="test-session",
        workspace_id="test-ws",
        agent_id="test-agent",
        subagent_registry_size=2,
        handoff_enabled=True,
        max_handoff_chain_length=5,
    )
    result = handoff_advisory_section(inputs)
    assert result is not None
    assert "[HANDOFF_REQUEST]" in result
    assert "置信度不得提升" not in result


def test_r19_domain_advisory_renders_registered_rules():
    """领域规则（如医疗免责声明）由 domain_advisory 独立承载。"""
    from prismclaw.engine.execution.request._disclaimer_registry import (
        DomainAdvisoryRule,
        register_advisory_rule,
        unregister_advisory_rules,
    )
    from prismclaw.engine.execution.request._prompt_advisory import domain_advisory_section

    register_advisory_rule(
        DomainAdvisoryRule(
            domain="test-domain",
            title="免责声明（强制）",
            body="必须附加免责声明。",
        )
    )
    try:
        result = domain_advisory_section()
    finally:
        unregister_advisory_rules("test-domain")
    assert result is not None
    assert "免责声明（强制）" in result
    assert "必须附加免责声明" in result


# ═══════════════════════════════════════════════════════════════
# R20: 完整链路——从 evidence 收集到 prior_evidence 传递的端到端
# ═══════════════════════════════════════════════════════════════


def test_r20_full_evidence_chain_e2e():
    """模拟完整链路:
    1. subagent 执行 → 收集 evidence
    2. 主 Agent 保留来源并综合各 Agent 结果
    3. 写入 SessionStateUpdate → apply_update
    4. 下一轮 build_context_snapshot → prior_evidence
    5. render_context_snapshot → XML
    6. render_task_envelope → JSON 格式引导
    """
    from prismclaw.engine.state.state_protocol import (
        _extract_structured_tool_result_sections,
        _merge_subagent_result_update,
    )
    from prismclaw.engine.subagent.core._lifecycle_mixin import _collect_evidence_from_messages
    from prismclaw.engine.subagent.core.contract import (
        build_context_snapshot,
        render_context_snapshot,
        render_result_envelope_handover,
        render_task_envelope,
    )

    # ---- Step 1: subagent 执行, 产生工具调用 ----
    messages = []
    messages.extend(
        _make_read_messages("/src/drug_interaction.py", "DrugA contraindicates DrugB", offset=100)
    )
    messages.extend(
        _make_grep_messages(
            "contraindication", "drug_interaction.py:100: DrugA contraindicates DrugB"
        )
    )
    evidence = _collect_evidence_from_messages(messages)
    assert len(evidence) == 2

    # ---- Step 2: 两个 subagent 并行, 聚合 ----
    env1 = SubagentResultEnvelope(
        summary="Found contraindication",
        findings=["[HIGH] DrugA contraindicates DrugB"],
        evidence=evidence,
        confidence_assessment="high",
    )
    env2 = SubagentResultEnvelope(
        summary="No interaction found for DrugC",
        findings=["[MEDIUM] DrugC appears safe"],
        evidence=[
            EvidenceRef(
                source_type="web",
                source_path="https://pharma.db/c",
                quoted_text="No known interactions",
                confidence="medium",
            ),
        ],
        confidence_assessment="medium",
    )

    # 主 Agent 综合时保留独立来源；这里不存在重复证据，无需改写。
    merged_evidence = env1.evidence + env2.evidence
    assert len(merged_evidence) == 3  # 2 from env1 + 1 from env2

    # 主 Agent 按来源质量作判断，而非由聚合器机械取最低值。
    confidence = "medium"

    # ---- Step 3: 写入 session state ----
    merged_envelope = SubagentResultEnvelope(
        summary="Combined findings",
        findings=env1.findings + env2.findings,
        evidence=merged_evidence,
        confidence_assessment=confidence,
    )

    # 序列化为 JSON（模拟 ToolResult 内容）
    envelope_json = merged_envelope.model_dump_json()

    # 解析
    sections = _extract_structured_tool_result_sections(envelope_json)
    assert sections is not None
    assert "evidence" in sections
    assert len(sections["evidence"]) == 3

    # 合并到 SessionStateUpdate
    update = SessionStateUpdate()
    _merge_subagent_result_update(update, sections, source_ref=None)
    assert len(update.evidence) == 3
    assert len(update.facts) == 2  # findings → facts

    # apply_update
    state = _make_session_state()
    state.apply_update(update)
    assert len(state.evidence) == 3

    # ---- Step 4: 下一轮 build_context_snapshot ----
    class MockTaskSpec:
        goal = "Check drug interactions"
        constraints = []
        execution_mode = "sync"
        risk_level = None

    class MockCtx:
        task_spec = MockTaskSpec()
        session_state = state
        system_prompt_sections = {}
        invoked_skill_state = None
        task_id = "t1"
        request_id = "r1"
        decision_ledger_tail = []

    snapshot = build_context_snapshot(task="Check DrugA-DrugB interaction", parent_ctx=MockCtx())
    assert len(snapshot.prior_evidence) == 3
    assert snapshot.prior_evidence[0].source_type == "file"
    assert snapshot.prior_evidence[0].source_path == "/src/drug_interaction.py"

    # ---- Step 5: render_context_snapshot ----
    rendered = render_context_snapshot(snapshot)
    assert "<prior_evidence>" in rendered
    assert "[high] file: /src/drug_interaction.py" in rendered
    assert "</prior_evidence>" in rendered

    # ---- Step 6: render_task_envelope 包含 confidence_assessment ----
    task_text = render_task_envelope("Check DrugA-DrugB interaction", snapshot)
    assert "confidence_assessment" in task_text

    # ---- 额外验证: render_result_envelope_handover ----
    handover = render_result_envelope_handover(merged_envelope)
    assert "evidence:" in handover
    assert "confidence: medium" in handover


# ═══════════════════════════════════════════════════════════════
# 边界条件补充测试
# ═══════════════════════════════════════════════════════════════


def test_evidence_ref_default_confidence_is_high():
    """EvidenceRef 默认 confidence 为 high（因为来自工具输出，非 LLM 记忆）。"""
    ev = EvidenceRef(source_type="file", source_path="/a.py", quoted_text="text")
    assert ev.confidence == "high"


def test_evidence_ref_empty_quoted_text():
    """工具调用无结果时 quoted_text 为空字符串，不应崩溃。"""
    ev = EvidenceRef(source_type="file", source_path="/a.py", quoted_text="")
    assert ev.quoted_text == ""


def test_collect_evidence_skips_unknown_tools():
    """不在 _TOOL_EVIDENCE_MAP 中的工具不应生成 evidence。"""
    from prismclaw.engine.subagent.core._lifecycle_mixin import _collect_evidence_from_messages

    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tc_001",
                    "type": "function",
                    "function": {
                        "name": "unknown_tool",
                        "arguments": json.dumps({"arg": "val"}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "tc_001",
            "content": "result",
        },
    ]
    evidence = _collect_evidence_from_messages(messages)
    assert len(evidence) == 0


def test_collect_evidence_handles_list_content():
    """tool result content 为 list 格式时不崩溃。"""
    from prismclaw.engine.subagent.core._lifecycle_mixin import _collect_evidence_from_messages

    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tc_001",
                    "type": "function",
                    "function": {
                        "name": "read",
                        "arguments": json.dumps({"file_path": "/a.py"}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "tc_001",
            "content": [{"type": "text", "text": "file content here"}],
        },
    ]
    evidence = _collect_evidence_from_messages(messages)
    assert len(evidence) == 1
    assert evidence[0].quoted_text == "file content here"


def test_read_evidence_includes_line_location():
    """read 工具带 offset 时应提取行号。"""
    from prismclaw.engine.subagent.core._lifecycle_mixin import _collect_evidence_from_messages

    messages = _make_read_messages("/a.py", "content", offset=42)
    evidence = _collect_evidence_from_messages(messages)
    assert len(evidence) == 1
    assert evidence[0].source_location == "L42-L92"


def test_quoted_text_truncated_at_2000():
    """quoted_text 超过 2000 字符时截断。"""
    from prismclaw.engine.subagent.core._lifecycle_mixin import _collect_evidence_from_messages

    long_content = "x" * 3000
    messages = _make_read_messages("/big.py", long_content)
    evidence = _collect_evidence_from_messages(messages)
    assert len(evidence[0].quoted_text) == 2000


def test_empty_confidence_is_not_rewritten_by_result_projection():
    from prismclaw.engine.subagent.tool import SubAgentTool
    from prismclaw.schema.content.content_types import (
        SubagentEnvelopeArtifact,
        TextBlock,
        ToolResult,
    )

    envelope = SubagentResultEnvelope(summary="unknown", confidence_assessment="")
    tool_result = ToolResult(
        tool_call_id="call-empty-confidence",
        content=[TextBlock(text=envelope.model_dump_json())],
        subagent_envelope=SubagentEnvelopeArtifact(result_envelope=envelope),
    )
    item = SubAgentTool._result_item(
        {"agent_name": "explorer", "outcome": "success", "tool_result": tool_result}
    )
    assert item.envelope is not None
    assert item.envelope.confidence_assessment == ""


def test_collect_prior_evidence_handles_invalid_dict():
    """无效 dict 不应导致崩溃。"""
    from prismclaw.engine.subagent.core.contract import _collect_prior_evidence

    state = _make_session_state(
        evidence=[
            {"invalid_key": "no source_type"},
            {
                "source_type": "file",
                "source_path": "/a.py",
                "quoted_text": "ok",
                "confidence": "high",
            },
        ]
    )
    prior = _collect_prior_evidence(state)
    # 第一个 dict 缺少必要字段但 EvidenceRef 有默认值，不会崩溃
    assert len(prior) == 2


def test_apply_update_evidence_empty_update():
    """空 update 不影响现有 evidence。"""
    ev = {
        "source_type": "file",
        "source_path": "/a.py",
        "quoted_text": "hello",
        "confidence": "high",
    }
    state = _make_session_state(evidence=[ev])
    update = SessionStateUpdate()  # evidence 为空列表
    state.apply_update(update)
    assert len(state.evidence) == 1


def test_has_content_includes_evidence():
    """has_content() 应在有 evidence 时返回 True。"""
    state = _make_session_state(
        evidence=[
            {
                "source_type": "file",
                "source_path": "/a.py",
                "quoted_text": "hello",
                "confidence": "high",
            },
        ]
    )
    assert state.has_content() is True


def test_session_state_without_evidence_has_content_false():
    """无 evidence 且无其他内容时 has_content 为 False。"""
    state = _make_session_state()
    assert state.has_content() is False
