"""Phase 1 基线: Fact Sheet 注入契约测试。

锁定不变量（重构过程必须持续通过）：
1. load_fact_sheet: 空 tree → 空字典；含 fact_sheet entries → 合并最新值
2. load_fact_sheet: VOID 标记的 key → 从结果中移除
3. save_fact_sheet: 无变更 → 返回空字符串（不追加 entry）
4. render_fact_sheet_text: 空字典 → 空字符串；有事实 → 含 [FACT SHEET] 锚点
5. FactSheetUnavailable 异常：load_fact_sheet 不再 except Exception: pass

Phase 4 重构后此测试必须验证：history_compaction 中删 except pass + 抛 FactSheetUnavailable。
当前 Phase 1 先验证 fact_sheet 模块本身契约。
"""
from __future__ import annotations

import pytest

from prismclaw.engine.compression.fact_sheet import (
    load_fact_sheet,
    render_fact_sheet_text,
    save_fact_sheet,
)
from prismclaw.schema.agent.session import SessionHeader
from prismclaw.schema.session.types import (
    CustomEntry,
    SessionTree,
    generate_entry_id,
)


def _make_tree() -> SessionTree:
    """构造空 SessionTree（需 header 字段）。"""
    return SessionTree(
        header=SessionHeader(id="", workspace_id="", agent_id="")
    )


def _append_fact_sheet_entry(
    tree: SessionTree,
    facts: dict[str, str],
    parent_id: str | None = None,
) -> str:
    """直接向 tree 追加一个 fact_sheet CustomEntry。"""
    existing_ids = set(tree.entries.keys())
    entry_id = generate_entry_id(existing_ids)
    entry = CustomEntry(
        id=entry_id,
        type="custom",
        custom_type="fact_sheet",
        data={"custom_type": "fact_sheet", "facts": facts},
        parent_id=parent_id or tree.leaf_id or "",
    )
    tree.append(entry)
    return entry_id


# ──────────────────────────── 1. load_fact_sheet 基础契约 ────────────────────────────


def test_load_fact_sheet_empty_tree():
    """空 tree → 空 dict。"""
    tree = _make_tree()
    assert load_fact_sheet(tree) == {}


def test_load_fact_sheet_single_entry():
    """单条 fact_sheet entry → 全部加载。"""
    tree = _make_tree()
    _append_fact_sheet_entry(tree, {"通知时间": "周六14:00", "降糖药": "二甲双胍"})
    facts = load_fact_sheet(tree)
    assert facts == {"通知时间": "周六14:00", "降糖药": "二甲双胍"}


def test_load_fact_sheet_multiple_entries_merge_latest():
    """多条 fact_sheet entry → 后者覆盖前者（最新值）。"""
    tree = _make_tree()
    _append_fact_sheet_entry(tree, {"通知时间": "周六14:00", "降脂药": "辛伐他汀"})
    _append_fact_sheet_entry(tree, {"通知时间": "周日10:00"})  # 更新通知时间
    facts = load_fact_sheet(tree)
    assert facts["通知时间"] == "周日10:00"  # 最新值
    assert facts["降脂药"] == "辛伐他汀"  # 保留


def test_load_fact_sheet_void_key_removed():
    """VOID 标记的 key → 从结果中移除。"""
    tree = _make_tree()
    _append_fact_sheet_entry(tree, {"通知时间": "周六14:00", "降糖药": "二甲双胍"})
    _append_fact_sheet_entry(tree, {"降糖药": "VOID"})  # 作废降糖药
    facts = load_fact_sheet(tree)
    assert "降糖药" not in facts
    assert facts["通知时间"] == "周六14:00"


# ──────────────────────────── 2. save_fact_sheet 增量契约 ────────────────────────────


def test_save_fact_sheet_no_change_returns_empty():
    """无变更 → 返回空字符串，不追加 entry。"""
    tree = _make_tree()
    _append_fact_sheet_entry(tree, {"通知时间": "周六14:00"})
    initial_count = len(tree.entries)

    result = save_fact_sheet(tree, {"通知时间": "周六14:00"})
    assert result == ""
    assert len(tree.entries) == initial_count  # 没追加


def test_save_fact_sheet_diff_appended():
    """有变更 → 追加 diff entry，返回 entry_id。"""
    tree = _make_tree()
    _append_fact_sheet_entry(tree, {"通知时间": "周六14:00"})
    initial_count = len(tree.entries)

    # 新增一个 key + 更新一个 key
    entry_id = save_fact_sheet(tree, {"通知时间": "周日10:00", "降糖药": "二甲双胍"})
    assert entry_id != ""
    assert len(tree.entries) == initial_count + 1

    # 合并后应反映最新值
    facts = load_fact_sheet(tree)
    assert facts["通知时间"] == "周日10:00"
    assert facts["降糖药"] == "二甲双胍"


def test_save_fact_sheet_removed_key_voided():
    """删除一个 key → diff entry 中标记为 VOID。"""
    tree = _make_tree()
    _append_fact_sheet_entry(tree, {"通知时间": "周六14:00", "降糖药": "二甲双胍"})

    # 只保留通知时间，降糖药应被 VOID
    save_fact_sheet(tree, {"通知时间": "周六14:00"})
    facts = load_fact_sheet(tree)
    assert "降糖药" not in facts
    assert facts["通知时间"] == "周六14:00"


# ──────────────────────────── 3. render_fact_sheet_text 契约 ────────────────────────────


def test_render_fact_sheet_empty_returns_empty_string():
    """空 facts → 空字符串（不注入）。"""
    assert render_fact_sheet_text({}) == ""


def test_render_fact_sheet_contains_anchor():
    """有 facts → 输出含 [FACT SHEET] 锚点，每个 key-value 都在内。"""
    facts = {"通知时间": "周六14:00", "降糖药": "二甲双胍"}
    text = render_fact_sheet_text(facts)
    assert "[FACT SHEET" in text
    assert "通知时间" in text
    assert "周六14:00" in text
    assert "降糖药" in text
    assert "二甲双胍" in text


# ──────────────────────────── 4. Phase 4 后置契约：FactSheetUnavailable 异常 ────────────────────────────
#
# Phase 1 阶段 fact_sheet.py 尚未引入 FactSheetUnavailable 异常。
# Phase 4 修复契约后，此测试将启用。当前 xfail 标记，避免阻塞基线。


def test_fact_sheet_unavailable_exception_will_be_raised_after_phase4():
    """Phase 4 后：load_fact_sheet 不再 except Exception: pass，应抛 FactSheetUnavailable。
    当前阶段此异常尚不存在 → xfail。"""
    # 预期 Phase 4 后这段代码可用：
    # from prismclaw.engine.compression.fact_sheet import FactSheetUnavailable
    # with pytest.raises(FactSheetUnavailable):
    #     load_fact_sheet(broken_tree)

    # Phase 1 验证现状：load_fact_sheet 对损坏 tree 不会抛 FactSheetUnavailable
    # （因为该异常类尚未定义）
    try:
        from prismclaw.engine.compression.fact_sheet import FactSheetUnavailable  # type: ignore
        pytest.fail("Phase 4 重构后此测试应启用")
    except ImportError:
        # Phase 1 现状：异常类未定义，测试 xfail
        pytest.skip("Phase 4 后启用：FactSheetUnavailable 尚未引入")

