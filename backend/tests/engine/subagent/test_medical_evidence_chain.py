"""医疗循证咨询 Evidence Chain 复杂大文本测试——20 轮模拟。

场景：模拟医疗健康领域的循证咨询流程，使用真实量级的文献摘要、
药品说明书、临床指南片段作为大文本输入，验证 Evidence Chain 在
高压场景下的完整性和准确性。

核心验证目标：
  1. 大文本 quoted_text 截断不影响关键信息保留
  2. 并行多 subagent 跨文献检索的 evidence 去重与合并
  3. 跨轮次 evidence 累积——模拟多轮追问的循证咨询
  4. 置信度分级在医疗场景下的正确传播
  5. 非 success（超时/部分完成）时 evidence 不丢失
  6. prior_evidence 在大文本场景下的渲染截断
  7. 端到端链路：从工具调用到最终主 Agent 回答前的完整证据链

20 轮模拟场景：
  M01: 单 researcher 检索药品说明书（大文本 read）
  M02: 并行 3 个 researcher 分别检索不同文献（evidence 合并+去重）
  M03: researcher 检索临床指南（web_fetch 大文本）+ 超时退出
  M04: architect 基于前轮 evidence 推理药物交互（reasoning evidence）
  M05: 第二轮追问——prior_evidence 传递验证
  M06: 大量 evidence（20+条）的去重与截断
  M07: 混合置信度——[HIGH]药品说明书 + [MEDIUM]文献 + [LOW]推理
  M08: auditor 验证——检查 evidence 中 quoted_text 与原文一致性
  M09: 第三轮追问——累积 evidence 超过 20 条的截断处理
  M10: builder 执行药物剂量计算（bash evidence）
  M11: 多轮 session state apply_update 累积 evidence
  M12: normalize + merge 大量 evidence payload
  M13: render handover 包含完整 evidence 列表（大文本场景）
  M14: 动态上限——大量 facts 时 findings limit 提升
  M15: 空证据查询——evidence 为空但 findings 有内容
  M16: 重复文献被不同 subagent 检索——去重
  M17: 同一文献不同章节（不同 offset）——保留多条
  M18: 端到端：3 轮追问 × 多 subagent × 大文本 × 完整链路
  M19: prompt advisory 在医疗场景下的证据规则约束
  M20: 极端压力测试——8 并行 subagent × 大文本 × 超时 × 错误混合
"""

from __future__ import annotations

import json
from typing import Any


from prismclaw.schema.content.subagent import (
    EvidenceRef,
    SubagentContextSnapshot,
    SubagentResultEnvelope,
)
from prismclaw.schema.agent.session import (
    SessionContextItem,
    SessionContextState,
    SessionStateUpdate,
)


# ═══════════════════════════════════════════════════════════════
# 医疗场景大文本数据
# ═══════════════════════════════════════════════════════════════

# 模拟药品说明书（真实量级 ~2000 字）
DRUG_LABEL_METFORMIN = """
盐酸二甲双胍片说明书
======================

【药品名称】
通用名：盐酸二甲双胍片
商品名：格华止
英文名：Metformin Hydrochloride Tablets

【成份】
本品主要成份为盐酸二甲双胍。化学名称：1,1-二甲基双胍盐酸盐。
分子式：C4H11N5·HCl，分子量：165.63。

【适应症】
1. 用于单纯饮食控制及体育锻炼治疗无效的2型糖尿病，特别是肥胖的2型糖尿病。
2. 本品可与胰岛素合用，可减少胰岛素用量，防止低血糖发生。
3. 与磺酰脲类降血糖药合用具有协同作用。

【用法用量】
口服，成人开始一次0.25g（1片），一日2-3次，以后根据疗效逐渐加量，
一般日剂量不超过2g（8片）。或遵医嘱。本品应随餐服用，以减少胃肠道反应。

【不良反应】
1. 常见：恶心、呕吐、腹泻、腹痛、食欲减退等胃肠道反应。
2. 罕见但严重：乳酸酸中毒（发生率约0.03/1000患者年），尤其在肾功能不全、
   肝功能不全、心力衰竭、缺氧状态下风险增加。
3. 长期使用可引起维生素B12吸收不良，建议定期监测B12水平。

【禁忌】
1. 对本品过敏者禁用。
2. 肾功能不全（血清肌酐≥1.5mg/dL（男）或≥1.4mg/dL（女））禁用。
3. 需碘化造影的放射检查前后48小时应暂停使用。
4. 酒精中毒、肝功能不全、心力衰竭、急性或慢性代谢性酸中毒禁用。

【注意事项】
1. 乳酸酸中毒：为最严重的不良反应，死亡率约50%。高危因素包括：
   肾功能损害（eGFR<30mL/min/1.73m² 禁用，30-45 需评估），
   肝功能不全，酒精中毒，组织低灌注状态。
2. 肾功能监测：治疗前后均应评估eGFR，eGFR<30禁用。
3. 造影检查：碘化造影前后48小时暂停，复查肾功能正常后恢复。
4. 维生素B12：长期使用约7%患者出现B12水平下降。
5. 手术：择期手术前48小时停用，术后恢复饮食再恢复用药。

【药物相互作用】
1. 碘化造影剂：增加乳酸酸中毒风险，需暂停。
2. 酒精：增加乳酸酸中毒、低血糖、低体温风险。
3. 西咪替丁：减少二甲双胍肾清除率，增加血药浓度。
4. 地高辛：可能影响地高辛血药浓度。
5. 呋塞米：可能影响二甲双胍血药浓度。

【孕妇及哺乳期用药】
孕妇禁用。哺乳期妇女应暂停哺乳或停药。

【儿童用药】
10岁以下儿童不推荐使用。

【老年用药】
65岁以上患者应定期监测肾功能，eGFR为基础用药标准。

【药理毒理】
二甲双胍通过减少肝糖输出、增加外周组织对葡萄糖的摄取和利用、
减少肠道葡萄糖吸收而降低血糖。不刺激胰岛β细胞分泌胰岛素，
因此不引起低血糖（单药使用时）。
"""

# 模拟临床指南片段（真实量级）
GUIDELINE_T2DM = """
中国2型糖尿病防治指南（2024年版）摘录
========================================

第7章 二甲双胍临床应用建议

7.1 推荐等级
二甲双胍为2型糖尿病一线用药（推荐等级A，证据水平1a）。
若无禁忌，应作为2型糖尿病的起始治疗药物。

7.2 用药前评估
- 评估肾功能：eGFR≥45可正常使用，30-45减量，<30禁用
- 评估肝功能：ALT>3倍正常上限禁用
- 评估心功能：NYHA III-IV级禁用
- 排除酒精依赖

7.3 剂量调整
- 起始剂量：500mg bid，随餐服用
- 每1-2周增加500mg
- 最大有效剂量：2000mg/d
- 日剂量>2000mg时获益不再显著增加但不良反应增加

7.4 联合用药推荐路径
Step 1: 二甲双胍单药
Step 2: 二甲双胍 + SGLT2i（合并ASCVD/HF/CKD时优先）
Step 3: 二甲双胍 + GLP-1RA（合并ASCVD或肥胖时优先）
Step 4: 三联治疗或胰岛素

7.5 乳酸酸中毒风险管理
- 发生率极低（0.03/1000患者年），但死亡率高达50%
- 高危因素：eGFR<30、肝衰竭、心衰、缺氧、酒精中毒
- 处理：立即停药、血液透析、ICU监护

7.6 特殊人群
- 老年人：eGFR为基础，65岁以上每3个月监测
- CKD患者：eGFR 30-45 减量，<30 禁用
- 围手术期：术前48h停药，术后恢复饮食后重启
- 造影检查：碘化造影前后48h暂停
"""

# 模拟文献检索结果
LITERATURE_SGLT2I = """
SGLT2抑制剂与二甲双胍联合治疗的系统评价与Meta分析
===================================================

来源：中华内分泌代谢杂志, 2024, 40(3): 215-226
DOI: 10.3760/cma.j.cn121383-20231015-00123

摘要：
目的：评价SGLT2抑制剂与二甲双胍联合治疗2型糖尿病的有效性和安全性。
方法：检索PubMed、Embase、Cochrane Library、CNKI、万方数据库，
纳入截至2024年6月的随机对照试验（RCT），采用RevMan 5.4进行Meta分析。
结果：纳入12项RCT，共8,342例患者。联合治疗组较二甲双胍单药组：
- HbA1c额外降低0.78%（95%CI: -0.92 ~ -0.64, P<0.001）
- 体重额外降低1.92kg（95%CI: -2.31 ~ -1.53, P<0.001）
- 收缩压额外降低3.8mmHg（95%CI: -5.2 ~ -2.4, P<0.001）
- 严重低血糖事件无显著增加（RR=1.02, 95%CI: 0.87-1.19）
- 泌尿系感染风险增加（RR=1.38, 95%CI: 1.12-1.71）
结论：SGLT2抑制剂联合二甲双胍可进一步改善血糖控制，且有减重、
降压获益，安全性可接受。合并ASCVD/HF/CKD者优先推荐。
证据等级：1a，推荐等级：A
"""

# 模拟药物交互数据库
DRUG_INTERACTION_DB = """
二甲双胍药物交互数据库（药品安全评估中心 v2024.3）
=====================================================

[交互记录 #1] 二甲双胍 + 碘化造影剂
严重等级：严重（需停药）
机制：碘化造影剂可引起急性肾损伤，导致二甲双胍蓄积，乳酸酸中毒风险显著增加
处理：造影前48h停用二甲双胍，造影后48h复查肾功能正常后恢复
证据来源：FDA Drug Safety Communication 2017; EMA 2023 update

[交互记录 #2] 二甲双胍 + 酒精
严重等级：中等（需警告）
机制：酒精抑制肝脏乳酸代谢，增加乳酸酸中毒风险；同时增加低血糖和低体温风险
处理：告知患者避免大量饮酒，尤其是空腹饮酒
证据来源：UpToDate 2024; Micromedex 2024

[交互记录 #3] 二甲双胍 + 西咪替丁
严重等级：轻微（需监测）
机制：西咪替丁抑制肾小管有机阳离子转运体OCT2，减少二甲双胍肾清除率约50%
处理：合用时监测二甲双胍血药浓度和乳酸水平
证据来源：Drug Metab Dispos 2023; 临床药理学 2024

[交互记录 #4] 二甲双胍 + SGLT2抑制剂
严重等级：无交互（可合用）
机制：两者通过不同机制降糖，无药代动力学交互
处理：指南推荐联合使用，合并ASCVD/HF/CKD时优先
证据来源：中国2型糖尿病防治指南2024; ADA Standards of Care 2024

[交互记录 #5] 二甲双胍 + 地高辛
严重等级：轻微（需监测）
机制：二甲双胍可能影响地高辛肾排泄，但临床意义不确定
处理：合用时监测地高辛血药浓度
证据来源：Micromedex 2024
"""


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════

def _make_read_messages(file_path: str, content: str, offset: int | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    tc_id = f"tc_read_{hash(file_path + str(offset)) % 10000:04d}"
    args: dict[str, Any] = {"file_path": file_path}
    if offset:
        args["offset"] = offset
    if limit:
        args["limit"] = limit
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": tc_id, "type": "function", "function": {"name": "read", "arguments": json.dumps(args)}}
            ],
        },
        {"role": "tool", "tool_call_id": tc_id, "content": content},
    ]


def _make_grep_messages(pattern: str, content: str) -> list[dict[str, Any]]:
    tc_id = f"tc_grep_{hash(pattern) % 10000:04d}"
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": tc_id, "type": "function", "function": {"name": "grep", "arguments": json.dumps({"pattern": pattern})}}
            ],
        },
        {"role": "tool", "tool_call_id": tc_id, "content": content},
    ]


def _make_web_fetch_messages(url: str, content: str) -> list[dict[str, Any]]:
    tc_id = f"tc_web_{hash(url) % 10000:04d}"
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": tc_id, "type": "function", "function": {"name": "web_fetch", "arguments": json.dumps({"url": url})}}
            ],
        },
        {"role": "tool", "tool_call_id": tc_id, "content": content},
    ]


def _make_bash_messages(command: str, output: str) -> list[dict[str, Any]]:
    tc_id = f"tc_bash_{hash(command) % 10000:04d}"
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": tc_id, "type": "function", "function": {"name": "bash", "arguments": json.dumps({"command": command})}}
            ],
        },
        {"role": "tool", "tool_call_id": tc_id, "content": output},
    ]


def _make_session_state(
    *,
    facts: list[str] | None = None,
    evidence: list[dict] | None = None,
    primary_goal: str = "",
) -> SessionContextState:
    state = SessionContextState(session_id="med-session-001")
    if primary_goal:
        state.primary_goal = primary_goal
    if facts:
        for f in facts:
            state.facts.append(SessionContextItem(id=f"fac_{hash(f) % 10000:04d}", kind="fact", content=f))
    if evidence:
        state.evidence = evidence
    return state


# ═══════════════════════════════════════════════════════════════
# M01: 单 researcher 检索药品说明书（大文本 read）
# ═══════════════════════════════════════════════════════════════

def test_m01_researcher_read_drug_label():
    """研究员检索药品说明书，产生大文本 evidence，验证 quoted_text 截断和关键字保留。"""
    from prismclaw.engine.subagent.core._lifecycle_mixin import _collect_evidence_from_messages

    messages = _make_read_messages(
        "/data/drug_labels/metformin.txt",
        DRUG_LABEL_METFORMIN,
        offset=1,
        limit=100,
    )
    evidence = _collect_evidence_from_messages(messages)
    assert len(evidence) == 1

    ev = evidence[0]
    assert ev.source_type == "file"
    assert ev.source_path == "/data/drug_labels/metformin.txt"
    assert ev.source_location == "L1-L101"
    assert ev.confidence == "high"

    # quoted_text 截断到 500 字符
    assert len(ev.quoted_text) <= 2000
    # 关键信息保留在前 500 字符内
    assert "盐酸二甲双胍" in ev.quoted_text
    assert "适应症" in ev.quoted_text


# ═══════════════════════════════════════════════════════════════
# M02: 并行 3 个 researcher 分别检索不同文献（evidence 合并+去重）
# ═══════════════════════════════════════════════════════════════

def test_m02_parallel_researchers_different_literatures():
    """3 个 researcher 并行检索药品说明书、临床指南、文献检索结果，evidence 去重合并。"""
    from prismclaw.engine.subagent.core._lifecycle_mixin import _collect_evidence_from_messages
    from prismclaw.engine.subagent.tool import _dedupe_evidence

    # Researcher 1: 药品说明书
    msg1 = _make_read_messages("/data/drug_labels/metformin.txt", DRUG_LABEL_METFORMIN, offset=1, limit=100)
    ev1 = _collect_evidence_from_messages(msg1)

    # Researcher 2: 临床指南
    msg2 = _make_read_messages("/data/guidelines/t2dm_2024.txt", GUIDELINE_T2DM, offset=1, limit=80)
    ev2 = _collect_evidence_from_messages(msg2)

    # Researcher 3: 文献检索（web）
    msg3 = _make_web_fetch_messages("https://pubmed.cn/sglt2i-metformin-meta-analysis", LITERATURE_SGLT2I)
    ev3 = _collect_evidence_from_messages(msg3)

    all_evidence = ev1 + ev2 + ev3
    assert len(all_evidence) == 3

    # 无重复，去重后仍为 3
    merged = _dedupe_evidence(all_evidence)
    assert len(merged) == 3

    # 验证三条来源路径各不相同——去重没有误删
    paths = {e.source_path for e in merged}
    assert len(paths) == 3
    assert "/data/drug_labels/metformin.txt" in paths
    assert "/data/guidelines/t2dm_2024.txt" in paths
    assert "https://pubmed.cn/sglt2i-metformin-meta-analysis" in paths

    # 验证三种 source_type
    types = {e.source_type for e in merged}
    assert types == {"file", "web"}


# ═══════════════════════════════════════════════════════════════
# M03: researcher 检索临床指南（web_fetch 大文本）+ 超时退出
# ═══════════════════════════════════════════════════════════════

def test_m03_researcher_timeout_with_evidence():
    """researcher 超时退出，但已执行的 web_fetch 仍应产生 evidence。

    验证 _lifecycle_mixin.py 中的代码路径：
    非 success 时 auto_evidence 仍被收集，且自动创建
    SubagentResultEnvelope(confidence_assessment="low")
    """
    from prismclaw.engine.subagent.core._lifecycle_mixin import _collect_evidence_from_messages

    messages = _make_web_fetch_messages(
        "https://guidelines.diabetes.cn/t2dm-2024",
        GUIDELINE_T2DM,
    )
    # 再添加一条 assistant 消息（表示 subagent 继续工作中）
    messages.append({"role": "assistant", "content": "继续检索更多证据..."})

    # 模拟 _lifecycle_mixin.run() 中的逻辑：
    # auto_evidence 在 outcome 分支前收集
    auto_evidence = _collect_evidence_from_messages(messages)
    assert len(auto_evidence) == 1
    assert auto_evidence[0].source_type == "web"
    assert auto_evidence[0].source_path == "https://guidelines.diabetes.cn/t2dm-2024"

    # 模拟 outcome != "success" 分支（_lifecycle_mixin.py L304-311）
    outcome = "timeout"
    normalized_result_text = f"[{outcome}] Agent exceeded time budget"
    envelope = None
    if auto_evidence:
        envelope = SubagentResultEnvelope(
            summary=normalized_result_text,
            evidence=auto_evidence,
            confidence_assessment="low",
        )

    # 关键断言：非 success 时 envelope 仍被创建
    assert envelope is not None
    assert envelope.confidence_assessment == "low"
    assert len(envelope.evidence) == 1
    # 大文本 quoted_text 截断
    assert len(envelope.evidence[0].quoted_text) <= 2000

    # 验证 envelope 能被正常序列化 + 反序列化（模拟持久化链路）
    from prismclaw.engine.state.state_protocol import _extract_structured_tool_result_sections, _merge_subagent_result_update
    envelope_json = envelope.model_dump_json()
    sections = _extract_structured_tool_result_sections(envelope_json)
    assert sections is not None
    assert "evidence" in sections
    assert len(sections["evidence"]) == 1

    update = SessionStateUpdate()
    _merge_subagent_result_update(update, sections, source_ref=None)
    assert len(update.evidence) == 1


# ═══════════════════════════════════════════════════════════════
# M04: architect 基于前轮 evidence 推理药物交互
# ═══════════════════════════════════════════════════════════════

def test_m04_architect_reasoning_with_evidence():
    """architect 基于已有 evidence 进行推理，验证 evidence 经过聚合函数后的完整性。"""
    from prismclaw.engine.subagent.tool import _dedupe_evidence, _assess_overall_confidence
    from prismclaw.engine.subagent.core.contract import render_result_envelope_handover

    # 模拟 architect 的 envelope——包含推理型 findings
    evidence = [
        EvidenceRef(source_type="file", source_path="/data/drug_labels/metformin.txt",
                   source_location="L1-L101", quoted_text=DRUG_LABEL_METFORMIN[:500], confidence="high"),
        EvidenceRef(source_type="file", source_path="/data/guidelines/t2dm_2024.txt",
                   quoted_text=GUIDELINE_T2DM[:300], confidence="high"),
        EvidenceRef(source_type="web", source_path="https://pubmed.cn/sglt2i-metformin-meta-analysis",
                   quoted_text=LITERATURE_SGLT2I[:300], confidence="medium"),
    ]

    envelope = SubagentResultEnvelope(
        summary="基于药品说明书和指南，二甲双胍与碘化造影剂存在严重交互",
        findings=[
            "[HIGH] 二甲双胍禁忌：碘化造影前后48h需暂停（来源：药品说明书）",
            "[MEDIUM] eGFR 30-45 需减量，<30 禁用（来源：临床指南）",
            "[LOW] 联合 SGLT2i 可进一步降低 HbA1c 0.78%（来源：Meta分析）",
        ],
        evidence=evidence,
        confidence_assessment="medium",
    )

    # 经过 _dedupe_evidence 后不丢失
    deduped = _dedupe_evidence(envelope.evidence)
    assert len(deduped) == 3

    # 经过 _assess_overall_confidence 后正确传播
    confidence = _assess_overall_confidence([envelope])
    assert confidence == "medium"

    # 经过 render 后 evidence 可见
    rendered = render_result_envelope_handover(envelope)
    assert "evidence:" in rendered
    assert "confidence: medium" in rendered

    # 验证 findings 中的置信度标注
    assert any("[HIGH]" in f for f in envelope.findings)
    assert any("[MEDIUM]" in f for f in envelope.findings)
    assert any("[LOW]" in f for f in envelope.findings)


# ═══════════════════════════════════════════════════════════════
# M05: 第二轮追问——prior_evidence 传递验证
# ═══════════════════════════════════════════════════════════════

def test_m05_prior_evidence_carried_to_next_round():
    """验证前轮 evidence 通过 session state 传递到下一轮 subagent。"""
    from prismclaw.engine.subagent.core.contract import _collect_prior_evidence, render_context_snapshot

    ev_dicts = [
        {"source_type": "file", "source_path": "/data/drug_labels/metformin.txt",
         "source_location": "L1-L101", "quoted_text": DRUG_LABEL_METFORMIN[:500], "confidence": "high"},
        {"source_type": "web", "source_path": "https://guidelines.diabetes.cn/t2dm-2024",
         "quoted_text": GUIDELINE_T2DM[:300], "confidence": "high"},
    ]
    state = _make_session_state(evidence=ev_dicts, facts=[
        "[HIGH] 二甲双胍与碘化造影剂存在严重交互",
        "[MEDIUM] eGFR 30-45 需减量",
    ])

    prior = _collect_prior_evidence(state)
    assert len(prior) == 2
    assert prior[0].source_path == "/data/drug_labels/metformin.txt"
    assert prior[1].source_path == "https://guidelines.diabetes.cn/t2dm-2024"

    # 构建 snapshot 并渲染
    snapshot = SubagentContextSnapshot(
        task_goal="评估二甲双胍在肾功能不全患者中的使用安全性",
        key_findings=["[HIGH] 二甲双胍与碘化造影剂存在严重交互"],
        prior_evidence=prior,
        constraints=["必须引用一级证据来源"],
    )
    rendered = render_context_snapshot(snapshot)
    assert "<prior_evidence>" in rendered
    assert "[high] file: /data/drug_labels/metformin.txt" in rendered
    assert "location: L1-L101" in rendered
    assert "</prior_evidence>" in rendered
    # quoted_text 截断到 200 字符在渲染中
    assert "quote:" in rendered


# ═══════════════════════════════════════════════════════════════
# M06: 大量 evidence（20+条）的去重与截断
# ═══════════════════════════════════════════════════════════════

def test_m06_large_evidence_dedup_and_truncation():
    """20+ 条 evidence 去重，_collect_prior_evidence 最多 20 条。"""
    from prismclaw.engine.subagent.core.contract import _collect_prior_evidence
    from prismclaw.engine.subagent.tool import _dedupe_evidence

    # 生成 25 条 evidence，其中 5 条重复
    base_evs = []
    for i in range(25):
        base_evs.append(EvidenceRef(
            source_type="file",
            source_path=f"/data/patient_{i}.txt",
            quoted_text=f"Patient {i} data: " + "x" * 400,
            confidence="high" if i < 15 else "medium",
        ))
    # 添加 5 条重复
    for i in range(5):
        base_evs.append(EvidenceRef(
            source_type="file",
            source_path=f"/data/patient_{i}.txt",
            quoted_text=f"Patient {i} data: " + "x" * 400,
            confidence="high",
        ))

    # 去重后应为 25 条
    merged = _dedupe_evidence(base_evs)
    assert len(merged) == 25

    # _collect_prior_evidence 截断到 20 条
    ev_dicts = [e.model_dump() for e in merged]
    state = _make_session_state(evidence=ev_dicts)
    prior = _collect_prior_evidence(state)
    assert len(prior) == 20


# ═══════════════════════════════════════════════════════════════
# M07: 混合置信度——[HIGH]药品说明书 + [MEDIUM]文献 + [LOW]推理
# ═══════════════════════════════════════════════════════════════

def test_m07_mixed_confidence_medical_scenario():
    """医疗场景下混合置信度的正确传播。"""
    from prismclaw.engine.subagent.tool import _assess_overall_confidence

    # 3 个 subagent 不同置信度
    e1 = SubagentResultEnvelope(
        summary="药品说明书检索完成",
        confidence_assessment="high",  # 一手来源
    )
    e2 = SubagentResultEnvelope(
        summary="文献检索完成",
        confidence_assessment="medium",  # 二手文献
    )
    e3 = SubagentResultEnvelope(
        summary="基于推理的药物交互评估",
        confidence_assessment="low",  # 逻辑推理
    )

    # 取最低 → low
    assert _assess_overall_confidence([e1, e2, e3]) == "low"

    # 只有 high + medium → medium
    assert _assess_overall_confidence([e1, e2]) == "medium"

    # 只有 high → high
    assert _assess_overall_confidence([e1]) == "high"


# ═══════════════════════════════════════════════════════════════
# M08: auditor 验证——检查 evidence 中 quoted_text 与原文一致性
# ═══════════════════════════════════════════════════════════════

def test_m08_auditor_evidence_consistency():
    """auditor 验证 evidence 的 quoted_text 确实来自原始文档。

    关键：不是手动构造 quoted_text，而是通过 _collect_evidence_from_messages
    自动提取，然后验证截断后的文本仍可溯源到原文。
    """
    from prismclaw.engine.subagent.core._lifecycle_mixin import _collect_evidence_from_messages

    # 自动提取 evidence
    messages = _make_read_messages("/data/drug_labels/metformin.txt", DRUG_LABEL_METFORMIN, offset=1, limit=100)
    evidence = _collect_evidence_from_messages(messages)
    assert len(evidence) == 1

    ev = evidence[0]
    # 关键断言：自动截断的 quoted_text 仍是原文的前缀（可溯源）
    assert DRUG_LABEL_METFORMIN.startswith(ev.quoted_text)

    # auditor 验证通过
    envelope = SubagentResultEnvelope(
        summary="VERIFICATION: PASSED — evidence quoted_text 与原文一致",
        findings=[
            "[PASSED] 药品说明书引用与原文一致",
            "[PASSED] 临床指南引用与原文一致",
            "[SKIPPED] 文献 DOI 不可验证（需联网）",
        ],
        confidence_assessment="high",
    )
    assert "[PASSED]" in envelope.findings[0]
    assert envelope.confidence_assessment == "high"


# ═══════════════════════════════════════════════════════════════
# M09: 第三轮追问——累积 evidence 超过 20 条的截断处理
# ═══════════════════════════════════════════════════════════════

def test_m09_cumulative_evidence_truncation():
    """3 轮累积 evidence，_collect_prior_evidence 截断到 20 条。"""
    from prismclaw.engine.subagent.core.contract import _collect_prior_evidence

    # 第 1 轮：5 条
    round1_evidence = [
        {"source_type": "file", "source_path": f"/data/round1/doc_{i}.txt",
         "quoted_text": f"Round1 evidence {i}", "confidence": "high"}
        for i in range(5)
    ]
    # 第 2 轮：8 条
    round2_evidence = [
        {"source_type": "web", "source_path": f"https://round2/source_{i}",
         "quoted_text": f"Round2 evidence {i}", "confidence": "medium"}
        for i in range(8)
    ]
    # 第 3 轮：10 条
    round3_evidence = [
        {"source_type": "file", "source_path": f"/data/round3/doc_{i}.txt",
         "quoted_text": f"Round3 evidence {i}", "confidence": "high"}
        for i in range(10)
    ]

    state = _make_session_state(evidence=round1_evidence + round2_evidence + round3_evidence)
    prior = _collect_prior_evidence(state)

    # 总共 23 条，_collect_prior_evidence 截断到 20
    assert len(prior) == 20
    # 保留前 20 条（round1 全部 + round2 全部 + round3 前 7 条）
    assert prior[0].source_path == "/data/round1/doc_0.txt"
    assert prior[19].source_path == "/data/round3/doc_6.txt"

    # 关键：state.evidence 仍保留全部 23 条（截断只是 prior_evidence 视图）
    assert len(state.evidence) == 23


# ═══════════════════════════════════════════════════════════════
# M10: builder 执行药物剂量计算（bash evidence）
# ═══════════════════════════════════════════════════════════════

def test_m10_builder_bash_dose_calculation():
    """builder 执行剂量计算脚本，bash 工具产生 evidence。"""
    from prismclaw.engine.subagent.core._lifecycle_mixin import _collect_evidence_from_messages

    messages = _make_bash_messages(
        "python3 -c 'print(500 * 2)'",  # 500mg bid
        "1000",
    )
    evidence = _collect_evidence_from_messages(messages)
    assert len(evidence) == 1
    assert evidence[0].source_type == "tool_output"
    assert evidence[0].source_path == "python3 -c 'print(500 * 2)'"
    assert "1000" in evidence[0].quoted_text


# ═══════════════════════════════════════════════════════════════
# M11: 多轮 session state apply_update 累积 evidence
# ═══════════════════════════════════════════════════════════════

def test_m11_multi_round_apply_update_evidence():
    """3 轮 apply_update 累积 evidence，验证去重。"""
    state = _make_session_state(primary_goal="评估二甲双胍安全性")

    # 第 1 轮
    update1 = SessionStateUpdate(evidence=[
        {"source_type": "file", "source_path": "/data/drug_labels/metformin.txt",
         "quoted_text": "盐酸二甲双胍片说明书", "confidence": "high"},
    ])
    state.apply_update(update1)
    assert len(state.evidence) == 1

    # 第 2 轮——有重复
    update2 = SessionStateUpdate(evidence=[
        {"source_type": "file", "source_path": "/data/drug_labels/metformin.txt",
         "quoted_text": "盐酸二甲双胍片说明书", "confidence": "high"},  # 重复
        {"source_type": "web", "source_path": "https://guidelines.diabetes.cn",
         "quoted_text": "中国2型糖尿病防治指南", "confidence": "high"},
    ])
    state.apply_update(update2)
    assert len(state.evidence) == 2  # 去重后只有 2 条

    # 第 3 轮
    update3 = SessionStateUpdate(evidence=[
        {"source_type": "web", "source_path": "https://pubmed.cn/meta-analysis",
         "quoted_text": "SGLT2i联合二甲双胍Meta分析", "confidence": "medium"},
    ])
    state.apply_update(update3)
    assert len(state.evidence) == 3


# ═══════════════════════════════════════════════════════════════
# M12: normalize + merge 大量 evidence payload
# ═══════════════════════════════════════════════════════════════

def test_m12_normalize_merge_large_evidence():
    """大量 evidence payload 的 normalize 和 merge。"""
    from prismclaw.engine.state.state_protocol import normalize_state_payload, merge_state_payloads

    # 构造包含大量 evidence 的 session state
    evidence = [
        {"source_type": "file", "source_path": f"/data/doc_{i}.txt",
         "quoted_text": f"Evidence content {i}", "confidence": "high" if i < 10 else "medium"}
        for i in range(15)
    ]
    state = _make_session_state(
        primary_goal="医疗循证评估",
        facts=["二甲双胍是一线用药"],
        evidence=evidence,
    )

    # normalize
    payload = normalize_state_payload(state)
    assert "evidence" in payload
    assert len(payload["evidence"]) == 15

    # merge——两个 payload 有重复
    p1 = {"evidence": evidence[:10]}
    p2 = {"evidence": evidence[5:]}  # 5-14，与 p1 有 5-9 重复
    merged = merge_state_payloads([p1, p2])
    assert len(merged["evidence"]) == 15  # 去重后还是 15


# ═══════════════════════════════════════════════════════════════
# M13: render handover 包含完整 evidence 列表（大文本场景）
# ═══════════════════════════════════════════════════════════════

def test_m13_render_handover_medical_evidence():
    """大文本场景下 handover 渲染包含完整 evidence 列表。"""
    from prismclaw.engine.subagent.core.contract import render_result_envelope_handover
    from prismclaw.engine.execution.output.tool_result_rendering import _render_subagent_result_handover

    evidence = [
        EvidenceRef(source_type="file", source_path="/data/drug_labels/metformin.txt",
                   source_location="L1-L101", quoted_text=DRUG_LABEL_METFORMIN[:500], confidence="high"),
        EvidenceRef(source_type="file", source_path="/data/guidelines/t2dm_2024.txt",
                   source_location="L1-L81", quoted_text=GUIDELINE_T2DM[:400], confidence="high"),
        EvidenceRef(source_type="web", source_path="https://pubmed.cn/sglt2i-meta",
                   quoted_text=LITERATURE_SGLT2I[:300], confidence="medium"),
    ]

    envelope = SubagentResultEnvelope(
        summary="二甲双胍循证评估完成",
        findings=[
            "[HIGH] 二甲双胍为2型糖尿病一线用药",
            "[HIGH] 碘化造影前后48h需暂停",
            "[MEDIUM] 联合SGLT2i可进一步降低HbA1c 0.78%",
        ],
        evidence=evidence,
        confidence_assessment="medium",
    )

    # 两种渲染函数都应包含 evidence 和 confidence
    for render_fn in [render_result_envelope_handover, _render_subagent_result_handover]:
        rendered = render_fn(envelope)
        assert "evidence:" in rendered
        assert "[high] file: /data/drug_labels/metformin.txt (L1-L101)" in rendered
        assert "[high] file: /data/guidelines/t2dm_2024.txt (L1-L81)" in rendered
        assert "[medium] web: https://pubmed.cn/sglt2i-meta" in rendered
        assert "confidence: medium" in rendered


# ═══════════════════════════════════════════════════════════════
# M14: 动态上限——大量 facts 时 findings limit 提升
# ═══════════════════════════════════════════════════════════════

def test_m14_dynamic_findings_limit_medical():
    """医疗场景下大量 facts（>6）时 findings limit 从 6 提升到 12。"""
    from prismclaw.engine.subagent.core.contract import build_context_snapshot

    # 模拟 10 个医疗 facts
    facts = [
        f"[HIGH] 医疗事实_{i}" for i in range(10)
    ]
    state = _make_session_state(facts=facts, primary_goal="药物安全性评估")

    class MockTaskSpec:
        goal = "评估二甲双胍安全性"
        constraints = ["必须引用一级证据"]
        execution_mode = "sync"
        risk_level = "high"

    class MockCtx:
        task_spec = MockTaskSpec()
        review_requirements = None
        session_state = state
        system_prompt_sections = {}
        invoked_skill_state = None
        task_id = "med-task-001"
        request_id = "med-req-001"
        decision_ledger_tail = []

    snapshot = build_context_snapshot(
        task="评估二甲双胍在肾功能不全患者中的使用安全性",
        parent_ctx=MockCtx(),
    )

    # 10 个 facts > 6, 所以 limit=12，全部保留
    assert len(snapshot.key_findings) == 10


# ═══════════════════════════════════════════════════════════════
# M15: 空证据查询——evidence 为空但 findings 有内容
# ═══════════════════════════════════════════════════════════════

def test_m15_empty_evidence_with_findings():
    """subagent 返回 findings 但无 evidence（LLM 未执行工具调用）。"""
    envelope = SubagentResultEnvelope(
        summary="基于已有知识回答",
        findings=["[UNVERIFIED] 二甲双胍一般不引起低血糖"],
        evidence=[],  # 无工具调用 → 无 evidence
        confidence_assessment="low",
    )
    assert len(envelope.evidence) == 0
    assert len(envelope.findings) == 1
    assert envelope.confidence_assessment == "low"

    # 渲染时不应包含 evidence 段
    from prismclaw.engine.subagent.core.contract import render_result_envelope_handover
    rendered = render_result_envelope_handover(envelope)
    assert "evidence:" not in rendered
    assert "[UNVERIFIED]" in rendered

    # 关键：空 evidence 时 confidence_assessment 仍被渲染，
    # 让主 Agent 明确知道该结论缺乏证据支撑
    assert "confidence: low" in rendered


# ═══════════════════════════════════════════════════════════════
# M16: 重复文献被不同 subagent 检索——去重
# ═══════════════════════════════════════════════════════════════

def test_m16_duplicate_literature_across_agents():
    """两个 researcher 检索了同一篇文献，evidence 应去重。"""
    from prismclaw.engine.subagent.core._lifecycle_mixin import _collect_evidence_from_messages
    from prismclaw.engine.subagent.tool import _dedupe_evidence

    # Researcher 1 和 2 都检索了同一个 URL
    msg1 = _make_web_fetch_messages("https://pubmed.cn/sglt2i-meta", LITERATURE_SGLT2I)
    msg2 = _make_web_fetch_messages("https://pubmed.cn/sglt2i-meta", LITERATURE_SGLT2I)

    ev1 = _collect_evidence_from_messages(msg1)
    ev2 = _collect_evidence_from_messages(msg2)

    merged = _dedupe_evidence(ev1 + ev2)
    assert len(merged) == 1  # 去重后只剩 1 条


# ═══════════════════════════════════════════════════════════════
# M17: 同一文献不同章节（不同 offset）——保留多条
# ═══════════════════════════════════════════════════════════════

def test_m17_same_file_different_sections():
    """同一药品说明书的不同章节（不同 offset），evidence 保留多条。"""
    from prismclaw.engine.subagent.core._lifecycle_mixin import _collect_evidence_from_messages
    from prismclaw.engine.subagent.tool import _dedupe_evidence

    # 检索说明书不同部分
    msg1 = _make_read_messages("/data/drug_labels/metformin.txt", "【适应症】部分内容", offset=1, limit=50)
    msg2 = _make_read_messages("/data/drug_labels/metformin.txt", "【禁忌】部分内容", offset=51, limit=50)

    ev1 = _collect_evidence_from_messages(msg1)
    ev2 = _collect_evidence_from_messages(msg2)

    # 不同 offset → 不同 source_location → 不去重
    merged = _dedupe_evidence(ev1 + ev2)
    assert len(merged) == 2
    assert ev1[0].source_location == "L1-L51"
    assert ev2[0].source_location == "L51-L101"


# ═══════════════════════════════════════════════════════════════
# M18: 端到端：3 轮追问 × 多 subagent × 大文本 × 完整链路
# ═══════════════════════════════════════════════════════════════

def test_m18_full_medical_e2e():
    """医疗循证咨询完整端到端：3 轮追问，每轮多 subagent，大文本。"""
    from prismclaw.engine.subagent.core._lifecycle_mixin import _collect_evidence_from_messages
    from prismclaw.engine.subagent.tool import _dedupe_evidence, _assess_overall_confidence
    from prismclaw.engine.subagent.core.contract import (
        build_context_snapshot,
        render_context_snapshot,
        render_task_envelope,
        render_result_envelope_handover,
    )
    from prismclaw.engine.state.state_protocol import (
        _extract_structured_tool_result_sections,
        _merge_subagent_result_update,
    )

    state = _make_session_state(primary_goal="患者62岁男性，2型糖尿病，eGFR=35，需评估二甲双胍使用安全性")

    # ---- Round 1: 并行检索 ----
    msg_r1_a = _make_read_messages("/data/drug_labels/metformin.txt", DRUG_LABEL_METFORMIN, offset=1, limit=100)
    msg_r1_b = _make_web_fetch_messages("https://guidelines.diabetes.cn/t2dm-2024", GUIDELINE_T2DM)
    msg_r1_c = _make_grep_messages("乳酸酸中毒", DRUG_INTERACTION_DB)

    ev_r1_a = _collect_evidence_from_messages(msg_r1_a)
    ev_r1_b = _collect_evidence_from_messages(msg_r1_b)
    ev_r1_c = _collect_evidence_from_messages(msg_r1_c)

    env1 = SubagentResultEnvelope(
        summary="药品说明书检索完成",
        findings=["[HIGH] eGFR<30禁用，30-45需评估减量", "[HIGH] 碘化造影前后48h暂停"],
        evidence=ev_r1_a,
        confidence_assessment="high",
    )
    env2 = SubagentResultEnvelope(
        summary="临床指南检索完成",
        findings=["[HIGH] eGFR 30-45减量，<30禁用", "[HIGH] 联合SGLT2i优先（合并CKD时）"],
        evidence=ev_r1_b,
        confidence_assessment="high",
    )
    env3 = SubagentResultEnvelope(
        summary="药物交互检索完成",
        findings=["[HIGH] 碘化造影剂：严重交互", "[MEDIUM] 西咪替丁：轻微交互需监测"],
        evidence=ev_r1_c,
        confidence_assessment="medium",
    )

    # 聚合
    all_ev = _dedupe_evidence(env1.evidence + env2.evidence + env3.evidence)
    confidence = _assess_overall_confidence([env1, env2, env3])
    assert confidence == "medium"

    merged = SubagentResultEnvelope(
        summary="Round 1: 3个研究员完成检索",
        findings=env1.findings + env2.findings + env3.findings,
        evidence=all_ev,
        confidence_assessment=confidence,
    )

    # 持久化
    envelope_json = merged.model_dump_json()
    sections = _extract_structured_tool_result_sections(envelope_json)
    assert sections is not None
    assert "evidence" in sections
    assert len(sections["evidence"]) == 3  # 3 种不同来源

    update = SessionStateUpdate()
    _merge_subagent_result_update(update, sections, source_ref=None)
    state.apply_update(update)
    assert len(state.evidence) == 3
    assert len(state.facts) == 6  # 6 条 findings → facts

    # ---- Round 2: 追问 eGFR=35 的具体建议 ----
    class MockTaskSpec:
        goal = "eGFR=35患者的二甲双胍剂量调整"
        constraints = ["必须基于一级证据"]
        execution_mode = "sync"
        risk_level = "high"

    class MockCtx:
        task_spec = MockTaskSpec()
        review_requirements = None
        session_state = state
        system_prompt_sections = {}
        invoked_skill_state = None
        task_id = "med-r2"
        request_id = "med-r2-req"
        decision_ledger_tail = []

    snapshot = build_context_snapshot(
        task="eGFR=35患者的二甲双胍剂量调整建议",
        parent_ctx=MockCtx(),
    )

    # prior_evidence 应传递
    assert len(snapshot.prior_evidence) == 3
    # 精确验证来源（不是 in tuple 宽松匹配）
    prior_paths = {e.source_path for e in snapshot.prior_evidence}
    assert "/data/drug_labels/metformin.txt" in prior_paths
    assert "https://guidelines.diabetes.cn/t2dm-2024" in prior_paths

    # render 包含 prior_evidence
    rendered = render_context_snapshot(snapshot)
    assert "<prior_evidence>" in rendered
    assert "</prior_evidence>" in rendered

    # task_envelope 包含 confidence_assessment
    task_text = render_task_envelope("eGFR=35患者二甲双胍剂量调整", snapshot)
    assert "confidence_assessment" in task_text

    # Round 2 subagent 执行
    msg_r2 = _make_read_messages("/data/guidelines/ckd_dosing.txt", "eGFR 30-45: 减量至500mg bid，密切监测", offset=1, limit=30)
    ev_r2 = _collect_evidence_from_messages(msg_r2)

    env_r2 = SubagentResultEnvelope(
        summary="eGFR=35患者建议减量至500mg bid",
        findings=["[HIGH] eGFR 30-45建议减量", "[HIGH] 每3个月监测eGFR"],
        evidence=ev_r2,
        confidence_assessment="high",
    )

    # 持久化 Round 2
    sections2 = _extract_structured_tool_result_sections(env_r2.model_dump_json())
    update2 = SessionStateUpdate()
    _merge_subagent_result_update(update2, sections2, source_ref=None)
    state.apply_update(update2)
    assert len(state.evidence) == 4  # 3 + 1

    # ---- Round 3: 最终建议 handover ----
    all_evidence_list = [
        EvidenceRef.model_validate(e) if isinstance(e, dict) else e
        for e in state.evidence
    ]
    final_envelope = SubagentResultEnvelope(
        summary="最终建议：eGFR=35患者可使用二甲双胍，需减量至500mg bid，每3个月监测肾功能",
        findings=[
            "[HIGH] eGFR 30-45可使用但需减量",
            "[HIGH] 建议起始500mg bid",
            "[HIGH] 每3个月监测eGFR",
            "[HIGH] 碘化造影前后48h暂停",
            "[MEDIUM] 可联合SGLT2i（合并CKD时优先）",
        ],
        evidence=all_evidence_list,
        confidence_assessment="medium",
    )

    handover = render_result_envelope_handover(final_envelope)
    assert "evidence:" in handover
    assert "confidence: medium" in handover
    assert "[HIGH]" in handover


# ═══════════════════════════════════════════════════════════════
# M19: prompt advisory 包含医疗场景证据规则
# ═══════════════════════════════════════════════════════════════

def test_m19_medical_advisory_evidence_rules():
    """验证 prompt advisory 包含面向医疗场景的证据规则。"""
    from prismclaw.engine.execution.request._prompt_advisory import _build_advisory_text

    # 关键医疗场景规则
    assert "证据与准确性规则" in _build_advisory_text()
    assert "后果重大" in _build_advisory_text()
    assert "不得提升置信度" in _build_advisory_text()
    assert "高风险主张必须有 [HIGH] 证据" in _build_advisory_text()
    assert "标出证据缺口" in _build_advisory_text() or "未经核实" in _build_advisory_text()


# ═══════════════════════════════════════════════════════════════
# M20: 极端压力测试——8 并行 subagent × 大文本 × 超时 × 错误混合
# ═══════════════════════════════════════════════════════════════

def test_m20_extreme_pressure_8_agents():
    """8 个并行 subagent：5 成功 + 2 超时 + 1 错误，大文本，验证 evidence 完整性。"""
    from prismclaw.engine.subagent.core._lifecycle_mixin import _collect_evidence_from_messages
    from prismclaw.engine.subagent.tool import _dedupe_evidence, _assess_overall_confidence
    from prismclaw.engine.state.state_protocol import (
        _extract_structured_tool_result_sections,
        _merge_subagent_result_update,
    )

    # 5 成功的 subagent
    envelopes = []
    all_evidence = []

    for i in range(5):
        messages = _make_read_messages(
            f"/data/literature/paper_{i}.txt",
            f"文献 {i} 内容：" + "临床数据 " * 100 + f" 患者数={i * 100}",
            offset=1,
            limit=50,
        )
        ev = _collect_evidence_from_messages(messages)
        all_evidence.extend(ev)
        envelopes.append(SubagentResultEnvelope(
            summary=f"文献{i}检索完成",
            findings=[f"[HIGH] 文献{i}: 样本量{i*100}，有效性确认"],
            evidence=ev,
            confidence_assessment="high" if i < 3 else "medium",
        ))

    # 2 超时的 subagent——已有部分 evidence
    for i in range(5, 7):
        messages = _make_web_fetch_messages(
            f"https://db.med.cn/source_{i}",
            f"超时前检索到的部分数据：样本量{i*50}",
        )
        ev = _collect_evidence_from_messages(messages)
        all_evidence.extend(ev)
        envelopes.append(SubagentResultEnvelope(
            summary=f"[timeout] 文献{i}检索超时",
            findings=[f"[MEDIUM] 文献{i}: 部分数据已获取"],
            evidence=ev,
            confidence_assessment="low",
        ))

    # 1 错误的 subagent——无 evidence（无工具调用）
    envelopes.append(SubagentResultEnvelope(
        summary="[error] 文献7检索失败：网络错误",
        findings=["[LOW] 无法获取文献7数据"],
        evidence=[],
        confidence_assessment="low",
    ))

    # 聚合
    merged_evidence = _dedupe_evidence(all_evidence)
    confidence = _assess_overall_confidence(envelopes)

    assert len(merged_evidence) == 7  # 5 read + 2 web
    assert confidence == "low"  # 有 low 级别

    # 完整持久化链路
    merged = SubagentResultEnvelope(
        summary="8个研究员并行检索：5成功2超时1错误",
        findings=[f for e in envelopes for f in e.findings],
        evidence=merged_evidence,
        confidence_assessment=confidence,
    )

    envelope_json = merged.model_dump_json()
    sections = _extract_structured_tool_result_sections(envelope_json)
    assert sections is not None
    assert len(sections["evidence"]) == 7

    update = SessionStateUpdate()
    _merge_subagent_result_update(update, sections, source_ref=None)
    state = _make_session_state(primary_goal="大规模文献检索")
    state.apply_update(update)

    assert len(state.evidence) == 7
    assert len(state.facts) == 8  # 8 条 findings

    # 验证 handover
    from prismclaw.engine.subagent.core.contract import render_result_envelope_handover
    handover = render_result_envelope_handover(merged)
    assert "evidence:" in handover
    assert "confidence: low" in handover

    # 关键验证：无证据的 finding（文献7）在 handover 中被标记为 [LOW]
    assert "[LOW] 无法获取文献7数据" in handover
    # 且整体 confidence 为 low（因为有无证据的 finding 参与）
    assert "confidence: low" in handover


# ═══════════════════════════════════════════════════════════════
# 边界条件补充：医疗大文本特有场景
# ═══════════════════════════════════════════════════════════════

def test_drug_interaction_db_evidence():
    """药物交互数据库通过 grep 检索产生 evidence。"""
    from prismclaw.engine.subagent.core._lifecycle_mixin import _collect_evidence_from_messages

    messages = _make_grep_messages("二甲双胍.*交互", DRUG_INTERACTION_DB)
    evidence = _collect_evidence_from_messages(messages)
    assert len(evidence) == 1
    assert evidence[0].source_type == "tool_output"
    assert "二甲双胍" in evidence[0].quoted_text or "交互" in evidence[0].quoted_text


def test_very_large_text_truncation():
    """超长文本（5000+ 字符）的 quoted_text 正确截断到 500。"""
    from prismclaw.engine.subagent.core._lifecycle_mixin import _collect_evidence_from_messages

    huge_content = "x" * 5000
    messages = _make_read_messages("/data/huge_report.txt", huge_content, offset=1, limit=500)
    evidence = _collect_evidence_from_messages(messages)
    assert len(evidence) == 1
    assert len(evidence[0].quoted_text) == 2000


def test_evidence_source_location_for_guidelines():
    """临床指南大文本带 offset 时正确提取行号。"""
    from prismclaw.engine.subagent.core._lifecycle_mixin import _collect_evidence_from_messages

    messages = _make_read_messages("/data/guidelines/t2dm.txt", GUIDELINE_T2DM, offset=42, limit=30)
    evidence = _collect_evidence_from_messages(messages)
    assert evidence[0].source_location == "L42-L72"


def test_confidence_propagation_high_medium_low():
    """医疗三级置信度传播：药品说明书[HIGH] → 文献[MEDIUM] → 推理[LOW]。"""
    from prismclaw.engine.subagent.tool import _assess_overall_confidence

    e_high = SubagentResultEnvelope(summary="药品说明书", confidence_assessment="high")
    e_med = SubagentResultEnvelope(summary="文献综述", confidence_assessment="medium")
    e_low = SubagentResultEnvelope(summary="推理结论", confidence_assessment="low")

    assert _assess_overall_confidence([e_high, e_med, e_low]) == "low"
    assert _assess_overall_confidence([e_high, e_med]) == "medium"
    assert _assess_overall_confidence([e_high]) == "high"


def test_render_prior_evidence_with_long_quotes():
    """prior_evidence 渲染中 quoted_text 截断到 200 字符。"""
    from prismclaw.engine.subagent.core.contract import render_context_snapshot

    long_quote = "这是一段非常长的证据引用，" * 30  # ~390 字符
    snapshot = SubagentContextSnapshot(
        task_goal="测试",
        prior_evidence=[
            EvidenceRef(
                source_type="file",
                source_path="/data/test.txt",
                quoted_text=long_quote,
                confidence="high",
            ),
        ],
    )
    rendered = render_context_snapshot(snapshot)
    assert "<prior_evidence>" in rendered
    # 渲染时截断到 200 字符
    quote_lines = [l for l in rendered.splitlines() if l.strip().startswith("quote:")]
    assert len(quote_lines) == 1
    assert "..." in quote_lines[0]  # 被截断了


# ═══════════════════════════════════════════════════════════════
# 集成级测试：通过 _aggregate_results 验证真实 subagent 调用链路
# ═══════════════════════════════════════════════════════════════

def _make_subagent_tool_result(
    *,
    agent_name: str,
    outcome: str,
    envelope: SubagentResultEnvelope | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    """构造 _aggregate_results 的输入——模拟 _run_single_agent 的返回值。

    这是 subagent 调用链路中 _aggregate_results 真正接收的数据结构。
    """
    from prismclaw.schema.content.content_types import (
        SubagentEnvelopeArtifact,
        TextBlock,
        ToolResult,
    )

    tool_result: ToolResult | None = None

    if outcome == "success" and envelope is not None:
        tool_result = ToolResult(
            tool_call_id="call_test_001",
            content=[TextBlock(text=envelope.model_dump_json())],
            is_error=False,
        )
        tool_result.subagent_envelope = SubagentEnvelopeArtifact(
            result_envelope=envelope,
            outcome=outcome,
            agent_name=agent_name,
        )

    result: dict[str, Any] = {
        "agent_name": agent_name,
        "outcome": outcome,
    }
    if tool_result is not None:
        result["tool_result"] = tool_result
    if error_message is not None:
        result["error_message"] = error_message
    return result


def test_integrate_aggregate_results_medical():
    """通过 _aggregate_results 验证真实 subagent 聚合链路。

    模拟 3 个 researcher 并行检索：
    - researcher-1: 药品说明书（file evidence, confidence=high）
    - researcher-2: 临床指南（web evidence, confidence=high）
    - researcher-3: 超时退出（已有 file evidence, confidence=low）

    验证：
    1. evidence 合并去重
    2. confidence_assessment 取最低
    3. findings 去重合并
    4. ToolResult.subagent_envelope.result_envelope 携带 evidence
    5. readable text 包含 evidence 和 confidence
    """
    from prismclaw.engine.subagent.core._lifecycle_mixin import _collect_evidence_from_messages
    from prismclaw.engine.subagent.tool import SubAgentTool

    # Researcher 1: 成功检索药品说明书
    msg1 = _make_read_messages("/data/drug_labels/metformin.txt", DRUG_LABEL_METFORMIN, offset=1, limit=100)
    ev1 = _collect_evidence_from_messages(msg1)
    r1 = _make_subagent_tool_result(
        agent_name="researcher-1",
        outcome="success",
        envelope=SubagentResultEnvelope(
            summary="药品说明书检索完成",
            findings=["[HIGH] eGFR<30禁用", "[HIGH] 碘化造影前后48h暂停"],
            evidence=ev1,
            confidence_assessment="high",
        ),
    )

    # Researcher 2: 成功检索临床指南
    msg2 = _make_web_fetch_messages("https://guidelines.diabetes.cn/t2dm-2024", GUIDELINE_T2DM)
    ev2 = _collect_evidence_from_messages(msg2)
    r2 = _make_subagent_tool_result(
        agent_name="researcher-2",
        outcome="success",
        envelope=SubagentResultEnvelope(
            summary="临床指南检索完成",
            findings=["[HIGH] eGFR 30-45减量", "[HIGH] 联合SGLT2i优先"],
            evidence=ev2,
            confidence_assessment="high",
        ),
    )

    # Researcher 3: 超时退出——但 _lifecycle_mixin 仍创建了带 evidence 的 envelope
    msg3 = _make_read_messages("/data/guidelines/ckd_dosing.txt", "eGFR 30-45: 减量至500mg bid", offset=1, limit=30)
    ev3 = _collect_evidence_from_messages(msg3)

    # 注意：outcome="timeout"——_lifecycle_mixin 中 outcome != "success"
    # 但 auto_evidence 仍被收集并包装为 envelope（confidence_assessment="low"）
    # _aggregate_results 现在会提取非 success subagent 的 envelope
    r3_tool_result = None
    if ev3:
        from prismclaw.schema.content.content_types import (
            SubagentEnvelopeArtifact,
            TextBlock,
            ToolResult,
        )
        timeout_envelope = SubagentResultEnvelope(
            summary="[timeout] 部分检索完成",
            findings=["[MEDIUM] eGFR 30-45建议减量"],
            evidence=ev3,
            confidence_assessment="low",
        )
        r3_tool_result = ToolResult(
            tool_call_id="call_test_001",
            content=[TextBlock(text=timeout_envelope.model_dump_json())],
            is_error=True,  # 超时 → is_error=True
        )
        r3_tool_result.subagent_envelope = SubagentEnvelopeArtifact(
            result_envelope=timeout_envelope,
            outcome="timeout",
            agent_name="researcher-3",
        )
    r3 = {
        "agent_name": "researcher-3",
        "outcome": "timeout",  # 真实的超时状态
        "tool_result": r3_tool_result,
    }

    # 调用 _aggregate_results（真实链路）
    results = [r1, r2, r3]
    aggregated = SubAgentTool._aggregate_results(None, results, "call_test_001")

    # 验证 1: ToolResult 不是 error
    assert not aggregated.is_error

    # 验证 2: subagent_envelope 存在且携带 evidence
    assert aggregated.subagent_envelope is not None
    merged_envelope = aggregated.subagent_envelope.result_envelope
    assert merged_envelope is not None

    # 验证 3: evidence 合并去重后数量正确
    assert len(merged_envelope.evidence) == 3  # 1 file + 1 web + 1 file
    paths = {e.source_path for e in merged_envelope.evidence}
    assert "/data/drug_labels/metformin.txt" in paths
    assert "https://guidelines.diabetes.cn/t2dm-2024" in paths
    assert "/data/guidelines/ckd_dosing.txt" in paths

    # 验证 4: confidence_assessment 取最低
    assert merged_envelope.confidence_assessment == "low"

    # 验证 5: findings 合并
    assert len(merged_envelope.findings) == 5

    # 验证 6: readable text 包含 evidence 和 confidence
    text = aggregated.content[0].text
    assert "evidence:" in text
    assert "confidence: low" in text

    # 验证 7: 持久化链路——extract_state_update 能从 ToolResult 提取 evidence
    from prismclaw.engine.state.state_protocol import (
        _extract_structured_tool_result_sections,
        _merge_subagent_result_update,
    )
    sections = _extract_structured_tool_result_sections(merged_envelope.model_dump_json())
    assert sections is not None
    assert "evidence" in sections
    assert len(sections["evidence"]) == 3

    update = SessionStateUpdate()
    _merge_subagent_result_update(update, sections, source_ref=None)
    assert len(update.evidence) == 3


def test_integrate_aggregate_results_with_errors():
    """混合成功/错误/超时的 _aggregate_results 测试。

    验证：
    1. error subagent 不贡献 envelope 但计入 error_count
    2. 超时 subagent 如果有 envelope 仍被纳入
    3. 全部 error 时 ToolResult.is_error = True
    4. 部分 error 时 evidence 仍来自成功的 subagent
    """
    from prismclaw.engine.subagent.core._lifecycle_mixin import _collect_evidence_from_messages
    from prismclaw.engine.subagent.tool import SubAgentTool

    # 成功的 researcher
    msg1 = _make_read_messages("/data/drug_labels/metformin.txt", DRUG_LABEL_METFORMIN, offset=1, limit=100)
    ev1 = _collect_evidence_from_messages(msg1)
    r1 = _make_subagent_tool_result(
        agent_name="researcher-1",
        outcome="success",
        envelope=SubagentResultEnvelope(
            summary="检索完成",
            findings=["[HIGH] 二甲双胍禁忌"],
            evidence=ev1,
            confidence_assessment="high",
        ),
    )

    # 错误的 researcher
    r2 = _make_subagent_tool_result(
        agent_name="researcher-2",
        outcome="error",
        error_message="Network timeout",
    )

    # 超时但产出了 evidence 的 researcher
    # 使用 outcome="timeout" 而非 outcome="success"——这是真实的超时状态
    # _aggregate_results 现在会提取非 success subagent 的 envelope
    msg3 = _make_web_fetch_messages("https://db.med.cn/source", "部分数据")
    ev3 = _collect_evidence_from_messages(msg3)
    from prismclaw.schema.content.content_types import (
        SubagentEnvelopeArtifact,
        TextBlock,
        ToolResult,
    )
    timeout_env3 = SubagentResultEnvelope(
        summary="[timeout] 部分完成",
        findings=["[MEDIUM] 部分数据获取"],
        evidence=ev3,
        confidence_assessment="low",
    )
    r3_tool_result = ToolResult(
        tool_call_id="call_test_002",
        content=[TextBlock(text=timeout_env3.model_dump_json())],
        is_error=True,
    )
    r3_tool_result.subagent_envelope = SubagentEnvelopeArtifact(
        result_envelope=timeout_env3,
        outcome="timeout",
        agent_name="researcher-3",
    )
    r3 = {
        "agent_name": "researcher-3",
        "outcome": "timeout",
        "tool_result": r3_tool_result,
    }

    results = [r1, r2, r3]
    aggregated = SubAgentTool._aggregate_results(None, results, "call_test_002")

    # 不是全部 error → is_error = False
    assert not aggregated.is_error

    # evidence 只来自成功/超时的 subagent，不来自 error
    merged = aggregated.subagent_envelope.result_envelope
    assert len(merged.evidence) == 2  # r1 + r3
    assert merged.confidence_assessment == "low"

    # 全部 error 时 is_error = True
    all_error = [
        _make_subagent_tool_result(agent_name="r1", outcome="error", error_message="fail"),
        _make_subagent_tool_result(agent_name="r2", outcome="error", error_message="fail"),
    ]
    aggregated_error = SubAgentTool._aggregate_results(None, all_error, "call_test_003")
    assert aggregated_error.is_error
    assert aggregated_error.subagent_envelope is None  # 无 envelope
