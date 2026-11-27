"""已加载专家包的只读索引：构造后不读盘，不提供修改接口。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from prismclaw.engine.experts.codes import ExpertError, ExpertErrorCode
from prismclaw.engine.experts.loading.load_result import ExpertPackage
from prismclaw.schema.services.skills import qualified_skill_id


@dataclass(frozen=True, slots=True)
class ExpertRegistryIssue:
    code: str
    expert_id: str
    message: str


class ExpertRegistry:
    """按目录扫描结果构建的专家包索引；构造后不读盘。"""

    def __init__(
        self,
        packages: Iterable[ExpertPackage],
        *,
        issues: Iterable[ExpertRegistryIssue] = (),
    ) -> None:
        self._packages = tuple(packages)
        self._issues = tuple(issues)

    @property
    def issues(self) -> tuple[ExpertRegistryIssue, ...]:
        return self._issues

    def list_experts(self) -> tuple[ExpertPackage, ...]:
        """返回已加载专家包（保持构造时顺序）。"""
        return self._packages

    def get(self, expert_id: str) -> ExpertPackage | None:
        """按 ``expert.name`` 或 ``expert_id`` 命中专家包；多来源命中视为歧义。"""
        matches = [
            expert
            for expert in self._packages
            if expert_id in (expert.name, expert.expert_id)
        ]
        sources = sorted({expert.source for expert in matches})
        if len(sources) > 1:
            raise ExpertError(
                ExpertErrorCode.EXPERT_ID_AMBIGUOUS,
                f"Expert {expert_id!r} is ambiguous; sources: {', '.join(sources)}.",
                {"expert_id": expert_id, "sources": sources},
            )
        return matches[0] if matches else None

    def issue_for(self, expert_id: str) -> ExpertRegistryIssue | None:
        for issue in self._issues:
            if issue.expert_id == expert_id:
                return issue
        return None

    def known_ids(self, kind: str) -> tuple[str, ...]:
        """返回已加载模块 ID，供 CapabilityPolicyRegistry 校验配置。"""
        if kind in ("expert", "team"):
            return tuple(
                sorted(
                    expert.name
                    for expert in self._packages
                    if (
                        (expert.manifest.expert_type or "agent")
                        if expert.manifest is not None
                        else "agent"
                    )
                    == ("team" if kind == "team" else "agent")
                )
            )
        if kind != "skill":
            return ()
        skills: set[str] = set()
        for expert in self._packages:
            skills.update(qualified_skill_id(expert.name, skill.name) for skill in expert.skills)
            # 全局基础技能以裸名注册（不属于任何专家包），同样允许被勾选。
            skills.update(
                skill.name for skill in getattr(expert, "inherited_skills", []) or []
            )
        return tuple(sorted(skills))
