"""Skill management pure ops shared by CLI and Chat handlers (Phase A3-skills)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prismclaw.infra.paths import build_project_capability_layout
from prismclaw.schema.shared import CapabilityKind
from prismclaw.runtime.skills.clawhub_client import ClawHubClient, create_client
from prismclaw.runtime.skills.discovery import discover_skills
from prismclaw.runtime.skills.eligibility import (
    check_any_bins,
    check_bins,
    check_env,
    check_os,
)
from prismclaw.runtime.skills.installer import install_skill
from prismclaw.schema.services.skills import SkillManifest
from prismclaw.runtime.skills.models import ClawHubError, SkillInstallError


@dataclass
class DiscoveredSkill:
    name: str
    emoji: str | None
    description: str
    eligible: bool
    location: str


@dataclass
class HubSearchResult:
    slug: str
    latest_version: str
    description: str


@dataclass
class InstallResult:
    ok: bool
    dest: str | None
    error: str | None = None


@dataclass
class EligibilityReport:
    name: str
    ok: bool
    issues: list[str]


def _eligibility_issues(manifest: SkillManifest) -> list[str]:
    issues: list[str] = []
    req = manifest.requirements
    if req.os and not check_os(req.os):
        issues.append(f"os not in {req.os}")
    if req.bins and not check_bins(req.bins):
        issues.append(f"missing bins: {req.bins}")
    if req.any_bins and not check_any_bins(req.any_bins):
        issues.append(f"no any_bins available from {req.any_bins}")
    if req.env and not check_env(req.env):
        issues.append(f"missing env vars: {req.env}")
    return issues


def _skill_section(settings: Any) -> Any:
    """从完整 Settings 或扁平 SkillSettings 取 skills section。"""
    nested = getattr(settings, "skills", None)
    return nested if nested is not None else settings


def _workspace_skill_locations(
    workspace_path: Path, settings: Any
) -> tuple[Path, ...]:
    """以 workspace 为根，经唯一 layout builder 展开 skills 目录（双来源）。

    workspace 级 ``.work/skills``（graduation / ClawHub 安装）与用户级
    ``~/.prismclaw/assets/skills``（repo 同步）都必须进入发现链。
    调用方可能传完整 Settings（字段在 ``.skills`` 下）或直接传 SkillSettings。
    """
    sec = _skill_section(settings)
    layout = build_project_capability_layout(
        primary=Path(workspace_path),
        skills_dir=getattr(sec, "workspace_skills_dir", "") or "",
        extra_skills_dirs=(getattr(sec, "asset_skills_dir", "") or "",),
    )
    return layout.existing_locations_of(CapabilityKind.SKILLS)


def list_discovered(
    workspace_path: Path,
    settings: Any,
) -> list[DiscoveredSkill]:
    manifests = discover_skills(
        project_locations=_workspace_skill_locations(workspace_path, settings),
        settings=settings,
    )
    results: list[DiscoveredSkill] = []
    for m in manifests:
        issues = _eligibility_issues(m)
        results.append(
            DiscoveredSkill(
                name=m.name,
                emoji=m.emoji,
                description=m.description[:200] if m.description else "",
                eligible=len(issues) == 0,
                location=m.file_path,
            )
        )
    return results


async def search_hub(query: str, *, client: ClawHubClient | None = None) -> list[HubSearchResult]:
    if client is None:
        client = await create_client()
    try:
        hits = await client.search(query)
    except ClawHubError as exc:
        raise RuntimeError(f"ClawHub search failed: {exc}") from exc

    results: list[HubSearchResult] = []
    for hit in hits:
        slug = getattr(hit, "slug", "") or ""
        version = getattr(hit, "version", None) or getattr(hit, "latest_version", "") or ""
        description = getattr(hit, "description", "") or ""
        results.append(
            HubSearchResult(
                slug=str(slug),
                latest_version=str(version),
                description=str(description)[:200],
            )
        )
    return results


async def install(
    slug: str,
    version: str | None,
    install_dir: Path,
    *,
    client: ClawHubClient | None = None,
) -> InstallResult:
    if client is None:
        client = await create_client()
    try:
        dest = await install_skill(client, slug, version, install_dir)
        return InstallResult(ok=True, dest=str(dest))
    except (ClawHubError, SkillInstallError) as exc:
        return InstallResult(ok=False, dest=None, error=str(exc))
    except Exception as exc:
        return InstallResult(ok=False, dest=None, error=f"unexpected: {exc}")


def check_eligibility(
    workspace_path: Path,
    settings: Any,
    name: str | None = None,
) -> list[EligibilityReport]:
    manifests = discover_skills(
        project_locations=_workspace_skill_locations(workspace_path, settings),
        settings=settings,
    )
    if name is not None:
        manifests = [m for m in manifests if m.name == name]

    reports: list[EligibilityReport] = []
    for m in manifests:
        issues = _eligibility_issues(m)
        reports.append(EligibilityReport(name=m.name, ok=len(issues) == 0, issues=issues))
    return reports


class DefaultSkillManagement:
    """Skill management facade that wraps the 4 module-level ops into an
    object suitable for injection into ``AgentRunnerDeps.skill_management``.
    """

    def list_discovered(
        self,
        workspace_path: Path,
        settings: Any,
    ) -> list[DiscoveredSkill]:
        return list_discovered(
            workspace_path,
            settings,
        )

    async def search_hub(self, query: str) -> list[HubSearchResult]:
        return await search_hub(query)

    async def install(
        self,
        slug: str,
        version: str | None,
        install_dir: Path,
    ) -> InstallResult:
        return await install(slug, version, install_dir)

    def check_eligibility(
        self,
        workspace_path: Path,
        settings: Any,
        name: str | None = None,
    ) -> list[EligibilityReport]:
        return check_eligibility(
            workspace_path,
            settings,
            name=name,
        )
