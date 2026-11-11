from __future__ import annotations

import json
import logging
import uuid
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from prismclaw.engine.execution.control.artifacts import ArtifactScanner
from prismclaw.engine.foundation.metrics import emit
from prismclaw.schema.agent.task_runtime import (
    AcceptanceEvaluation,
    EvidenceRecord,
    ReviewDecisionRecord,
    TaskRuntimeState,
)
from prismclaw.schema.agent.task_types import TaskSpec

logger = logging.getLogger(__name__)

CriterionKind = Literal["file_exists", "file_nonempty", "format_parsable", "unverifiable"]
CriterionStatus = Literal["met", "unmet", "unverifiable"]
Enforcement = Literal["advisory", "strict"]

_TEXT_SUFFIXES = frozenset({".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".log", ".html"})
_ZIP_SUFFIXES = frozenset({".xlsx", ".pptx", ".docx", ".odt", ".odp", ".ods"})


@dataclass(frozen=True)
class Criterion:
    """一条可执行的完成要求。target 为 None 时按本次运行的产物集合评估。"""

    kind: CriterionKind
    target: str | None = None

    def describe(self) -> str:
        subject = self.target or "本次运行产出的文件"
        if self.kind == "file_exists":
            return f"存在产物：{subject}"
        if self.kind == "file_nonempty":
            return f"产物非空：{subject}"
        if self.kind == "format_parsable":
            return f"产物可解析：{subject}"
        return f"无法机械核验（不计入缺口）：{subject}"


@dataclass(frozen=True)
class CompletionContract:
    """冻结的完成要求。enforcement=advisory 时缺口只提醒一次，不阻塞。"""

    enforcement: Enforcement
    criteria: tuple[Criterion, ...]
    max_repairs: int = 1


@dataclass(frozen=True)
class CriterionOutcome:
    criterion: Criterion
    status: CriterionStatus
    evidence_ids: tuple[str, ...] = ()

    @property
    def code(self) -> str:
        return {
            "file_exists": "file_missing",
            "file_nonempty": "file_empty",
            "format_parsable": "format_unreadable",
            "unverifiable": "unverifiable",
        }[self.criterion.kind]

    def describe(self) -> str:
        subject = self.criterion.target or "本次运行写出的文件"
        if self.criterion.kind == "file_exists":
            return f"缺少产物：{subject}"
        if self.criterion.kind == "file_nonempty":
            return f"产物为空：{subject}"
        if self.criterion.kind == "format_parsable":
            return f"产物无法解析：{subject}"
        return f"无法机械核验：{subject}"


@dataclass(frozen=True)
class ReviewResult:
    outcomes: tuple[CriterionOutcome, ...]

    @property
    def gaps(self) -> tuple[CriterionOutcome, ...]:
        return tuple(outcome for outcome in self.outcomes if outcome.status == "unmet")

    @property
    def accepted(self) -> bool:
        return not self.gaps

    def describe(self) -> tuple[str, ...]:
        return tuple(gap.describe() for gap in self.gaps)


def _declared_targets(task_spec: TaskSpec) -> tuple[str, ...]:
    probe = task_spec.preflight_probe
    if probe is None:
        return ()
    targets = [
        path
        for path in probe.mentioned_paths
        if isinstance(path, str) and Path(path.replace("\\", "/")).suffix
    ]
    return tuple(dict.fromkeys(targets))


def _file_criteria(target: str, *, with_content: bool) -> tuple[Criterion, ...]:
    criteria = [Criterion("file_exists", target)]
    if with_content:
        criteria.append(Criterion("file_nonempty", target))
        criteria.append(Criterion("format_parsable", target))
    return tuple(criteria)


# 看起来是纯问答/解释的 goal 关键词（中英混合）
_DIALOGUE_KEYWORDS = frozenset({
    "?", "？", "怎么", "什么", "为什么", "如何", "哪些", "区别", "对比",
    "解释", "说明", "介绍", "讲讲", "help", "explain", "what", "why", "how",
    "请问", "能否", "可以吗", "是什么",
})

# 看起来要产出文件/改代码的 goal 关键词
_PRODUCTION_KEYWORDS = frozenset({
    "写", "创建", "生成", "做个", "新建", "改", "修改", "更新", "编辑",
    "产出", "出一个", "实现", "fix", "edit", "update", "change",
    "create", "generate", "add", "build", "implement",
})


def _goal_suggests_dialogue(goal: str) -> bool:
    lower = goal.lower()
    return any(kw in lower for kw in _DIALOGUE_KEYWORDS)


def _goal_suggests_production(goal: str) -> bool:
    lower = goal.lower()
    return any(kw in lower for kw in _PRODUCTION_KEYWORDS)


def build_completion_contract(
    task_spec: TaskSpec,
    *,
    max_repairs: int = 1,
) -> CompletionContract | None:
    """goal + declared targets → 契约。

    先判 dialogue（问答/解释），再判 production（要产出），最后看有没有明确
    文件 targets 来决定 strict vs advisory。

    - 有明确文件 targets → strict 契约（edit/create 都一样）
    - 无 targets + 对话类 goal → None（answer/analyze/execute，无机械产物）
    - 无 targets + 生产类 goal → advisory 契约
    - 其他（run/execute 命令等）→ None
    """
    targets = _declared_targets(task_spec)
    goal = task_spec.goal

    if targets:
        # 有明确文件路径引用 → strict 验证
        criteria = tuple(
            criterion
            for target in targets
            for criterion in _file_criteria(target, with_content=True)
        )
        return CompletionContract("strict", criteria, max_repairs=max_repairs)

    # 无 targets：先判 dialogue 再判 production
    if _goal_suggests_dialogue(goal):
        return None
    if _goal_suggests_production(goal):
        return CompletionContract(
            "advisory",
            (Criterion("file_exists"),),
            max_repairs=max_repairs,
        )
    return None


class CompletionReviewer:
    """准则逐条评估器。只读产物事实与证据台账，纯函数式，不写状态。"""

    def __init__(self, scanner: ArtifactScanner) -> None:
        self._scanner = scanner

    def evaluate(
        self,
        contract: CompletionContract | None,
        evidence: Iterable[EvidenceRecord],
    ) -> ReviewResult:
        if contract is None:
            return ReviewResult(())
        records = tuple(record for record in evidence if record.status == "ok")
        wrote_paths = tuple(
            dict.fromkeys(path for record in records for path in record.wrote_paths)
        )
        evidence_ids = tuple(
            dict.fromkeys(record.evidence_id for record in records if record.wrote_paths)
        )
        outcomes = tuple(
            self._evaluate(criterion, wrote_paths, evidence_ids)
            for criterion in contract.criteria
        )
        for outcome in outcomes:
            emit(
                "criterion.status_total",
                1.0,
                kind="counter",
                labels={"kind": outcome.criterion.kind, "status": outcome.status},
            )
        logger.info(
            "completion_review: enforcement=%s outcomes=%s",
            contract.enforcement,
            [(outcome.code, outcome.status) for outcome in outcomes],
        )
        return ReviewResult(outcomes)

    def _evaluate(
        self,
        criterion: Criterion,
        wrote_paths: tuple[str, ...],
        evidence_ids: tuple[str, ...],
    ) -> CriterionOutcome:
        if criterion.kind == "unverifiable":
            return CriterionOutcome(criterion, "unverifiable")
        candidates = (criterion.target,) if criterion.target else wrote_paths
        if not candidates:
            return CriterionOutcome(criterion, "unmet")

        if criterion.kind == "file_exists":
            met = any(self._scanner.inspect(path).exists for path in candidates)
            return CriterionOutcome(criterion, "met" if met else "unmet", evidence_ids if met else ())

        if criterion.kind == "file_nonempty":
            met = any(self._scanner.inspect(path).size > 0 for path in candidates)
            return CriterionOutcome(criterion, "met" if met else "unmet", evidence_ids if met else ())

        verdicts = [self._parsable(path) for path in candidates]
        if any(verdict is True for verdict in verdicts):
            return CriterionOutcome(criterion, "met", evidence_ids)
        if all(verdict is None for verdict in verdicts):
            return CriterionOutcome(criterion, "unverifiable")
        return CriterionOutcome(criterion, "unmet")

    def _parsable(self, path: str) -> bool | None:
        """True/False 是机械判定；None 表示该扩展名无结构探针（不可核）。"""
        resolved = self._scanner.resolve(path)
        suffix = resolved.suffix.lower()
        if not resolved.is_file():
            return False
        if suffix in _ZIP_SUFFIXES:
            return zipfile.is_zipfile(resolved)
        if suffix in _TEXT_SUFFIXES:
            try:
                with resolved.open("rb") as handle:
                    head = handle.read(8192)
                head.decode("utf-8")
            except (OSError, UnicodeDecodeError):
                return False
            if suffix == ".json":
                try:
                    json.loads(resolved.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, ValueError):
                    return False
            return True
        return None


_STATUS_MAP: dict[CriterionStatus, str] = {
    "met": "met",
    "unmet": "unmet",
    "unverifiable": "unknown",
}


def build_review_record(
    *,
    task_runtime: TaskRuntimeState,
    result: ReviewResult,
) -> ReviewDecisionRecord:
    gaps = result.describe()
    reason = (
        "完成申报受理：机械可核对项无缺口。"
        if result.accepted
        else "完成申报未受理：" + "；".join(gaps)
    )
    return ReviewDecisionRecord(
        decision_id=f"{task_runtime.task_id}:review:{uuid.uuid4().hex[:10]}",
        task_id=task_runtime.task_id,
        verdict="complete" if result.accepted else "repair",
        reason=reason,
        evidence_ids=sorted(
            {
                evidence_id
                for outcome in result.outcomes
                if outcome.status == "met"
                for evidence_id in outcome.evidence_ids
            }
        ),
        acceptance_evaluation=[
            AcceptanceEvaluation(
                criterion=outcome.criterion.describe(),
                status=_STATUS_MAP[outcome.status],
                evidence_ids=list(outcome.evidence_ids),
                note=None if outcome.status == "met" else outcome.describe(),
            )
            for outcome in result.outcomes
        ],
    )
