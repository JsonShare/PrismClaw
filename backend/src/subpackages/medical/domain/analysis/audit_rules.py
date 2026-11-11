"""审计闸门规则 — 纯函数，0 LLM，0 IO。

三条规则各一个函数：
1. check_citation_existence   — 引用的 source_id 是否在输入数据中存在（阻断）
2. check_quote_verifiability  — 引文能否在原文中定位（降级，不阻断）
3. check_speculation_fencing  — 推测性假设是否被围栏隔离（阻断）

run_audit 组合三规则，返回 AuditResult(passed, violations)。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AuditViolation:
    """审计违规项。"""
    rule: str          # citation_existence | quote_verifiability | speculation_fencing
    citation_id: str
    detail: str


@dataclass
class AuditResult:
    """审计结果。"""
    passed: bool
    violations: list[AuditViolation] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════
# 规则 1：引用存在性检查（阻断）
# ══════════════════════════════════════════════════════════════


def check_citation_existence(
    citations: list[dict],
    *,
    report_ids: set[str],
    kb_ids: set[str],
) -> list[AuditViolation]:
    """引用的 source_id 是否在输入数据中存在。

    - report_section 类型 → source_id 必须在 report_ids 中
    - kb_* 类型 → source_id 必须在 kb_ids 中
    - speculative → 不检查存在性
    """
    violations: list[AuditViolation] = []
    for cit in citations:
        source_type = cit.get("source_type", "")
        source_id = cit.get("source_id", "")
        citation_id = cit.get("citation_id", "?")

        if source_type == "speculative":
            continue

        if source_type == "report_section":
            if source_id and source_id not in report_ids:
                violations.append(AuditViolation(
                    rule="citation_existence",
                    citation_id=citation_id,
                    detail=f"report_section source_id='{source_id}' 不在报告集合中",
                ))
        elif source_type.startswith("kb_"):
            if source_id and source_id not in kb_ids:
                violations.append(AuditViolation(
                    rule="citation_existence",
                    citation_id=citation_id,
                    detail=f"{source_type} source_id='{source_id}' 不在知识库集合中",
                ))
    return violations


# ══════════════════════════════════════════════════════════════
# 规则 2：引文可验证性检查（降级，不阻断）
# ══════════════════════════════════════════════════════════════


def check_quote_verifiability(
    citations: list[dict],
    *,
    raw_texts: dict[str, str],
) -> list[AuditViolation]:
    """quoted_text 是否能在原始文本中定位。

    截取 quoted_text 前 30 字，在 raw_text 中搜索。
    找不到 → violation（但不阻断，降级为"引用待验证"标记）。
    """
    violations: list[AuditViolation] = []
    for cit in citations:
        source_type = cit.get("source_type", "")
        if source_type != "report_section":
            continue

        source_id = cit.get("source_id", "")
        quoted = cit.get("quoted_text", "")
        citation_id = cit.get("citation_id", "?")

        if not quoted or not source_id:
            continue

        raw = raw_texts.get(source_id, "")
        if not raw:
            continue

        # 截取前 30 字搜索
        probe = quoted[:30].strip()
        if probe and probe not in raw:
            violations.append(AuditViolation(
                rule="quote_verifiability",
                citation_id=citation_id,
                detail=f"quoted_text 前30字在报告 {source_id} 中未找到",
            ))
    return violations


# ══════════════════════════════════════════════════════════════
# 规则 3：推测围栏隔离检查（阻断）
# ══════════════════════════════════════════════════════════════


def check_speculation_fencing(
    sections: list[dict],
    citations: list[dict],
) -> list[AuditViolation]:
    """推测性假设是否被围栏隔离。

    1. is_speculative=true 的段落 → 其所有 citation 的 source_type 必须是 speculative
    2. source_type=speculative 的 citation → 必须只出现在 is_speculative=true 的段落中
    """
    # 建立 citation_id → source_type 映射
    cit_types: dict[str, str] = {}
    for cit in citations:
        cit_types[cit.get("citation_id", "")] = cit.get("source_type", "")

    violations: list[AuditViolation] = []

    # 规则 3a：推测段落只能含 speculative 引用
    for sec in sections:
        if not sec.get("is_speculative"):
            continue
        for cid in sec.get("citation_ids", []):
            st = cit_types.get(cid, "")
            if st and st != "speculative":
                violations.append(AuditViolation(
                    rule="speculation_fencing",
                    citation_id=cid,
                    detail=f"推测性段落引用了非 speculative 引用 (source_type={st})",
                ))

    # 规则 3b：speculative 引用不能出现在非推测段落
    speculative_cids: set[str] = {
        cid for cid, st in cit_types.items() if st == "speculative"
    }
    if speculative_cids:
        for sec in sections:
            if sec.get("is_speculative"):
                continue
            for cid in sec.get("citation_ids", []):
                if cid in speculative_cids:
                    violations.append(AuditViolation(
                        rule="speculation_fencing",
                        citation_id=cid,
                        detail="speculative 引用出现在非推测性段落中",
                    ))

    return violations


# ══════════════════════════════════════════════════════════════
# 组合审计
# ══════════════════════════════════════════════════════════════


def run_audit(
    sections: list[dict],
    citations: list[dict],
    *,
    report_ids: set[str],
    kb_ids: set[str],
    raw_texts: dict[str, str],
) -> AuditResult:
    """运行全部审计规则，返回 AuditResult(passed, violations)。

    阻断条件（任一满足则 passed=False）：
    - citation_existence 发现 1+ 条引用指向不存在的 source_id
    - speculation_fencing 发现 1+ 条推测性引用出现在非推测段落中

    降级条件（passed 仍为 True，但 violation 记入）：
    - quote_verifiability 发现引用原文定位失败
    """
    violations: list[AuditViolation] = []

    # 规则 1（阻断级）
    v_exist = check_citation_existence(citations, report_ids=report_ids, kb_ids=kb_ids)
    violations.extend(v_exist)

    # 规则 2（降级级）
    v_quote = check_quote_verifiability(citations, raw_texts=raw_texts)
    violations.extend(v_quote)

    # 规则 3（阻断级）
    v_fence = check_speculation_fencing(sections, citations)
    violations.extend(v_fence)

    # 判定：citation_existence 和 speculation_fencing 有违规 → 阻断
    blocking = any(
        v.rule in ("citation_existence", "speculation_fencing")
        for v in violations
    )

    return AuditResult(passed=not blocking, violations=violations)
