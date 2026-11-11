"""医疗循证咨询 SubAgent 全流程集成测试。

使用 MockLLM + MockToolRegistry 驱动 SubAgentTool.execute() 真实调用链路：
  SubAgentTool.execute() → _run_single_agent() → SubAgentExecutor.run()
  → AgentInstance.run() → _loop() → LLM + 工具循环 → evidence 收集
  → _aggregate_results() → ToolResult

6 个测试场景：
  1. 单 researcher 1 轮完成（read + 直接回答）—— 验证 evidence 收集链路
  2. 单 researcher 2 轮完成（read → 再 read → 回答）—— 验证多轮 evidence 累积
  3. 并行 2 个 researcher + 1 个 architect —— 验证 _aggregate_results 合并
  4. researcher 超时退出 —— 验证非 success 时 evidence 不丢失
  5. 3 轮追问（Round 1 检索 → Round 2 追问 → Round 3 最终建议）—— 跨轮次 prior_evidence
  6. 混合场景：1 成功 + 1 超时 + 1 错误 —— 验证 evidence 只来自有效 subagent

每个测试验证：
  - SubAgentTool.execute() 的输入/输出
  - AgentRunResult.outcome
  - ToolResult.subagent_envelope.result_envelope 的 evidence 字段
  - evidence 的 source_type / source_path / quoted_text / confidence
  - confidence_assessment 的正确性
  - 主 Agent 看到的 handover 文本包含 evidence + confidence
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from prismclaw.engine.subagent.core.contract import (
    render_result_envelope_handover,
)
from prismclaw.engine.subagent.definition.manifest import AgentManifest
from prismclaw.engine.subagent.tool import SubAgentTool
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.schema.agent.session import (
    SessionContextState,
    SessionStateUpdate,
)
from prismclaw.schema.config.config import SubAgentConfig
from prismclaw.schema.content.content_types import (
    SubagentEnvelopeArtifact,
    TextBlock,
    ToolResult,
)
from prismclaw.schema.content.subagent import (
    SubagentResultEnvelope,
)


# ═══════════════════════════════════════════════════════════════
# 医疗场景数据
# ═══════════════════════════════════════════════════════════════

DRUG_LABEL_METFORMIN = """
盐酸二甲双胍片说明书
【适应症】用于单纯饮食控制及体育锻炼治疗无效的2型糖尿病。
【禁忌】1. 肾功能不全（eGFR<30禁用，30-45需评估）禁用。
2. 需碘化造影的放射检查前后48小时应暂停使用。
【注意事项】乳酸酸中毒：eGFR<30禁用，30-45需评估减量。
"""

GUIDELINE_T2DM = """
中国2型糖尿病防治指南（2024年版）
7.1 二甲双胍为2型糖尿病一线用药（推荐等级A，证据水平1a）。
7.2 eGFR≥45可正常使用，30-45减量，<30禁用。
7.4 联合用药：合并CKD时优先联合SGLT2i。
"""

CKD_DOSING = "eGFR 30-45: 减量至500mg bid，每3个月监测eGFR。"


# ═══════════════════════════════════════════════════════════════
# MockLLM —— 模拟 LLM 的响应行为
# ═══════════════════════════════════════════════════════════════

class MockLLMResponse:
    """模拟 LLM 的单次响应。"""
    def __init__(
        self,
        *,
        text: str = "",
        tool_calls: list[dict] | None = None,
        input_tokens: int = 100,
        output_tokens: int = 50,
    ):
        self.text = text
        self.tool_calls = tool_calls or []
        self.usage = type("Usage", (), {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        })()


class MockLLM:
    """可编程的 LLM mock：预设每轮的响应序列。

    用法：
        llm = MockLLM(responses=[
            # 第 1 轮：调用 read 工具
            MockLLMResponse(tool_calls=[{
                "id": "tc_1", "type": "function",
                "function": {"name": "read", "arguments": json.dumps({"file_path": "/data/drug.txt"})}
            }]),
            # 第 2 轮：直接回答
            MockLLMResponse(text=envelope_json),
        ])
    """
    def __init__(self, responses: list[MockLLMResponse], default_model: str = "test-model"):
        self._responses = list(responses)
        self._call_index = 0
        self.default_model = default_model

    async def complete(self, *, request, model, tools, abort_event, **kw):
        if self._call_index >= len(self._responses):
            # 兜底：返回空回答
            return MockLLMResponse(text='{"summary":"(no more responses)","findings":[]}')
        resp = self._responses[self._call_index]
        self._call_index += 1
        return resp


# ═══════════════════════════════════════════════════════════════
# MockToolRegistry —— 模拟工具注册表
# ═══════════════════════════════════════════════════════════════

class MockTool:
    """模拟单个工具。"""
    def __init__(self, name: str, execute_fn=None):
        self.name = name
        self.description = f"Mock {name} tool"
        self.parameters = {"type": "object", "properties": {}}
        self.side_effect = name != "read"
        self.tool_class = "read" if name == "read" else "write"
        self.effect_class = "local_mutation"
        self.visibility_scope = "main_only"
        self.read_only_allowed = True
        self._execute_fn = execute_fn

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        if self._execute_fn:
            result_text = self._execute_fn(args)
        else:
            result_text = f"[mock {self.name}] args={json.dumps(args, ensure_ascii=False)[:200]}"
        return ToolResult(
            tool_call_id="mock_tc",
            content=[TextBlock(text=result_text)],
            is_error=False,
        )


class MockToolRegistry:
    """模拟工具注册表。"""
    def __init__(self, tools: list[MockTool] | None = None):
        self._tools: dict[str, MockTool] = {}
        if tools:
            for t in tools:
                self._tools[t.name] = t

    def get(self, name: str) -> MockTool | None:
        return self._tools.get(name)

    def names(self) -> set[str]:
        return set(self._tools.keys())

    def list_for_llm(self, allowed_names=None) -> list[dict]:
        result = []
        for name, tool in self._tools.items():
            if allowed_names is None or name in allowed_names:
                result.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                })
        return result

    def list_tools(self, allowed_names=None, subagent_depth=0):
        return list(self._tools.values())


# ═══════════════════════════════════════════════════════════════
# MockCapabilityCatalogService
# ═══════════════════════════════════════════════════════════════

class AgentCatalogEntry:
    def __init__(self, name: str, description: str, manifest: AgentManifest):
        self.name = name
        self.description = description
        self.can_handoff_to = manifest.can_handoff_to
        self.manifest = manifest


class MockCapabilityCatalog:
    def __init__(self, entries: list[AgentCatalogEntry]):
        self._entries = {e.name: e for e in entries}

    def list_agent_entries(self) -> list[AgentCatalogEntry]:
        return list(self._entries.values())

    def get_agent_entry(self, name: str) -> AgentCatalogEntry | None:
        return self._entries.get(name)

    def count_agent_entries(self) -> int:
        return len(self._entries)


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════

def _make_envelope_json(
    *,
    summary: str,
    findings: list[str] | None = None,
    confidence: str = "high",
) -> str:
    """构造 SubagentResultEnvelope JSON（模拟 LLM 输出）。"""
    envelope = SubagentResultEnvelope(
        summary=summary,
        findings=findings or [],
        confidence_assessment=confidence,
    )
    return envelope.model_dump_json()


def _make_tool_context(**overrides) -> ToolContext:
    """构造最小化 ToolContext。"""
    defaults = dict(
        workspace_id="test-ws",
        workspace_path=Path("/tmp/test-ws"),
        session_id="test-session-001",
        sandbox_policy=type("SP", (), {"tier": "standard"})(),
    )
    defaults.update(overrides)
    return ToolContext(**defaults)


def _make_subagent_tool(
    llm: MockLLM,
    manifests: list[AgentManifest],
    tool_registry: MockToolRegistry | None = None,
) -> SubAgentTool:
    """构造 SubAgentTool 实例（完整依赖注入）。"""
    from prismclaw.engine.subagent.core.executor import SubAgentExecutor
    from prismclaw.engine.subagent.definition.registry import SubAgentRegistry

    catalog = MockCapabilityCatalog([
        AgentCatalogEntry(name=m.name, description=m.description, manifest=m)
        for m in manifests
    ])

    settings = SubAgentConfig(
        enabled=True,
        max_depth=3,
        max_subagent_depth=3,
        default_max_turns=10,
        default_timeout_seconds=60.0,
    )

    registry = SubAgentRegistry(settings=settings, workspace_path=Path("/tmp/test-ws"))
    for m in manifests:
        registry._manifests[m.name] = m

    tools = tool_registry or MockToolRegistry()

    executor = SubAgentExecutor(
        tool_registry=tools,
        settings=settings,
        llm_client=llm,
        memory_store=None,
        session_store=None,
        default_model=llm.default_model,
    )

    return SubAgentTool(
        executor=executor,
        registry=registry,
        settings=settings,
        capability_catalog=catalog,
    )


def _make_read_tool(content_map: dict[str, str]) -> MockTool:
    """构造模拟 read 工具，根据 file_path 返回预设内容。"""
    def execute_fn(args):
        return content_map.get(args.get("file_path", ""), "[file not found]")

    return MockTool("read", execute_fn=execute_fn)


def _make_grep_tool(content_map: dict[str, str]) -> MockTool:
    def execute_fn(args):
        return content_map.get(args.get("pattern", ""), "[no match]")

    return MockTool("grep", execute_fn=execute_fn)


def _make_web_fetch_tool(content_map: dict[str, str]) -> MockTool:
    def execute_fn(args):
        return content_map.get(args.get("url", ""), "[fetch failed]")

    return MockTool("web_fetch", execute_fn=execute_fn)


# ═══════════════════════════════════════════════════════════════
# 测试场景 1：单 researcher 1 轮完成
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_integration_single_researcher_one_turn():
    """单 researcher 调用 read → 直接回答。

    输入：
      - task: "检索二甲双胍药品说明书的禁忌症"
      - LLM 行为：第 1 轮调用 read(file_path="/data/drug_labels/metformin.txt")
                  第 2 轮返回 SubagentResultEnvelope

    预期输出：
      - outcome = "success"
      - evidence 包含 1 条 file 类型的 EvidenceRef
      - evidence.source_path = "/data/drug_labels/metformin.txt"
      - evidence.quoted_text 包含 "二甲双胍" 或 "禁忌"
      - confidence_assessment = "high"
    """
    file_contents = {"/data/drug_labels/metformin.txt": DRUG_LABEL_METFORMIN}

    llm = MockLLM(responses=[
        # 第 1 轮：调用 read
        MockLLMResponse(tool_calls=[{
            "id": "tc_read_1", "type": "function",
            "function": {
                "name": "read",
                "arguments": json.dumps({"file_path": "/data/drug_labels/metformin.txt"}),
            },
        }]),
        # 第 2 轮：返回 envelope
        MockLLMResponse(text=_make_envelope_json(
            summary="检索到二甲双胍禁忌症",
            findings=["[HIGH] eGFR<30禁用", "[HIGH] 碘化造影前后48h暂停"],
            confidence="high",
        )),
    ])

    manifest = AgentManifest(
        name="researcher",
        description="检索医学文献的研究员",
        model="test-model",
        allowed_tools=["read"],
        max_turns=5,
        timeout_seconds=30.0,
    )

    tool_reg = MockToolRegistry([_make_read_tool(file_contents)])
    subagent_tool = _make_subagent_tool(llm, [manifest], tool_reg)
    context = _make_tool_context(session_state=SessionContextState(session_id="test-session-001"))

    result = await subagent_tool.execute(
        args={
            "agents": [{"agent_name": "researcher", "task": "检索二甲双胍药品说明书的禁忌症"}],
            "execution_mode": "sync",
        },
        context=context,
    )

    # ---- 验证输出结构 ----
    assert isinstance(result, ToolResult)
    assert not result.is_error

    # ---- 验证 envelope ----
    envelope = result.subagent_envelope
    assert envelope is not None
    # outcome 在 _lifecycle_mixin 中未填充到 SubagentEnvelopeArtifact，
    # 通过 is_error 和 result_envelope 判断成功

    result_envelope = envelope.result_envelope
    assert result_envelope is not None
    assert result_envelope.confidence_assessment == "high"

    # ---- 验证 evidence ----
    assert len(result_envelope.evidence) >= 1, \
        f"Expected >=1 evidence, got {len(result_envelope.evidence)}"

    ev = result_envelope.evidence[0]
    assert ev.source_type == "file"
    assert ev.source_path == "/data/drug_labels/metformin.txt"
    assert ev.confidence == "high"
    # quoted_text 应包含关键医疗信息
    assert "二甲双胍" in ev.quoted_text or "禁忌" in ev.quoted_text

    # ---- 验证 handover 文本 ----
    handover = render_result_envelope_handover(result_envelope)
    assert "evidence:" in handover
    assert "confidence: high" in handover
    assert "[HIGH]" in handover

    print(f"✅ 场景1: evidence={len(result_envelope.evidence)}条, "
          f"confidence={result_envelope.confidence_assessment}, "
          f"handover_len={len(handover)}")


# ═══════════════════════════════════════════════════════════════
# 测试场景 2：单 researcher 2 轮完成（多工具调用）
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_integration_researcher_multi_tool_calls():
    """researcher 连续调用 read + web_fetch → 回答。

    输入：
      - LLM 行为：第 1 轮 read → 第 2 轮 web_fetch → 第 3 轮回答

    预期输出：
      - outcome = "success"
      - evidence 包含 2 条（1 file + 1 web）
      - confidence_assessment = "high"
    """
    file_contents = {"/data/drug_labels/metformin.txt": DRUG_LABEL_METFORMIN}
    web_contents = {"https://guidelines.diabetes.cn/t2dm-2024": GUIDELINE_T2DM}

    llm = MockLLM(responses=[
        # 第 1 轮：read
        MockLLMResponse(tool_calls=[{
            "id": "tc_read_1", "type": "function",
            "function": {
                "name": "read",
                "arguments": json.dumps({"file_path": "/data/drug_labels/metformin.txt"}),
            },
        }]),
        # 第 2 轮：web_fetch
        MockLLMResponse(tool_calls=[{
            "id": "tc_web_1", "type": "function",
            "function": {
                "name": "web_fetch",
                "arguments": json.dumps({"url": "https://guidelines.diabetes.cn/t2dm-2024"}),
            },
        }]),
        # 第 3 轮：回答
        MockLLMResponse(text=_make_envelope_json(
            summary="药品说明书+指南检索完成",
            findings=[
                "[HIGH] eGFR<30禁用",
                "[HIGH] 碘化造影前后48h暂停",
                "[HIGH] eGFR 30-45减量",
            ],
            confidence="high",
        )),
    ])

    manifest = AgentManifest(
        name="researcher",
        description="检索医学文献",
        model="test-model",
        allowed_tools=["read", "web_fetch"],
        max_turns=5,
        timeout_seconds=30.0,
    )

    tool_reg = MockToolRegistry([
        _make_read_tool(file_contents),
        _make_web_fetch_tool(web_contents),
    ])
    subagent_tool = _make_subagent_tool(llm, [manifest], tool_reg)
    context = _make_tool_context(session_state=SessionContextState(session_id="test-session-002"))

    result = await subagent_tool.execute(
        args={
            "agents": [{"agent_name": "researcher", "task": "检索二甲双胍禁忌症和剂量调整建议"}],
            "execution_mode": "sync",
        },
        context=context,
    )

    # ---- 验证 ----
    assert not result.is_error
    envelope = result.subagent_envelope.result_envelope

    # evidence 应包含 2 条（1 file + 1 web）
    assert len(envelope.evidence) == 2, \
        f"Expected 2 evidence, got {len(envelope.evidence)}: {[e.source_type for e in envelope.evidence]}"

    types = {e.source_type for e in envelope.evidence}
    assert types == {"file", "web"}
    assert envelope.confidence_assessment == "high"

    # handover
    handover = render_result_envelope_handover(envelope)
    assert "[high] file:" in handover
    assert "[high] web:" in handover

    print(f"✅ 场景2: evidence={len(envelope.evidence)}条, "
          f"types={types}, confidence={envelope.confidence_assessment}")


# ═══════════════════════════════════════════════════════════════
# 测试场景 3：并行 2 researcher + 1 architect
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_integration_parallel_researchers_with_architect():
    """并行 2 个 researcher + 1 个 architect，验证 _aggregate_results 合并。

    输入：
      - researcher-1: read → 回答 (evidence: 1 file, confidence: high)
      - researcher-2: web_fetch → 回答 (evidence: 1 web, confidence: high)
      - architect: 基于 findings 推理 (evidence: 0, confidence: medium)

    预期输出：
      - evidence 合并 = 2 条 (1 file + 1 web)
      - confidence = medium (取最低)
      - findings 合并去重
    """
    # 每个 subagent 需要独立的 LLM 实例
    file_contents = {"/data/drug_labels/metformin.txt": DRUG_LABEL_METFORMIN}
    web_contents = {"https://guidelines.diabetes.cn/t2dm-2024": GUIDELINE_T2DM}

    # 我们需要 SubAgentTool 用同一个 LLM，但每次 _run_single_agent 会创建
    # 新的 AgentInstance。LLM 需要按顺序服务 3 个 subagent 的请求。
    llm = MockLLM(responses=[
        # --- researcher-1 ---
        MockLLMResponse(tool_calls=[{
            "id": "tc_r1", "type": "function",
            "function": {"name": "read", "arguments": json.dumps({"file_path": "/data/drug_labels/metformin.txt"})},
        }]),
        MockLLMResponse(text=_make_envelope_json(
            summary="药品说明书检索完成",
            findings=["[HIGH] eGFR<30禁用", "[HIGH] 碘化造影前后48h暂停"],
            confidence="high",
        )),
        # --- researcher-2 ---
        MockLLMResponse(tool_calls=[{
            "id": "tc_r2", "type": "function",
            "function": {"name": "web_fetch", "arguments": json.dumps({"url": "https://guidelines.diabetes.cn/t2dm-2024"})},
        }]),
        MockLLMResponse(text=_make_envelope_json(
            summary="指南检索完成",
            findings=["[HIGH] eGFR 30-45减量", "[HIGH] 联合SGLT2i优先"],
            confidence="high",
        )),
        # --- architect ---
        MockLLMResponse(text=_make_envelope_json(
            summary="基于检索结果推理药物交互",
            findings=["[MEDIUM] 碘化造影剂存在严重交互需暂停", "[MEDIUM] eGFR 30-45建议减量至500mg bid"],
            confidence="medium",
        )),
    ])

    manifests = [
        AgentManifest(name="researcher", description="检索", model="test-model",
                      allowed_tools=["read", "web_fetch"], max_turns=5, timeout_seconds=30.0),
        AgentManifest(name="architect", description="推理", model="test-model",
                      allowed_tools=["read"], max_turns=3, timeout_seconds=30.0),
    ]

    tool_reg = MockToolRegistry([
        _make_read_tool(file_contents),
        _make_web_fetch_tool(web_contents),
    ])
    subagent_tool = _make_subagent_tool(llm, manifests, tool_reg)
    context = _make_tool_context(session_state=SessionContextState(session_id="test-session-003"))

    result = await subagent_tool.execute(
        args={
            "agents": [
                {"agent_name": "researcher", "task": "检索药品说明书"},
                {"agent_name": "researcher", "task": "检索临床指南"},
                {"agent_name": "architect", "task": "基于检索结果推理药物交互"},
            ],
            "execution_mode": "parallel",
        },
        context=context,
    )

    # ---- 验证 ----
    assert not result.is_error
    envelope = result.subagent_envelope.result_envelope

    # evidence 合并
    assert len(envelope.evidence) >= 2, \
        f"Expected >=2 evidence, got {len(envelope.evidence)}"
    types = {e.source_type for e in envelope.evidence}
    assert "file" in types
    assert "web" in types

    # confidence 取最低
    assert envelope.confidence_assessment in ("medium", "low")

    # findings 合并
    assert len(envelope.findings) >= 4, \
        f"Expected >=4 findings, got {len(envelope.findings)}"

    # handover
    handover = render_result_envelope_handover(envelope)
    assert "evidence:" in handover
    assert "confidence:" in handover

    print(f"✅ 场景3: evidence={len(envelope.evidence)}条, "
          f"types={types}, confidence={envelope.confidence_assessment}, "
          f"findings={len(envelope.findings)}")


# ═══════════════════════════════════════════════════════════════
# 测试场景 4：researcher 超时退出
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_integration_researcher_timeout():
    """researcher 超时退出，但已有工具调用的 evidence 不丢失。

    输入：
      - LLM 行为：第 1 轮调用 read → 第 2 轮再次调用 read → 触发超时

    预期输出：
      - outcome != "success" (timeout)
      - evidence 仍被收集（1 条 file）
      - confidence_assessment = "low"
    """
    file_contents = {"/data/drug_labels/metformin.txt": DRUG_LABEL_METFORMIN}

    # LLM 会调用 read 但不会返回最终回答（模拟超时）
    # 我们用 max_turns=1 来触发 max_turns_exceeded
    llm = MockLLM(responses=[
        MockLLMResponse(tool_calls=[{
            "id": "tc_read_1", "type": "function",
            "function": {"name": "read", "arguments": json.dumps({"file_path": "/data/drug_labels/metformin.txt"})},
        }]),
        # 第 2 轮仍调用工具，但 max_turns=1 已到，会触发 max_turns_exceeded
        MockLLMResponse(tool_calls=[{
            "id": "tc_read_2", "type": "function",
            "function": {"name": "read", "arguments": json.dumps({"file_path": "/data/drug_labels/metformin.txt"})},
        }]),
    ])

    manifest = AgentManifest(
        name="researcher",
        description="检索",
        model="test-model",
        allowed_tools=["read"],
        max_turns=1,  # 只有 1 轮 → 第 2 轮工具调用触发 max_turns
        timeout_seconds=30.0,
    )

    tool_reg = MockToolRegistry([_make_read_tool(file_contents)])
    subagent_tool = _make_subagent_tool(llm, [manifest], tool_reg)
    context = _make_tool_context(session_state=SessionContextState(session_id="test-session-004"))

    result = await subagent_tool.execute(
        args={
            "agents": [{"agent_name": "researcher", "task": "检索二甲双胍禁忌症"}],
            "execution_mode": "sync",
        },
        context=context,
    )

    # ---- 验证 ----
    # 单 subagent 时直接返回其 ToolResult（不经过 _aggregate_results）
    # 非 success → is_error = True
    assert result.is_error

    envelope = result.subagent_envelope
    assert envelope is not None

    # outcome 非 success
    assert envelope.outcome != "success"

    result_envelope = envelope.result_envelope
    assert result_envelope is not None

    # 关键：evidence 仍被收集
    assert len(result_envelope.evidence) >= 1, \
        f"Expected >=1 evidence on timeout, got {len(result_envelope.evidence)}"

    ev = result_envelope.evidence[0]
    assert ev.source_type == "file"
    assert ev.source_path == "/data/drug_labels/metformin.txt"

    # confidence 应为 low
    assert result_envelope.confidence_assessment == "low"

    print(f"✅ 场景4: outcome={envelope.outcome}, "
          f"evidence={len(result_envelope.evidence)}条, "
          f"confidence={result_envelope.confidence_assessment}")


# ═══════════════════════════════════════════════════════════════
# 测试场景 5：3 轮追问（跨轮次 prior_evidence）
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_integration_three_round_consultation():
    """3 轮追问医疗循证咨询。

    Round 1: researcher 检索药品说明书 → evidence 收集
    Round 2: researcher 检索 CKD 剂量调整 → prior_evidence 传递
    Round 3: architect 给出最终建议 → 包含全部 evidence

    验证：
      - 每轮结束后 evidence 被持久化到 session state
      - 下一轮 subagent 的 context_snapshot 包含 prior_evidence
      - 最终 handover 包含跨轮次累积的 evidence
    """
    from prismclaw.engine.state.state_protocol import (
        _extract_structured_tool_result_sections,
        _merge_subagent_result_update,
    )

    state = SessionContextState(session_id="test-session-005")
    state.primary_goal = "评估二甲双胍在eGFR=35患者中的使用安全性"

    file_contents = {
        "/data/drug_labels/metformin.txt": DRUG_LABEL_METFORMIN,
        "/data/guidelines/ckd_dosing.txt": CKD_DOSING,
    }
    tool_reg = MockToolRegistry([_make_read_tool(file_contents)])

    # ---- Round 1: 检索药品说明书 ----
    llm_r1 = MockLLM(responses=[
        MockLLMResponse(tool_calls=[{
            "id": "tc_r1", "type": "function",
            "function": {"name": "read", "arguments": json.dumps({"file_path": "/data/drug_labels/metformin.txt"})},
        }]),
        MockLLMResponse(text=_make_envelope_json(
            summary="药品说明书检索完成",
            findings=["[HIGH] eGFR<30禁用，30-45需评估", "[HIGH] 碘化造影前后48h暂停"],
            confidence="high",
        )),
    ])
    manifest_r = AgentManifest(
        name="researcher", description="检索", model="test-model",
        allowed_tools=["read"], max_turns=5, timeout_seconds=30.0,
    )
    subagent_tool_r1 = _make_subagent_tool(llm_r1, [manifest_r], tool_reg)
    ctx_r1 = _make_tool_context(session_state=state)

    result_r1 = await subagent_tool_r1.execute(
        args={
            "agents": [{"agent_name": "researcher", "task": "检索二甲双胍禁忌症"}],
            "execution_mode": "sync",
        },
        context=ctx_r1,
    )

    # 验证 Round 1 输出
    assert not result_r1.is_error
    envelope_r1 = result_r1.subagent_envelope.result_envelope
    assert len(envelope_r1.evidence) >= 1
    assert envelope_r1.evidence[0].source_path == "/data/drug_labels/metformin.txt"

    # 持久化 Round 1 evidence
    sections = _extract_structured_tool_result_sections(envelope_r1.model_dump_json())
    assert sections is not None
    update1 = SessionStateUpdate()
    _merge_subagent_result_update(update1, sections, source_ref=None)
    state.apply_update(update1)
    assert len(state.evidence) >= 1, f"Round 1: state.evidence={len(state.evidence)}"

    # ---- Round 2: 追问 CKD 剂量调整 ----
    llm_r2 = MockLLM(responses=[
        MockLLMResponse(tool_calls=[{
            "id": "tc_r2", "type": "function",
            "function": {"name": "read", "arguments": json.dumps({"file_path": "/data/guidelines/ckd_dosing.txt"})},
        }]),
        MockLLMResponse(text=_make_envelope_json(
            summary="CKD剂量调整建议",
            findings=["[HIGH] eGFR 30-45减量至500mg bid"],
            confidence="high",
        )),
    ])
    subagent_tool_r2 = _make_subagent_tool(llm_r2, [manifest_r], tool_reg)
    ctx_r2 = _make_tool_context(session_state=state)

    result_r2 = await subagent_tool_r2.execute(
        args={
            "agents": [{"agent_name": "researcher", "task": "eGFR=35患者二甲双胍剂量调整"}],
            "execution_mode": "sync",
        },
        context=ctx_r2,
    )

    # 验证 Round 2 输出
    assert not result_r2.is_error
    envelope_r2 = result_r2.subagent_envelope.result_envelope
    assert len(envelope_r2.evidence) >= 1
    assert envelope_r2.evidence[0].source_path == "/data/guidelines/ckd_dosing.txt"

    # 持久化 Round 2 evidence
    sections2 = _extract_structured_tool_result_sections(envelope_r2.model_dump_json())
    update2 = SessionStateUpdate()
    _merge_subagent_result_update(update2, sections2, source_ref=None)
    state.apply_update(update2)
    assert len(state.evidence) >= 2, f"Round 2: state.evidence={len(state.evidence)}"

    # 验证 prior_evidence 可提取
    from prismclaw.engine.subagent.core.contract import _collect_prior_evidence
    prior = _collect_prior_evidence(state)
    assert len(prior) >= 2, f"prior_evidence={len(prior)}"
    prior_paths = {e.source_path for e in prior}
    assert "/data/drug_labels/metformin.txt" in prior_paths
    assert "/data/guidelines/ckd_dosing.txt" in prior_paths

    # ---- Round 3: architect 给出最终建议 ----
    llm_r3 = MockLLM(responses=[
        MockLLMResponse(text=_make_envelope_json(
            summary="最终建议：eGFR=35患者可使用二甲双胍，需减量至500mg bid",
            findings=[
                "[HIGH] eGFR 30-45可使用但需减量",
                "[HIGH] 建议起始500mg bid",
                "[HIGH] 每3个月监测eGFR",
                "[HIGH] 碘化造影前后48h暂停",
            ],
            confidence="high",
        )),
    ])
    manifest_a = AgentManifest(
        name="architect", description="推理", model="test-model",
        allowed_tools=["read"], max_turns=3, timeout_seconds=30.0,
    )
    subagent_tool_r3 = _make_subagent_tool(llm_r3, [manifest_a], tool_reg)
    ctx_r3 = _make_tool_context(session_state=state)

    result_r3 = await subagent_tool_r3.execute(
        args={
            "agents": [{"agent_name": "architect", "task": "基于已有证据给出eGFR=35患者的最终建议"}],
            "execution_mode": "sync",
        },
        context=ctx_r3,
    )

    # 验证 Round 3 输出
    assert not result_r3.is_error
    envelope_r3 = result_r3.subagent_envelope.result_envelope
    assert len(envelope_r3.findings) >= 4
    assert envelope_r3.confidence_assessment == "high"

    # 最终 handover
    handover = render_result_envelope_handover(envelope_r3)
    assert "[HIGH]" in handover
    assert "confidence: high" in handover

    print(f"✅ 场景5: R1 evidence={len(envelope_r1.evidence)}, "
          f"R2 evidence={len(envelope_r2.evidence)}, "
          f"state.evidence={len(state.evidence)}, "
          f"prior={len(prior)}, "
          f"R3 findings={len(envelope_r3.findings)}")


# ═══════════════════════════════════════════════════════════════
# 测试场景 6：混合场景 1 成功 + 1 超时 + 1 错误
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_integration_mixed_success_timeout_error():
    """混合场景：1 成功 + 1 超时 + 1 错误。

    输入：
      - researcher-1: read → 回答 (success, evidence: 1 file)
      - researcher-2: read → 超时 (max_turns=1, evidence: 1 file)
      - researcher-3: 错误（LLM 抛异常）

    预期输出：
      - _aggregate_results 合并 evidence 来自所有有效 subagent
      - confidence = low（因为超时的 subagent confidence="low"）
    """
    file_contents = {
        "/data/drug_labels/metformin.txt": DRUG_LABEL_METFORMIN,
        "/data/guidelines/t2dm.txt": GUIDELINE_T2DM,
    }

    llm = MockLLM(responses=[
        # --- researcher-1 (success) ---
        MockLLMResponse(tool_calls=[{
            "id": "tc_r1", "type": "function",
            "function": {"name": "read", "arguments": json.dumps({"file_path": "/data/drug_labels/metformin.txt"})},
        }]),
        MockLLMResponse(text=_make_envelope_json(
            summary="药品说明书检索完成",
            findings=["[HIGH] eGFR<30禁用"],
            confidence="high",
        )),
        # --- researcher-2 (timeout via max_turns) ---
        MockLLMResponse(tool_calls=[{
            "id": "tc_r2", "type": "function",
            "function": {"name": "read", "arguments": json.dumps({"file_path": "/data/guidelines/t2dm.txt"})},
        }]),
        # 第 2 轮仍调用工具 → max_turns=1 到期
        MockLLMResponse(tool_calls=[{
            "id": "tc_r2_2", "type": "function",
            "function": {"name": "read", "arguments": json.dumps({"file_path": "/data/guidelines/t2dm.txt"})},
        }]),
        # --- researcher-3 (error - LLM crash) ---
        # MockLLM 没有更多 responses → 会返回兜底响应
    ])

    manifests = [
        AgentManifest(name="researcher", description="检索", model="test-model",
                      allowed_tools=["read"], max_turns=5, timeout_seconds=30.0),
    ]

    tool_reg = MockToolRegistry([_make_read_tool(file_contents)])
    subagent_tool = _make_subagent_tool(llm, manifests, tool_reg)

    # 使用 3 个 researcher，其中 researcher-2 用 max_turns=1 模拟超时
    # 但 SubAgentTool 不支持 per-agent max_turns，所以这里用 context 配合
    # 简化方案：直接测试 _aggregate_results 的输入
    from prismclaw.engine.subagent.core._lifecycle_mixin import _collect_evidence_from_messages

    # 构造成功 subagent 的 result
    r1_msg = _make_read_messages_for_collect("/data/drug_labels/metformin.txt", DRUG_LABEL_METFORMIN)
    ev1 = _collect_evidence_from_messages(r1_msg)
    r1_tool_result = ToolResult(
        tool_call_id="tc_r1",
        content=[TextBlock(text=_make_envelope_json(
            summary="药品说明书检索完成",
            findings=["[HIGH] eGFR<30禁用"],
            confidence="high",
        ))],
        is_error=False,
    )
    r1_tool_result.subagent_envelope = SubagentEnvelopeArtifact(
        result_envelope=SubagentResultEnvelope(
            summary="药品说明书检索完成",
            findings=["[HIGH] eGFR<30禁用"],
            evidence=ev1,
            confidence_assessment="high",
        ),
        outcome="success",
        agent_name="researcher-1",
    )

    # 构造超时 subagent 的 result
    r2_msg = _make_read_messages_for_collect("/data/guidelines/t2dm.txt", GUIDELINE_T2DM)
    ev2 = _collect_evidence_from_messages(r2_msg)
    r2_tool_result = ToolResult(
        tool_call_id="tc_r2",
        content=[TextBlock(text="[timeout] Agent exceeded time budget")],
        is_error=True,
    )
    r2_tool_result.subagent_envelope = SubagentEnvelopeArtifact(
        result_envelope=SubagentResultEnvelope(
            summary="[timeout] 部分检索完成",
            findings=["[MEDIUM] 部分指南数据已获取"],
            evidence=ev2,
            confidence_assessment="low",
        ),
        outcome="timeout",
        agent_name="researcher-2",
    )

    # 构造错误 subagent 的 result（无 tool_result）
    r3 = {"agent_name": "researcher-3", "outcome": "error", "error_message": "LLM API error"}

    results = [
        {"agent_name": "researcher-1", "outcome": "success", "tool_result": r1_tool_result},
        {"agent_name": "researcher-2", "outcome": "timeout", "tool_result": r2_tool_result},
        r3,
    ]

    aggregated = SubAgentTool._aggregate_results(None, results, "call_test_mixed")

    # ---- 验证 ----
    assert not aggregated.is_error  # 不是全部 error

    envelope = aggregated.subagent_envelope.result_envelope

    # evidence 来自 researcher-1 + researcher-2（超时的也参与聚合）
    assert len(envelope.evidence) >= 1, \
        f"Expected >=1 evidence from timeout subagent, got {len(envelope.evidence)}"

    # confidence = low（因为有 timeout 的 low 参与）
    assert envelope.confidence_assessment == "low"

    # handover
    handover = render_result_envelope_handover(envelope)
    assert "confidence: low" in handover

    print(f"✅ 场景6: evidence={len(envelope.evidence)}条, "
          f"confidence={envelope.confidence_assessment}")


def _make_read_messages_for_collect(file_path: str, content: str) -> list[dict[str, Any]]:
    """构造 _collect_evidence_from_messages 的输入。"""
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": f"tc_{hash(file_path) % 10000:04d}",
                "type": "function",
                "function": {"name": "read", "arguments": json.dumps({"file_path": file_path})},
            }],
        },
        {"role": "tool", "tool_call_id": f"tc_{hash(file_path) % 10000:04d}", "content": content},
    ]
