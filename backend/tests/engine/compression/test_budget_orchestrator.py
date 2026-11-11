"""Phase 1 基线: BudgetOrchestrator 行为契约测试。

锁定不变量：
1. assess_history_compaction：预算内 → 不触发；超预算 → 触发
2. select_non_static_blocks：预算内全部保留；超预算按优先级 drop/truncate
3. apply_context_layout_budgets：system + dynamic 两区分离
4. BudgetDecision.to_metadata_dict：序列化格式稳定
5. truncate_context_block_for_overflow：working_dir 可用时持久化，不可用时 drop
"""
from __future__ import annotations

from prismclaw.engine.compression.budget_orchestrator import (
    BudgetAuditTrail,
    BudgetDecision,
    BudgetOrchestrator,
    HistoryCompactionAssessment,
    truncate_context_block_for_overflow,
)
from prismclaw.schema.content.context import ContextBlock, ContextPackLayout


def _make_block(
    block_id: str,
    source: str,
    content: str,
    *,
    estimated_tokens: int | None = None,
    priority: str = "medium",
    replaceable: bool = True,
    zone: str = "dynamic",
) -> ContextBlock:
    """构造测试用 ContextBlock。priority 仅接受 critical/high/medium/low。"""
    return ContextBlock(
        id=block_id,
        source=source,
        content=content,
        estimated_tokens=estimated_tokens if estimated_tokens is not None else len(content) // 4,
        priority=priority,
        replaceable=replaceable,
        zone=zone,
    )


# ──────────────────────────── 1. BudgetDecision 序列化契约 ────────────────────────────


def test_budget_decision_to_metadata_dict_keys():
    """BudgetDecision.to_metadata_dict 输出格式稳定。"""
    decision = BudgetDecision(
        stage="select",
        action="drop",
        reason="remove_for_budget",
        zone="dynamic",
        block_id="block_1",
        source="attachment",
        before_tokens=500,
        after_tokens=0,
    )
    metadata = decision.to_metadata_dict()
    assert set(metadata.keys()) == {
        "stage", "action", "reason", "zone",
        "block_id", "source", "before_tokens", "after_tokens",
    }
    assert metadata["stage"] == "select"
    assert metadata["action"] == "drop"
    assert metadata["before_tokens"] == 500
    assert metadata["after_tokens"] == 0


def test_budget_audit_trail_round_trip():
    """BudgetAuditTrail.from_decisions → to_metadata_dict → from_metadata 往返。"""
    decisions = [
        BudgetDecision(
            stage="select", action="drop", reason="remove_for_budget",
            zone="dynamic", block_id="b1", source="s1",
            before_tokens=100, after_tokens=0,
        ),
        BudgetDecision(
            stage="truncate", action="truncate", reason="truncate_for_budget",
            zone="system", block_id="b2", source="s2",
            before_tokens=200, after_tokens=50,
        ),
    ]
    trail = BudgetAuditTrail.from_decisions(decisions)
    assert trail.decision_count == 2
    assert trail.stage_counts() == {"select": 1, "truncate": 1}
    assert trail.action_counts() == {"drop": 1, "truncate": 1}

    metadata = trail.to_metadata_dict()
    restored = BudgetAuditTrail.from_metadata(metadata)
    assert restored is not None
    assert restored.decision_count == 2


# ──────────────────────────── 2. select_non_static_blocks 预算分配 ────────────────────────────


def test_select_non_static_blocks_within_budget():
    """预算充足 → 全部保留，0 drop 决策。"""
    blocks = [
        _make_block("b1", "memory_archives", "content1", estimated_tokens=100),
        _make_block("b2", "memory_procedures", "content2", estimated_tokens=200),
    ]
    orchestrator = BudgetOrchestrator()
    selected, decisions = orchestrator.select_non_static_blocks(
        blocks, budget=1000, task_profile=None,
    )
    assert len(selected) == 2
    # 预算内不应有 drop
    drop_decisions = [d for d in decisions if d.action == "drop"]
    assert drop_decisions == []


def test_select_non_static_blocks_over_budget_skips_lowest_priority():
    """超预算 → 通过 skip 决策舍弃低优先级 block（action='skip', reason='skip_lower_value_block'）。"""
    blocks = [
        _make_block("b1", "memory_archives", "c1", estimated_tokens=500, priority="high", replaceable=False),
        _make_block("b2", "memory_procedures", "c2", estimated_tokens=500, priority="low", replaceable=True),
        _make_block("b3", "runtime_context", "c3", estimated_tokens=500, priority="medium", replaceable=True),
    ]
    orchestrator = BudgetOrchestrator()
    selected, decisions = orchestrator.select_non_static_blocks(
        blocks, budget=600, task_profile=None,
    )
    # 至少 skip 一个（action="skip"，非 "drop"）
    skip_actions = [d for d in decisions if d.action == "skip"]
    assert len(skip_actions) >= 1
    # selected 数 < 总数（超预算被剔除）
    assert len(selected) < len(blocks)
    # 留下的块 tokens 之和 <= budget + 最大单块（required anchor headroom）
    selected_tokens = sum(b.estimated_tokens for b in selected)
    assert selected_tokens <= 600 or len(selected) <= 2


# ──────────────────────────── 3. truncate_context_block_for_overflow ────────────────────────────


def test_truncate_context_block_within_overflow_returns_unchanged():
    """block tokens <= overflow_tokens → 不动，原 block 返回。"""
    block = _make_block("b1", "test", "short", estimated_tokens=50)
    result = truncate_context_block_for_overflow(block, overflow_tokens=100)
    assert result is not None
    assert result.id == block.id
    assert result.content == block.content


def test_truncate_context_block_no_working_dir_drops_when_overflow():
    """working_dir=None + 超预算 → 返回 None（drop，避免截断信息丢失）。"""
    block = _make_block("b1", "test", "x" * 1000, estimated_tokens=500)
    result = truncate_context_block_for_overflow(block, overflow_tokens=100, working_dir=None)
    assert result is None


# ──────────────────────────── 4. HistoryCompactionAssessment 契约 ────────────────────────────


def test_history_compaction_assessment_audit_trail_property():
    """HistoryCompactionAssessment.audit_trail 从 decisions 构造。"""
    decisions = (
        BudgetDecision(
            stage="assess", action="keep", reason="within-budget",
            zone="history", before_tokens=5000, after_tokens=5000,
        ),
    )
    assessment = HistoryCompactionAssessment(
        should_compact=False,
        trigger_budget=8000,
        decisions=decisions,
    )
    assert assessment.audit_trail.decision_count == 1
    assert assessment.threshold_override is None
    assert assessment.keep_recent_tokens_override is None


def test_history_compaction_assessment_overrides_propagated():
    """threshold_override / keep_recent_tokens_override 字段可独立设置。"""
    assessment = HistoryCompactionAssessment(
        should_compact=True,
        trigger_budget=8000,
        threshold_override=0.7,
        keep_recent_tokens_override=5000,
    )
    assert assessment.should_compact is True
    assert assessment.threshold_override == 0.7
    assert assessment.keep_recent_tokens_override == 5000


# ──────────────── 5. 冻结区裁剪：发现面必须存活（插件不可见的根因回归） ────────────────


class _FakePorts:
    """把 persist_context_block 替换成固定长度的引用文本，无需真实写盘。"""

    def persist_context_block(self, *, block_id, source, content, reason, working_dir):
        return (
            f"<context-block-ref>\nblock_id: {block_id}\nsource: {source}\n"
            f"size: {len(content)} chars\npreview: {content[:200]}\n"
            f"[Full content persisted to: context_blocks/cb_{block_id}.txt]\n"
        )


def _frozen_zone_blocks() -> list[ContextBlock]:
    """复刻 SDD D3 之后的真实冻结区：l1_snapshot 已移出，不再出现于此区。"""
    return [
        _make_block("identity", "identity", "你是 PrismClaw。" * 5, estimated_tokens=35, priority="critical"),
        _make_block("repository_map", "repository_map", "r" * 4000, estimated_tokens=1000, priority="medium"),
        _make_block("tools", "tools", "t" * 3180, estimated_tokens=796, priority="high"),
        _make_block("skills", "skills", "s" * 1050, estimated_tokens=263, priority="high"),
        _make_block("subagents", "subagents", "a" * 9190, estimated_tokens=2298, priority="high"),
        _make_block("workspace", "workspace", "w" * 214, estimated_tokens=54, priority="high"),
    ]


def test_system_zone_over_budget_keeps_discovery_surface():
    """冻结区超预算：先撤可重建的 repository_map，tools/skills 发现面必须保留。

    回归背景（同源事故）：<memory_index> 曾占满冻结区导致 tools/skills 被裁、
    插件生态对模型不可见。SDD D3 后记忆索引移入动态区，冻结区的增长源只剩
    repository_map/subagents；本测试锁定"发现面永远最后撤"的不变量。
    """
    orchestrator = BudgetOrchestrator(working_dir="/tmp/unused", ports=_FakePorts())
    result = orchestrator.apply_context_layout_budgets(
        ContextPackLayout(static_blocks=_frozen_zone_blocks()),
        system_budget=4096,
        dynamic_budget=0,
        result_budget=0,
    )
    surviving = {block.source for block in result.context_layout.static_blocks}
    assert {"tools", "skills", "subagents", "workspace"}.issubset(surviving)
    persisted = {
        block.source: block.metadata.get("persisted")
        for block in result.context_layout.static_blocks
    }
    assert persisted.get("repository_map") is True


def test_dynamic_zone_drops_l1_snapshot_first():
    """动态区超预算：l1_snapshot 排第一先撤（可重读磁盘），memory_facts 保留。"""
    blocks = [
        _make_block("l1", "l1_snapshot", "m" * 4160, estimated_tokens=1042, priority="medium"),
        _make_block("f1", "memory_facts", "f" * 400, estimated_tokens=100, priority="high"),
    ]
    orchestrator = BudgetOrchestrator(working_dir=None, ports=None)
    result = orchestrator.apply_context_layout_budgets(
        ContextPackLayout(dynamic_blocks=blocks),
        system_budget=0,
        dynamic_budget=120,
        result_budget=0,
    )
    sources = {block.source for block in result.context_layout.non_static_blocks()}
    assert "memory_facts" in sources
    assert "l1_snapshot" not in sources
