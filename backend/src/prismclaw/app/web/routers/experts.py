"""Read-only expert catalog and session expert selection routes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from prismclaw.app.auth.auth import get_current_user
from prismclaw.app.web.deps import get_web_deps
from prismclaw.engine.experts.codes import ExpertError, ExpertErrorCode
from prismclaw.engine.experts.loading.load_result import ExpertPackage
from prismclaw.engine.experts.manifest.expert_schema import (
    ExpertLocalizedText,
    ExpertMember,
    ExpertPackageManifest,
)
from prismclaw.engine.experts.registry.expert_registry import ExpertRegistry
from prismclaw.engine.experts.selection import select_session_expert
from prismclaw.infra.config.capability_policy import CapabilityPolicyRegistry
from prismclaw.runtime.skills.eligibility import is_user_visible
from prismclaw.schema.services.skills import qualified_skill_id
from prismclaw.schema.shared import _extract_frontmatter
from prismclaw.schema.storage.storage_types import SessionStore

experts_router = APIRouter(tags=["experts"])

_ERROR_STATUSES = {
    ExpertErrorCode.EXPERT_NOT_FOUND: 404,
    ExpertErrorCode.EXPERT_UNAVAILABLE: 409,
    ExpertErrorCode.MODULE_DISABLED: 409,
    ExpertErrorCode.SKILL_SELECTION_LIMIT: 400,
}


class ExpertStateResponse(BaseModel):
    expert_id: str | None
    source: str | None
    selected_skill_ids: list[str]
    # 不可用原因码（EXPERT_UNAVAILABLE / MODULE_DISABLED）；可用时为 None。
    unavailable_reason: str | None = None


class SelectExpertRequest(BaseModel):
    expert_id: str


class ExpertSummaryResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    expert_type: Literal["agent", "team"] = Field(alias="expertType")
    display_name: ExpertLocalizedText = Field(alias="displayName")
    profession: ExpertLocalizedText
    description: str | ExpertLocalizedText
    # 头像的**唯一交付形式**：由本服务提供的 URL，前端只负责附加鉴权参数。
    # 不下发包内相对路径（avatars/expert.png）——那会让前端把相对路径当站点路径
    # 请求，命中 SPA 兜底返回 HTML，头像永远破图。
    avatar_url: str = Field(alias="avatarUrl")
    members: list[ExpertMember]


class ExpertSkillResponse(BaseModel):
    id: str
    name: str
    description: str


class ExpertDetailResponse(ExpertSummaryResponse):
    skills: list[ExpertSkillResponse]
    prompt: str


def raise_expert_http_error(exc: ExpertError) -> NoReturn:
    status = _ERROR_STATUSES.get(exc.code, 400)
    detail = dict(exc.detail or {})
    detail.update({"code": str(exc.code), "message": exc.message})
    raise HTTPException(status_code=status, detail=detail) from exc


async def verify_session_owner(
    session_store: SessionStore, session_id: str, user_id: str
) -> None:
    tree = await session_store.load(session_id)
    if tree is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if not tree.header.session_key.startswith(f"web:{user_id}:"):
        raise HTTPException(status_code=403, detail="Access denied")


def get_expert_registries(request: Request) -> tuple[ExpertRegistry, CapabilityPolicyRegistry]:
    expert = get_web_deps(request).runner_deps.expert
    builtin = expert.expert_registry
    states = expert.capability_policy_registry
    if builtin is None or states is None:
        raise RuntimeError("expert registries are not configured")
    return builtin, states


def _find_expert(request: Request, expert_id: str) -> ExpertPackage:
    builtin, _states = get_expert_registries(request)
    expert = builtin.get(expert_id)
    if expert is None:
        listed = builtin.issue_for(expert_id) is not None
        code = (
            ExpertErrorCode.EXPERT_UNAVAILABLE
            if listed
            else ExpertErrorCode.EXPERT_NOT_FOUND
        )
        message = (
            "Expert is listed but unavailable."
            if listed
            else f"Expert {expert_id!r} is not deployed."
        )
        raise_expert_http_error(ExpertError(code, message, {"expert_id": expert_id}))
    return expert


def _check_expert_enabled(request: Request, expert: ExpertPackage) -> None:
    _builtin, states = get_expert_registries(request)
    manifest = expert.manifest
    kind = "team" if manifest is not None and manifest.expert_type == "team" else "expert"
    if not states.is_enabled(kind, expert.name):
        raise_expert_http_error(
            ExpertError(
                ExpertErrorCode.MODULE_DISABLED,
                f"Expert {expert.name!r} is disabled.",
                {"expert_id": expert.name},
            )
        )


def _expert_manifest(expert: ExpertPackage) -> ExpertPackageManifest:
    root = expert.root_path or Path(expert.path)
    path = root / ".plugin" / "plugin.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return ExpertPackageManifest.model_validate(raw)
    except Exception:
        raise_expert_http_error(
            ExpertError(
                ExpertErrorCode.EXPERT_UNAVAILABLE,
                "Expert display manifest is unavailable.",
                {"expert_id": expert.name},
            )
        )


def _expert_avatar_url(expert_name: str) -> str:
    """专家头像 URL —— 头像资源的唯一定位方式。

    相对路径（``avatars/expert.png``）在前端无法直接请求：同源相对路径会落到
    站点根，被 SPA 兜底成 index.html。因此展示层一律只传 URL，由前端按既有
    ``?_token=`` 约定附加鉴权（``<img>`` 无法携带 Authorization 头）。
    """
    return f"/api/experts/{expert_name}/avatar"


def _summary(expert: ExpertPackage, manifest: ExpertPackageManifest) -> ExpertSummaryResponse:
    return ExpertSummaryResponse(
        id=expert.name,
        expert_type=manifest.expert_type,
        display_name=manifest.display_name,
        profession=manifest.profession,
        description=manifest.display_description,
        avatar_url=_expert_avatar_url(expert.name),
        members=manifest.members or [],
    )


@experts_router.get("/experts", response_model=list[ExpertSummaryResponse])
async def list_experts(
    request: Request,
    user_id: str = Depends(get_current_user),
) -> list[ExpertSummaryResponse]:
    _ = user_id
    builtin, states = get_expert_registries(request)
    rows = []
    for expert in sorted(builtin.list_experts(), key=lambda item: item.name):
        manifest = expert.manifest
        kind = (
            "team"
            if manifest is not None and manifest.expert_type == "team"
            else "expert"
        )
        if not states.is_enabled(kind, expert.name):
            continue
        rows.append(_summary(expert, _expert_manifest(expert)))
    return rows


@experts_router.get("/experts/{expert_id}", response_model=ExpertDetailResponse)
async def get_expert(
    expert_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> ExpertDetailResponse:
    _ = user_id
    expert = _find_expert(request, expert_id)
    _check_expert_enabled(request, expert)
    manifest = _expert_manifest(expert)
    root = expert.root_path or Path(expert.path)
    try:
        content = (root / "agents" / f"{manifest.agent_name}.md").read_text(
            encoding="utf-8"
        )
        _frontmatter, prompt = _extract_frontmatter(content)
        prompt = prompt.strip()
    except Exception:
        raise_expert_http_error(
            ExpertError(
                ExpertErrorCode.EXPERT_UNAVAILABLE,
                "Expert prompt preview is unavailable.",
                {"expert_id": expert.name},
            )
        )
    skills = [
        ExpertSkillResponse(
            id=qualified_skill_id(expert.name, skill.name),
            name=skill.name,
            description=skill.description,
        )
        for skill in expert.skills
        if is_user_visible(skill)
    ]
    return ExpertDetailResponse(
        **_summary(expert, manifest).model_dump(),
        skills=skills,
        prompt=prompt,
    )


@experts_router.get("/experts/{expert_id}/avatar")
async def get_expert_avatar(
    expert_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> FileResponse:
    """返回专家头像（``manifest.avatar`` 指向的包内文件）。

    这是专家头像的**唯一读取入口**：路径完全由 manifest 决定，不在包内做
    文件名猜测（旧的 ``.plugin/icon.*`` 约定在所有包中都不存在）。鉴权走
    ``get_current_user``，它同时接受 ``Authorization`` 与 ``_token`` 查询参数，
    因此 ``<img src="...?_token=...">`` 可直接使用。
    """
    _ = user_id
    expert = _find_expert(request, expert_id)
    manifest = _expert_manifest(expert)
    root = (expert.root_path or Path(expert.path)).resolve()
    avatar = (root / manifest.avatar).resolve()
    try:
        avatar.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=404, detail="avatar not found") from None
    if not avatar.is_file():
        raise HTTPException(status_code=404, detail="avatar not found")
    return FileResponse(avatar, headers={"Cache-Control": "public, max-age=3600"})


async def _select_expert(
    request: Request,
    session_id: str,
    expert_id: str | None,
    user_id: str,
) -> ExpertStateResponse:
    deps = get_web_deps(request)
    await verify_session_owner(deps.session_store, session_id, user_id)
    builtin, states = get_expert_registries(request)
    try:
        state = await select_session_expert(
            session_id,
            expert_id,
            session_store=deps.session_store,
            expert_registry=builtin,
            capability_policy_registry=states,
        )
    except ExpertError as exc:
        raise_expert_http_error(exc)
    return ExpertStateResponse.model_validate(state)


@experts_router.post(
    "/sessions/{session_id}/expert", response_model=ExpertStateResponse
)
async def select_expert(
    session_id: str,
    payload: SelectExpertRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> ExpertStateResponse:
    return await _select_expert(request, session_id, payload.expert_id, user_id)


@experts_router.delete(
    "/sessions/{session_id}/expert", response_model=ExpertStateResponse
)
async def cancel_expert(
    session_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> ExpertStateResponse:
    return await _select_expert(request, session_id, None, user_id)
