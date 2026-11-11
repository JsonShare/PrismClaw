"""完成准则契约：goal + 声明的 targets → 冻结准则，逐条机械评估，无法核验不阻塞。"""
from __future__ import annotations

from pathlib import Path

from prismclaw.engine.execution.control.artifacts import ArtifactScanner
from prismclaw.engine.execution.control.completion import (
    CompletionContract,
    CompletionReviewer,
    Criterion,
    build_completion_contract,
    build_review_record,
)
from prismclaw.schema.agent.task_runtime import EvidenceRecord, PreflightProbeResult, TaskRuntimeState
from prismclaw.schema.agent.task_types import TaskSpec


def _spec(*, goal: str, declared: tuple[str, ...] = ()) -> TaskSpec:
    probe = None
    if declared:
        probe = PreflightProbeResult(
            probe_id="pfp_test",
            mentioned_paths=list(declared),
            summary="测试预检。",
        )
    return TaskSpec(
        task_id="s:t1",
        session_id="s1",
        goal=goal,
        preflight_probe=probe,
    )


def _evidence(*paths: str, status: str = "ok") -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id="ev-1",
        task_id="s:t1",
        tool_name="write",
        status=status,
        wrote_paths=list(paths),
    )


def _reviewer(root: Path) -> CompletionReviewer:
    return CompletionReviewer(ArtifactScanner(root))


def _runtime() -> TaskRuntimeState:
    return TaskRuntimeState(task_id="s:t1", session_id="s1", state="reviewing")


# ── 契约构建分支测试 ──────────────────────────────────────────────────────────


def test_contract_none_for_dialogue_goals():
    """对话类 goal（问答/解释）→ 无机械契约。"""
    for goal in (
        "解释一下什么是 transformer",
        "怎么写测试？",
        "这两个方案有什么区别？",
        "帮我分析一下这个报错",
    ):
        assert build_completion_contract(_spec(goal=goal)) is None, goal


def test_dialogue_like_short_goal_without_keyword_still_none():
    """无 production 关键词、无 targets 的短 goal → 无机械契约。"""
    assert build_completion_contract(_spec(goal="好的")) is None
    assert build_completion_contract(_spec(goal="帮我看看")) is None


def test_production_goal_without_targets_is_advisory():
    """有 production 关键词、无 targets → advisory（只能验证"写了啥"）。"""
    contract = build_completion_contract(_spec(goal="产出季度报告"))
    assert contract is not None
    assert contract.enforcement == "advisory"
    assert len(contract.criteria) == 1
    assert contract.criteria[0].kind == "file_exists"
    assert contract.criteria[0].target is None


def test_production_goal_with_declared_targets_is_strict():
    """有明确文件 targets → strict + 完整三准则。"""
    contract = build_completion_contract(_spec(goal="产出季度报告", declared=("out.md",)))
    assert contract is not None
    assert contract.enforcement == "strict"
    assert [criterion.kind for criterion in contract.criteria] == [
        "file_exists",
        "file_nonempty",
        "format_parsable",
    ]
    assert {criterion.target for criterion in contract.criteria} == {"out.md"}


def test_edit_goal_with_declared_targets_is_strict():
    """edit 类 goal + declared targets → 同样 strict 完整三准则（edit/create 不再区分）。"""
    contract = build_completion_contract(_spec(goal="修改 README.md 加一句", declared=("README.md",)))
    assert contract is not None
    assert contract.enforcement == "strict"
    assert len(contract.criteria) == 3
    assert all(c.target == "README.md" for c in contract.criteria)


# ── 评估集成测试 ──────────────────────────────────────────────────────────────


def test_advisory_contract_without_evidence_reports_gap(tmp_path: Path):
    """advisory 契约 + 无产物证据 → 报告缺口（file_exists 未命中）。"""
    contract = build_completion_contract(_spec(goal="产出季度报告"))
    result = _reviewer(tmp_path).evaluate(contract, [])
    assert not result.accepted
    assert "file_missing" in [outcome.code for outcome in result.gaps]


def test_production_with_targets_but_no_reports_file_missing(tmp_path: Path):
    """有 targets 但没产物 → 三条准则全 unmet（文件不存在，后续也无法验证空/可解析）。"""
    contract = build_completion_contract(_spec(goal="产出季度报告", declared=("out.md",)))
    result = _reviewer(tmp_path).evaluate(contract, [])
    assert not result.accepted
    assert [outcome.code for outcome in result.outcomes] == [
        "file_missing",
        "file_empty",
        "format_unreadable",
    ]


def test_targeted_artifact_is_accepted(tmp_path: Path):
    """有 targets + 产物真的存在且非空 → 全 met。"""
    contract = build_completion_contract(_spec(goal="产出季度报告", declared=("out.md",)))
    (tmp_path / "out.md").write_text("# 报告\n结论：完成。\n", encoding="utf-8")
    result = _reviewer(tmp_path).evaluate(contract, [_evidence("out.md")])
    assert result.accepted
    assert all(outcome.status == "met" for outcome in result.outcomes)


def test_empty_target_file_fails_nonempty_criterion(tmp_path: Path):
    """产物存在但空文件 → file_exists met, file_nonempty unmet, format_parsable 也会失败。"""
    contract = build_completion_contract(_spec(goal="产出季度报告", declared=("out.md",)))
    (tmp_path / "out.md").write_text("", encoding="utf-8")
    result = _reviewer(tmp_path).evaluate(contract, [_evidence("out.md")])
    statuses = [outcome.status for outcome in result.outcomes]
    assert statuses[0] == "met"  # file_exists 通过
    assert statuses[1] == "unmet"  # file_nonempty 失败


def test_nonempty_file_satisfies_nonempty_criterion(tmp_path: Path):
    contract = CompletionContract("strict", (Criterion("file_nonempty", "out.md"),))
    (tmp_path / "out.md").write_text("内容", encoding="utf-8")
    result = _reviewer(tmp_path).evaluate(contract, [_evidence("out.md")])
    assert result.accepted


def test_corrupt_json_fails_parsable_criterion(tmp_path: Path):
    contract = CompletionContract("strict", (Criterion("format_parsable", "bad.json"),))
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    result = _reviewer(tmp_path).evaluate(contract, [_evidence("bad.json")])
    assert result.outcomes[0].status == "unmet"
    assert result.outcomes[0].code == "format_unreadable"
    assert not result.accepted


def test_valid_json_satisfies_parsable_criterion(tmp_path: Path):
    contract = CompletionContract("strict", (Criterion("format_parsable", "ok.json"),))
    (tmp_path / "ok.json").write_text('{"a": 1}', encoding="utf-8")
    result = _reviewer(tmp_path).evaluate(contract, [_evidence("ok.json")])
    assert result.accepted


def test_unknown_extension_is_unverifiable_and_does_not_block(tmp_path: Path):
    contract = CompletionContract("strict", (Criterion("format_parsable", "data.bin"),))
    (tmp_path / "data.bin").write_bytes(b"\x00\x01")
    result = _reviewer(tmp_path).evaluate(contract, [_evidence("data.bin")])
    assert result.outcomes[0].status == "unverifiable"
    assert result.accepted is True


def test_unverifiable_criterion_never_blocks(tmp_path: Path):
    contract = CompletionContract("strict", (Criterion("unverifiable", "见文档"),))
    result = _reviewer(tmp_path).evaluate(contract, [])
    assert result.outcomes[0].status == "unverifiable"
    assert result.accepted is True


def test_advisory_contract_gaps_are_reported_not_swallowed(tmp_path: Path):
    contract = build_completion_contract(_spec(goal="产出季度报告"))
    result = _reviewer(tmp_path).evaluate(contract, [])
    assert result.accepted is False
    assert len(result.gaps) == 1


def test_review_record_maps_every_criterion_status(tmp_path: Path):
    (tmp_path / "out.md").write_text("内容", encoding="utf-8")
    contract = build_completion_contract(_spec(goal="产出季度报告", declared=("out.md",)))
    accepted = build_review_record(
        task_runtime=_runtime(),
        result=_reviewer(tmp_path).evaluate(contract, [_evidence("out.md")]),
    )
    assert accepted.verdict == "complete"
    assert len(accepted.acceptance_evaluation) == 3
    assert [entry.status for entry in accepted.acceptance_evaluation] == ["met", "met", "met"]
    assert all(entry.note is None for entry in accepted.acceptance_evaluation)


def test_review_record_marks_unmet_and_unknown_with_reason(tmp_path: Path):
    contract = CompletionContract(
        "strict",
        (Criterion("unverifiable", "见文档"), Criterion("file_exists", "missing.md")),
    )
    record = build_review_record(
        task_runtime=_runtime(),
        result=_reviewer(tmp_path).evaluate(contract, []),
    )
    assert record.verdict == "repair"
    assert [entry.status for entry in record.acceptance_evaluation] == ["unknown", "unmet"]
    assert "缺少产物：missing.md" in record.reason
