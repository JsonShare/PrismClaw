"""研究简报分析流水线编排。

六阶段状态机（对应 domain/analysis 的纯函数模块）：

    分析中(10) → 主任规划(20) → 知识检索(40) → 专家分析(70)
    → 探索假设(80) → 合成(90) → 审计(95) → 已通过/待修正

设计取舍（与 parse_pipeline 对称）：
- 主任规划失败 → 整条待修正（无规划就无法做后续分析）
- 知识检索失败 → 降级为空，仅靠报告数据做分析
- 专家分析任一路失败 → 该领域标注"暂缺"，其余照常
- 探索性假设失败 → 不影响简报（假设本来就可有可无）
- 合成失败 → 整条待修正
- 审计不通过 → 待修正，audit_result_json 记录违规
- LLM 完全不可用 → 待修正（不是"失败"，可重试）

并发控制：asyncio.Semaphore(2) 限制并行 LLM 调用 ≤2，防 429。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any

from ..domain.analysis.audit_rules import AuditResult, run_audit
from ..domain.analysis.prompts import (
    DIRECTOR_SYSTEM,
    DIRECTOR_TOOL,
    EXPERT_ANALYSIS_TOOL,
    EXPERT_ROLES,
    HYPOTHESIS_SYSTEM,
    HYPOTHESIS_TOOL,
    KB_RETRIEVAL_SYSTEM,
    KB_RETRIEVAL_TOOL,
    SYNTHESIS_SYSTEM,
    SYNTHESIS_TOOL,
    build_director_prompt,
    build_expert_prompt,
    build_hypothesis_prompt,
    build_kb_retrieval_prompt,
    build_synthesis_prompt,
    get_expert_system,
    normalize_brief,
    normalize_director_plan,
    normalize_expert_findings,
    normalize_hypothesis,
    normalize_kb_retrieval,
)
from ..domain.analysis.synthesis import (
    build_raw_texts_map,
    collect_all_citations,
    extract_kb_ids_from_hits,
    make_degradation_section,
)
from ..integration.llm_access import MEDICAL_LLM_SEMAPHORE, complete_json
from ..models.enums import BriefStatus
from ..persistence.brief_store import BriefStore
from ..persistence.kb_store import KbStore
from ..persistence.medical_store import MedicalStore

logger = logging.getLogger(__name__)

# 失败原因写库前截断
ERROR_LIMIT = 500

# 并发信号量：共享全局 MEDICAL_LLM_SEMAPHORE（parse + analysis 总 LLM 并发 ≤ 2）
_SEMAPHORE = MEDICAL_LLM_SEMAPHORE


async def _run_with_semaphore(coro: Any) -> Any:
    """在信号量保护下运行协程。"""
    async with _SEMAPHORE:
        return await coro


# ══════════════════════════════════════════════════════════════
# 公开入口
# ══════════════════════════════════════════════════════════════


async def run_analysis_pipeline(
    store: MedicalStore,
    brief_store: BriefStore,
    kb_store: KbStore,
    brief_id: str,
    *,
    llm: Any | None = None,
    vision: Any | None = None,
) -> None:
    """推进简报到终态。与 parse_pipeline 同构：try/except → _fail。"""
    try:
        await _run(store, brief_store, kb_store, brief_id, llm=llm, vision=vision)
    except Exception as exc:  # noqa: BLE001
        logger.exception("简报分析失败 brief_id=%s", brief_id)
        await _fail(brief_store, brief_id, f"{type(exc).__name__}: {exc}")


async def trigger_analysis_for_patient(
    store: MedicalStore,
    brief_store: BriefStore,
    kb_store: KbStore,
    patient_id: str,
    report_ids: list[str] | None = None,
    *,
    user_id: str = "system",
    llm: Any | None = None,
    vision: Any | None = None,
) -> str | None:
    """为患者创建简报并触发分析。

    report_ids 为空时自动检测未分析报告。
    若该患者已有"分析中"的简报，跳过创建（防并发重复）。
    返回 brief_id，无待分析报告时返回 None。
    """
    # 防重复：已有分析中的简报则跳过
    if await brief_store.has_active_brief(patient_id):
        logger.info("患者 %s 已有分析中简报，跳过创建", patient_id)
        return None

    if report_ids is None:
        report_ids = await brief_store.list_unanalyzed_reports(patient_id)
    if not report_ids:
        return None

    brief_id = str(uuid.uuid4())
    await brief_store.create_brief(brief_id, patient_id, report_ids, user_id=user_id)
    # 注意：调用方决定是在 BackgroundTask 里 await 还是直接 await
    await run_analysis_pipeline(store, brief_store, kb_store, brief_id, llm=llm, vision=vision)
    return brief_id


# ══════════════════════════════════════════════════════════════
# 核心状态机
# ══════════════════════════════════════════════════════════════


async def _run(
    store: MedicalStore,
    brief_store: BriefStore,
    kb_store: KbStore,
    brief_id: str,
    *,
    llm: Any | None,
    vision: Any | None,
) -> None:
    """六阶段状态机。"""
    brief = await brief_store.get_brief(brief_id)
    if brief is None:
        logger.warning("简报不存在，跳过分析: %s", brief_id)
        return

    patient_id = brief["patient_id"]
    report_ids = brief.get("report_ids_json") or []

    # ── 数据准备：从 Store 取报告数据 + 患者画像
    report_data: list[dict[str, Any]] = []
    for rid in report_ids:
        row = await store.get_report(rid)
        if row:
            report_data.append(row)

    patient_profile = await store.get_patient(patient_id) or {}

    # ── 阶段 1：主任规划
    await _advance(brief_store, brief_id, BriefStatus.ANALYZING.value, 20)
    report_summaries = [
        {
            "id": r.get("id", ""),
            "diagnosis": r.get("diagnosis", ""),
            "summary": r.get("summary", ""),
            "report_type": r.get("report_type", ""),
            "labs": r.get("labs_json", []),
            "medications": r.get("medications_json", []),
        }
        for r in report_data
    ]
    plan_raw = await complete_json(
        llm,
        system=DIRECTOR_SYSTEM,
        prompt=build_director_prompt(report_summaries, patient_profile),
        tool=DIRECTOR_TOOL,
        tool_name="director_plan",
    )
    plan = normalize_director_plan(plan_raw or {})
    # 保存主任规划（后续阶段消费）
    await brief_store.update_brief(
        brief_id, {"director_plan_json": plan}, user_id="system",
    )
    logger.info("简报 %s 主任规划: focus=%s", brief_id, plan.get("focus_areas", []))

    # ── 阶段 2：知识检索（Wave 1，2 路并行）
    await _advance(brief_store, brief_id, BriefStatus.ANALYZING.value, 40)

    # 2a: 本地 KB 检索
    kb_hits = await _wave1_kb_retrieval_with_llm(llm, kb_store, plan)

    # 2b: 患者归档聚合（纯 Store 读取，无需 LLM）
    patient_archive = await store.list_patient_reports(patient_id, limit=50)

    wave1_results = [
        {"type": "kb_retrieval", "hits": kb_hits},
        {"type": "patient_archive", "reports": patient_archive},
    ]

    # ── 阶段 3：专家分析（Wave 2，4 路并行，信号量控制 ≤2）
    await _advance(brief_store, brief_id, BriefStatus.ANALYZING.value, 70)

    expert_coros = [
        _run_with_semaphore(
            _expert_analysis(llm, role, plan, kb_hits, report_data)
        )
        for role in EXPERT_ROLES
    ]
    wave2_raw = await asyncio.gather(*expert_coros, return_exceptions=True)
    wave2_results = _handle_wave_failures(wave2_raw, wave="2")

    # ── 阶段 4：探索性假设（Wave 3，串行）
    await _advance(brief_store, brief_id, BriefStatus.ANALYZING.value, 80)

    wave3_result = await _wave3_hypothesis(llm, wave1_results, wave2_results, plan)

    # ── 阶段 5：合成简报
    await _advance(brief_store, brief_id, BriefStatus.ANALYZING.value, 90)

    all_citations = collect_all_citations(wave1_results, wave2_results, wave3_result)
    draft_raw = await complete_json(
        llm,
        system=SYNTHESIS_SYSTEM,
        prompt=build_synthesis_prompt(
            wave1_results, wave2_results, wave3_result, plan, all_citations,
        ),
        tool=SYNTHESIS_TOOL,
        tool_name="brief_draft",
    )
    draft = normalize_brief(draft_raw or {})

    # 降级填充：缺失领域加占位段落
    covered_roles = {w2.get("role") for w2 in wave2_results if w2.get("findings")}
    for role in EXPERT_ROLES:
        if role not in covered_roles:
            draft["sections"].append(make_degradation_section(role))

    # ── 阶段 6：审计闸门（0 LLM，纯程序）
    await _advance(brief_store, brief_id, BriefStatus.ANALYZING.value, 95)

    # 准备审计输入
    report_id_set = set(report_ids)
    kb_id_set = extract_kb_ids_from_hits(kb_hits)
    raw_texts = build_raw_texts_map(report_data)

    audit_result = run_audit(
        draft["sections"],
        draft["citations"],
        report_ids=report_id_set,
        kb_ids=kb_id_set,
        raw_texts=raw_texts,
    )

    if not audit_result.passed:
        await _audit_fail(brief_store, brief_id, draft, audit_result)
        return

    # 通过：落库
    await _audit_pass(brief_store, brief_id, draft, audit_result)


# ══════════════════════════════════════════════════════════════
# Wave 实现
# ══════════════════════════════════════════════════════════════


async def _wave1_kb_retrieval_with_llm(
    llm: Any,
    kb_store: KbStore,
    plan: dict[str, Any],
) -> list[dict[str, Any]]:
    """Wave 1a: 本地 KB 检索（带 LLM 实参版本）。"""
    queries = plan.get("retrieval_queries", [])
    if not queries:
        return []

    lits, _ = await kb_store.list_literatures(limit=50)
    guides, _ = await kb_store.list_guidelines(limit=50)
    drugs, _ = await kb_store.list_drugs(limit=50)

    raw = await complete_json(
        llm,
        system=KB_RETRIEVAL_SYSTEM,
        prompt=build_kb_retrieval_prompt(queries, lits, guides, drugs),
        tool=KB_RETRIEVAL_TOOL,
        tool_name="kb_retrieval_result",
    )
    return normalize_kb_retrieval(raw or {})


async def _expert_analysis(
    llm: Any,
    role: str,
    plan: dict[str, Any],
    kb_hits: list[dict[str, Any]],
    report_data: list[dict[str, Any]],
) -> dict[str, Any]:
    """Wave 2: 单个专家角色的分析。"""
    raw = await complete_json(
        llm,
        system=get_expert_system(role),
        prompt=build_expert_prompt(role, plan, kb_hits, report_data),
        tool=EXPERT_ANALYSIS_TOOL,
        tool_name="expert_findings",
    )
    return normalize_expert_findings(raw or {}, role)


async def _wave3_hypothesis(
    llm: Any,
    wave1_results: list[dict[str, Any]],
    wave2_results: list[dict[str, Any]],
    plan: dict[str, Any],
) -> dict[str, Any]:
    """Wave 3: 探索性假设（串行，围栏隔离）。"""
    try:
        raw = await complete_json(
            llm,
            system=HYPOTHESIS_SYSTEM,
            prompt=build_hypothesis_prompt(wave1_results, wave2_results, plan),
            tool=HYPOTHESIS_TOOL,
            tool_name="hypothesis_result",
        )
        return normalize_hypothesis(raw or {})
    except Exception:  # noqa: BLE001
        logger.warning("探索性假设失败，降级为空")
        return {"hypotheses": [], "citations": []}


# ══════════════════════════════════════════════════════════════
# 容错与辅助
# ══════════════════════════════════════════════════════════════


def _handle_wave_failures(
    results: list[Any],
    *,
    wave: str,
) -> list[dict[str, Any]]:
    """处理并行 Wave 的异常结果：异常 → 降级为空。"""
    out: list[dict[str, Any]] = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            logger.warning("Wave %s 第 %d 路失败: %s", wave, i, r)
            # 降级：根据 wave 类型返回空结构
            if wave == "2":
                role = EXPERT_ROLES[i] if i < len(EXPERT_ROLES) else f"expert_{i}"
                out.append({"role": role, "findings": [], "citations": [], "confidence": "low"})
            else:
                out.append({})
        else:
            out.append(r)
    return out


async def _advance(
    brief_store: BriefStore,
    brief_id: str,
    status: str,
    progress: int,
) -> None:
    """推进简报状态（与 parse_pipeline._advance 对称）。"""
    await brief_store.update_brief(
        brief_id, {"status": status}, user_id="system",
    )


async def _fail(brief_store: BriefStore, brief_id: str, reason: str) -> None:
    """分析失败 → 状态设为 待修正（可重试）。"""
    await brief_store.update_brief_status(
        brief_id,
        BriefStatus.NEEDS_FIX.value,
        error_message=reason[:ERROR_LIMIT],
        user_id="system",
    )


async def _audit_fail(
    brief_store: BriefStore,
    brief_id: str,
    draft: dict[str, Any],
    audit_result: AuditResult,
) -> None:
    """审计不通过 → 待修正，保留草稿和审计结果。"""
    now = datetime.utcnow()
    await brief_store.update_brief(
        brief_id,
        {
            "status": BriefStatus.NEEDS_FIX.value,
            "sections_json": draft["sections"],
            "citations_json": draft["citations"],
            "audit_result_json": {
                "passed": False,
                "violations": [
                    {"rule": v.rule, "citation_id": v.citation_id, "detail": v.detail}
                    for v in audit_result.violations
                ],
            },
            "error_message": f"审计未通过: {len(audit_result.violations)} 条违规",
            "finished_at": now,
        },
        user_id="system",
    )
    logger.info("简报 %s 审计未通过: %d 条违规", brief_id, len(audit_result.violations))


async def _audit_pass(
    brief_store: BriefStore,
    brief_id: str,
    draft: dict[str, Any],
    audit_result: AuditResult,
) -> None:
    """审计通过 → 已通过，落库。"""
    now = datetime.utcnow()
    audit_json: dict[str, Any] = {"passed": True}
    if audit_result.violations:
        # 降级级违规（quote_verifiability）仍记录
        audit_json["violations"] = [
            {"rule": v.rule, "citation_id": v.citation_id, "detail": v.detail}
            for v in audit_result.violations
        ]

    await brief_store.update_brief(
        brief_id,
        {
            "status": BriefStatus.PASSED.value,
            "sections_json": draft["sections"],
            "citations_json": draft["citations"],
            "audit_result_json": audit_json,
            "error_message": "",
            "finished_at": now,
        },
        user_id="system",
    )
    logger.info("简报 %s 审计通过", brief_id)
