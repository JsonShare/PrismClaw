from __future__ import annotations

import re

from prismclaw.engine.execution.handoff.contract import (
    handoff_request_marker_contract_summary,
)
from prismclaw.engine.execution.request._disclaimer_registry import get_advisory_rules
from prismclaw.engine.execution.request._prompt_types import PromptInputs, _wrap_xml


def _looks_like_chinese(text: str | None) -> bool:
    if not text:
        return False
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            return True
    return False


_EN_WORD_RE = re.compile(r"[A-Za-z]{2,}")


def _looks_like_english(text: str | None) -> bool:
    if not text:
        return False
    s = text.strip()
    if not s:
        return False
    if _looks_like_chinese(s):
        return False
    words = _EN_WORD_RE.findall(s)
    if not words:
        return False
    letters = sum(len(w) for w in words)
    non_space = sum(1 for ch in s if not ch.isspace())
    if non_space <= 0:
        return False
    return letters >= 6 or (letters / non_space) >= 0.3


_SUBAGENT_CORE_TEXT = """\
## 子代理并行分发

仅在需要并行调研、独立审查，或工作量超出当前上下文时使用子代理。
不要为简单修改或直接命令而创建子代理。
子代理彼此隔离：必须显式传入全部必要上下文，再把多个结果压缩成单条消息。

## 编排模式

1. **并行检索**：派发 2–4 个 `researcher`，各自负责互不重叠的范围（不同文件路径、关键词或领域）。每个子代理在独立模型实例上运行。
2. **迭代细化**：researcher 结果返回后，检查其输出中的 `open_questions`。若非空，再派发 `researcher` 针对这些缺口补充调研。此前发现会通过 `context_snapshot.key_findings` 自动传递——不要在任务描述中重复。
3. **规划-执行-验证**：调研充分后，先派发 `architect` 做规划，再由 `builder` 执行，最后由 `auditor` 验证。每一步都接收前一步的输出。
4. **合并并行结果**：使用 `reduce_subagent_results` 并指定策略 `merge_envelopes`，合并多个 researcher 的输出（会去重 findings 与 open_questions）。
5. **你是编排者**：当子代理返回 [HANDOFF_REQUEST] 时，评估应当派发建议的代理还是自行处理。不要机械转发——要结合当前任务上下文理解并决策。

## 证据与准确性规则

每个子代理结果都包含 `evidence` 列表（由工具调用自动收集）和 `confidence_assessment`。将子代理结果综合成最终答案时：

1. **按证据置信度分级表述**。仅由 [LOW] 或 [UNVERIFIED] 证据支撑的陈述，必须明确限定（例如“据未经核实的来源……”）。不要把它们当作既定事实。
2. **不得提升置信度**。若子代理报告 `confidence_assessment: low`，则无论其结论看起来多合理，都按低置信度处理。
3. **标出证据缺口**。当用户询问的某项主张没有得到任何子代理的证据支持时，明确说明该信息未经独立核实。
4. **证据去重**。多个子代理引用同一来源时，只引用一次，并采用其中最高的置信度等级。
5. **证据冲突时保持谨慎**。若子代理意见不一致且证据冲突，应同时呈现双方及其置信度，而不是选择其一。
6. **高风险主张必须有 [HIGH] 证据**。安全关键或后果重大的陈述，必须至少有一条 [HIGH] 置信度证据支撑；若没有，须明确提醒用户该主张缺乏验证。
7. **聚合时传递置信度**。合并子代理结果时，合并后的 `confidence_assessment` 必须反映所有来源中的最低置信度。
8. **受监管领域的数值精度**。呈现剂量、条款编号、日期等精确值时，须逐字引用原始来源中的准确值。不要四舍五入、近似或改写数值。
"""


def _build_advisory_text() -> str:
    """Build the full sub-agent advisory text: core rules + domain-specific rules from registry."""
    text = _SUBAGENT_CORE_TEXT
    domain_rules = get_advisory_rules()
    if domain_rules:
        lines = []
        for i, rule in enumerate(domain_rules, start=9):
            lines.append(f"{i}. **{rule.title}**. {rule.body}")
        text += "\n" + "\n".join(lines) + "\n"
    return text

_PHASE3_HANDOFF_ADVISORY_TEXT = (
    "\n\n## 跨代理移交\n"
    "子代理可能发出 request_handoff；ToolResult 的 final_text 会以 "
    f"{handoff_request_marker_contract_summary()} 结尾。你可以："
    "(1) 派发建议的 sub_agent——派发前先评估该移交是否合适；"
    "(2) 通过 reduce_subagent_results 合并（审计记录会写入 merged_from_chains）；"
    "(3) 忽略。移交跳数上限仍然适用。"
)

_PHASE3_HANDOFF_DISABLED_ADVISORY_TEXT = (
    "\n\n## 跨代理移交（受管理限制）\n"
    "移交链长限制为 1——request_handoff 在派发时会失败。"
    "不要尝试派发建议的 sub_agent。把子代理视为终点："
    "收到 [HANDOFF_REQUEST] 块时，直接作答。"
)


def _select_advisory_variant(inputs: PromptInputs) -> str | None:
    if inputs.subagent_registry_size <= 0:
        return None
    handoff_active = (
        inputs.handoff_enabled or inputs.any_manifest_has_can_handoff_to
    )
    advisory = _build_advisory_text()
    if not handoff_active:
        return advisory
    if inputs.max_handoff_chain_length >= 2:
        return advisory + _PHASE3_HANDOFF_ADVISORY_TEXT
    return advisory + _PHASE3_HANDOFF_DISABLED_ADVISORY_TEXT


def advisory_variant_label(inputs: PromptInputs) -> str:
    """Return one of A / B / C / D for the current advisory variant.

    A: registry empty (no advisory).
    B: registry non-empty + handoff disabled (Phase 1+2 only).
    C: handoff enabled + cap >= 2 (Phase 1+2 + Phase 3 enabled).
    D: handoff enabled + cap == 1 (Phase 1+2 + Phase 3 disabled-mode).
    """
    if inputs.subagent_registry_size <= 0:
        return "A"
    handoff_active = (
        inputs.handoff_enabled or inputs.any_manifest_has_can_handoff_to
    )
    if not handoff_active:
        return "B"
    if inputs.max_handoff_chain_length >= 2:
        return "C"
    return "D"


def subagent_advisory_section(inputs: PromptInputs) -> str | None:
    text = _select_advisory_variant(inputs)
    if not text:
        return None
    return _wrap_xml("subagent_advisory", text)
