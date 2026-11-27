"""Skills API routes – extracted from routes.py (Phase 3 router split)."""

from __future__ import annotations

import contextlib
import io
import logging
import re
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from prismclaw.app.auth.auth import get_current_user
from prismclaw.app.web._routes_helpers import (
    _clean_workspace_id,
    _resolve_workspace_root,
)
from prismclaw.app.web.deps import get_web_deps, get_web_deps_from_app
from prismclaw.app.web.routers.experts import (
    get_expert_registries,
    raise_expert_http_error,
    verify_session_owner,
)
from prismclaw.engine.execution.capabilities.extension_manager import ExtensionManager
from prismclaw.engine.execution.skills.selection import replace_session_skill_selection
from prismclaw.engine.experts.codes import ExpertError
from prismclaw.engine.experts.selection import get_session_expert_state
from prismclaw.runtime.skills.editor import (
    EditFailedError,
    EditForbiddenError,
    SkillEditError,
    SkillEditor,
    SkillNotFoundError,
)
from prismclaw.runtime.skills.eligibility import is_user_visible
from prismclaw.schema.agent.domains import SkillSource
from prismclaw.schema.services.skills import (
    SkillManifest,
    normalize_skill_ref,
    qualified_skill_id,
)

if TYPE_CHECKING:
    from prismclaw.app.web.deps import WebDeps
    from prismclaw.engine.execution.loop.deps import AgentRunnerDeps
    from prismclaw.infra.settings import Settings

logger = logging.getLogger(__name__)


skills_router = APIRouter(prefix="/skills", tags=["skills"])
session_skills_router = APIRouter(
    prefix="/sessions/{session_id}/skills", tags=["skills"]
)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class SkillListItem(BaseModel):
    name: str
    # 中文显示名；缺失时前端回退到 name。
    name_zh: str | None = None
    description: str = ""
    emoji: str | None = None
    source: str | None = None
    origin: str | None = None
    location: str | None = None
    group_path: list[str] = []
    group_label: str | None = None
    category: str | None = None
    expert_name: str | None = None
    expert_description: str | None = None
    # 所属专家包类型：agent=专家、team=专家团；非专家包来源为 None
    expert_type: str | None = None
    # 所属专家包分组：英文 id + 中文名，供前端分组筛选；非专家包来源为 None
    expert_category_id: str | None = None
    expert_category_name: str | None = None
    editable: bool = False


class CatalogSkillResponse(BaseModel):
    id: str
    name: str
    name_zh: str | None = None
    description: str = ""


class SkillPackageResponse(BaseModel):
    expert_name: str
    expert_description: str | None = None
    skills: list[CatalogSkillResponse]


class ReplaceSessionSkillsRequest(BaseModel):
    selected_skill_ids: list[str]


class SessionSkillSelectionResponse(BaseModel):
    session_id: str
    selected_skill_ids: list[str]


class InstallSkillRequest(BaseModel):
    url: str
    version: str | None = None


class EditSkillRequest(BaseModel):
    description: str | None = None
    body: str | None = None
    fields: dict[str, Any] | None = None


class RestoreSkillRequest(BaseModel):
    backup_id: str


class SimpleOkResponse(BaseModel):
    ok: bool = True


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _skill_source(
    manifest: SkillManifest,
    workspace_root: Path,
    settings: Settings,
    extension_manager: ExtensionManager | None = None,
    *,
    web_deps: WebDeps,
) -> str:
    """展示给前端的本地化 skill 来源说明（中文字符串）。

    classify_skill_origin → SkillSource 枚举,这里再映射成中文标签。
    注:若未来需要国际化,本函数是唯一切点。
    """
    origin = _skill_origin(
        manifest,
        workspace_root,
        settings,
        extension_manager=extension_manager,
        web_deps=web_deps,
    )
    if origin is SkillSource.EXPERT:
        return "专家包提供"
    if origin is SkillSource.MCP:
        return "MCP 提供"
    if origin is SkillSource.PERSONAL:
        return "用户安装"
    if origin is SkillSource.MANAGED:
        return "托管技能"
    if origin is SkillSource.PROJECT:
        return "项目技能"
    # 兜底:bundled / workspace / builtin / 未知来源
    return "系统内置"


def _skill_origin(
    manifest: SkillManifest,
    workspace_root: Path,
    settings: Settings,
    extension_manager: ExtensionManager | None = None,
    *,
    web_deps: WebDeps,
) -> SkillSource | None:
    fns = web_deps.skill_origin_fns
    classifier: Callable[..., SkillSource | None] = fns["classify_skill_origin"]
    return classifier(
        manifest,
        workspace_root,
        settings,
        extension_manager=extension_manager,
    )


async def _get_workspace_runner_deps(
    app: FastAPI, *, user_id: str, workspace_root: Path
) -> AgentRunnerDeps | None:
    web_deps = get_web_deps_from_app(app)
    fn = web_deps.workspace_runner_deps_fn
    if fn is not None:
        return await fn(app, user_id=user_id, workspace_root=workspace_root, wait_for_ready=True)
    return None


async def _list_runtime_skills(
    request: Request,
    *,
    user_id: str,
    workspace_root: Path,
    settings_full: Settings,
) -> tuple[list[Any], ExtensionManager | None]:
    _ = settings_full
    runner_deps = await _get_workspace_runner_deps(
        request.app,
        user_id=user_id,
        workspace_root=workspace_root,
    )
    if runner_deps is None:
        raise RuntimeError("workspace runner deps are required for skill listing")
    raw_extension_manager = runner_deps.expert.extension_manager
    extension_manager = (
        raw_extension_manager if isinstance(raw_extension_manager, ExtensionManager) else None
    )
    capability_catalog = (
        getattr(runner_deps, "capability_catalog", None) if runner_deps is not None else None
    )
    if capability_catalog is None:
        raise RuntimeError(
            "workspace runner deps must provide capability_catalog for skill listing"
        )
    return capability_catalog.list_skill_entries(), extension_manager


def _invalidate_expert_runtime_state(app: FastAPI, settings: Settings) -> None:
    web_deps = app.state.web_deps
    web_deps.apply_settings_fn(app, settings, invalidate_expert_runtime=True)


def _normalize_skill_dir_name(raw: str) -> str:
    base = (raw or "").strip().lower()
    base = re.sub(r"[^a-z0-9_-]+", "-", base)
    base = re.sub(r"-{2,}", "-", base).strip("-")
    return base or "skill"


def _unique_child_dir(parent: Path, base_name: str) -> Path:
    base = _normalize_skill_dir_name(base_name)
    candidate = parent / base
    if not candidate.exists():
        return candidate
    i = 2
    while True:
        candidate = parent / f"{base}-{i}"
        if not candidate.exists():
            return candidate
        i += 1


def _resolve_extracted_skill_root(tmp_root: Path) -> Path:
    direct = tmp_root / "SKILL.md"
    if direct.is_file():
        return tmp_root
    children = [p for p in tmp_root.iterdir() if p.is_dir()]
    if len(children) == 1 and (children[0] / "SKILL.md").is_file():
        return children[0]
    raise HTTPException(status_code=400, detail="Invalid skill package: SKILL.md not found")


def _safe_extract_zip(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extract ``zf`` into ``dest`` rejecting path traversal / absolute members.

    Prevents a malicious archive (``../``, absolute paths, or symlinks escaping
    ``dest``) from writing outside the intended directory — a zip-slip RCE vector.
    """
    base = dest.resolve()
    for name in zf.namelist():
        if name.startswith("/") or ".." in name.split("/"):
            raise HTTPException(status_code=400, detail="Invalid zip: unsafe path")
        target = (base / name).resolve()
        if target != base and base not in target.parents:
            raise HTTPException(status_code=400, detail="Invalid zip: path traversal")
    zf.extractall(dest)


def _is_asset_skill(manifest: SkillManifest, asset_root: Path) -> bool:
    """技能文件是否位于用户级 asset 技能目录下。"""
    if not manifest.file_path:
        return False
    resolved = Path(manifest.file_path).expanduser().resolve()
    return resolved == asset_root or asset_root in resolved.parents


def _ensure_json_path(obj: dict[str, Any], *keys: str) -> dict[str, Any]:
    cur: dict[str, Any] = obj
    for k in keys:
        nxt = cur.get(k)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[k] = nxt
        cur = nxt
    return cur


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


@skills_router.get("", response_model=list[SkillPackageResponse])
async def list_deployed_skills(
    request: Request,
    user_id: str = Depends(get_current_user),
) -> list[SkillPackageResponse]:
    _ = user_id
    builtin, states = get_expert_registries(request)
    experts = sorted(builtin.list_experts(), key=lambda expert: expert.name)
    groups: list[SkillPackageResponse] = []
    for expert in experts:
        if expert.source == "experts":
            manifest = expert.manifest
            kind = "team" if manifest is not None and manifest.expert_type == "team" else "expert"
            if not states.is_enabled(kind, expert.name):
                continue
        skills = []
        for skill in expert.skills:
            if not is_user_visible(skill):
                continue
            skill_id = qualified_skill_id(expert.name, skill.name)
            if not states.is_enabled("skill", skill_id):
                continue
            skills.append(
                CatalogSkillResponse(
                    id=skill_id,
                    name=skill.name,
                    name_zh=skill.name_zh,
                    description=skill.description,
                )
            )
        if skills:
            groups.append(
                SkillPackageResponse(
                    expert_name=expert.name,
                    expert_description=(
                        expert.manifest.description if expert.manifest is not None else None
                    ),
                    skills=skills,
                )
            )

    # 全局基础技能（来自 extra_skill_dirs，expert_name=None）单独成组。
    # 它们是所有专家共享的运行时能力，不属于任何专家包；技能选择列表
    # 只展示这一组，专家自有技能随专家启用，不在此处单列。
    base: dict[str, Any] = {}
    for expert in experts:
        for skill in getattr(expert, "inherited_skills", []) or []:
            if is_user_visible(skill):
                base.setdefault(skill.name, skill)
    if base:
        groups.append(
            SkillPackageResponse(
                expert_name="base",
                expert_description="全局基础技能（所有专家共享）",
                skills=[
                    CatalogSkillResponse(
                        id=skill.name,
                        name=skill.name,
                        name_zh=skill.name_zh,
                        description=skill.description,
                    )
                    for skill in sorted(base.values(), key=lambda item: item.name)
                ],
            )
        )
    return groups


@session_skills_router.put("", response_model=SessionSkillSelectionResponse)
async def replace_session_skills(
    session_id: str,
    payload: ReplaceSessionSkillsRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> SessionSkillSelectionResponse:
    deps = get_web_deps(request)
    await verify_session_owner(deps.session_store, session_id, user_id)
    builtin, states = get_expert_registries(request)
    try:
        header = await replace_session_skill_selection(
            session_id,
            payload.selected_skill_ids,
            session_store=deps.session_store,
            expert_registry=builtin,
            capability_policy_registry=states,
        )
    except ExpertError as exc:
        raise_expert_http_error(exc)
    return SessionSkillSelectionResponse(
        session_id=session_id, selected_skill_ids=header.selected_skill_ids
    )


@session_skills_router.delete(
    "/{skill_id:path}", response_model=SessionSkillSelectionResponse
)
async def remove_session_skill(
    session_id: str,
    skill_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> SessionSkillSelectionResponse:
    deps = get_web_deps(request)
    await verify_session_owner(deps.session_store, session_id, user_id)
    builtin, states = get_expert_registries(request)
    state = await get_session_expert_state(
        session_id,
        session_store=deps.session_store,
        expert_registry=builtin,
        capability_policy_registry=states,
    )
    selected = [item for item in state["selected_skill_ids"] if item != skill_id]
    try:
        header = await replace_session_skill_selection(
            session_id,
            selected,
            session_store=deps.session_store,
            expert_registry=builtin,
            capability_policy_registry=states,
        )
    except ExpertError as exc:
        raise_expert_http_error(exc)
    return SessionSkillSelectionResponse(
        session_id=session_id, selected_skill_ids=header.selected_skill_ids
    )


@skills_router.get("/list", response_model=list[SkillListItem])
async def list_skills(
    request: Request,
    user_id: str = Depends(get_current_user),
    workspace_id: str = "default",
    scope: Literal["assets", "all"] = "assets",
) -> list[SkillListItem]:
    wid = _clean_workspace_id(workspace_id)
    workspace_root = _resolve_workspace_root(request=request, user_id=user_id, workspace_id=wid)
    web_deps = get_web_deps(request)
    settings_full = web_deps.settings_full
    asset_root = (
        Path(settings_full.skills.asset_skills_dir).expanduser().resolve()  # noqa: ASYNC240
    )
    entries, extension_manager = await _list_runtime_skills(
        request,
        user_id=user_id,
        workspace_root=workspace_root,
        settings_full=settings_full,
    )
    # 专家包类型用于前端区分「专家 / 专家团」两个 tab；注册表缺失时降级为 None。
    try:
        expert_registry, _states = get_expert_registries(request)
    except RuntimeError:
        expert_registry = None

    def _manifest_of(name: str | None):
        """取专家包清单；注册表缺失或未命中时返回 None。"""
        if not name or expert_registry is None:
            return None
        try:
            package = expert_registry.get(name)
        except ExpertError:
            return None
        return package.manifest if package is not None else None

    out: list[SkillListItem] = []
    for entry in entries:
        m = entry.manifest
        expert_manifest = _manifest_of(entry.expert_name)
        if not is_user_visible(m):
            continue
        if scope == "assets" and not _is_asset_skill(m, asset_root):
            continue
        origin = _skill_origin(
            m,
            workspace_root,
            settings_full,
            extension_manager=extension_manager,
            web_deps=web_deps,
        )

        origin_fns = web_deps.skill_origin_fns
        out.append(
            SkillListItem(
                name=m.name,
                name_zh=getattr(m, "name_zh", None),
                description=m.description or "",
                emoji=m.emoji,
                source=_skill_source(
                    m,
                    workspace_root,
                    settings_full,
                    extension_manager=extension_manager,
                    web_deps=web_deps,
                ),
                origin=origin,
                location=m.file_path or None,
                group_path=origin_fns["classify_skill_group_path"](
                    m,
                    workspace_root,
                    settings_full,
                    extension_manager=extension_manager,
                ),
                group_label=origin_fns["classify_skill_group_label"](
                    m,
                    workspace_root,
                    settings_full,
                    extension_manager=extension_manager,
                ),
                category=m.category,
                expert_name=entry.expert_name,
                expert_description=entry.expert_description,
                expert_type=(expert_manifest.expert_type if expert_manifest else None),
                expert_category_id=(expert_manifest.category_id if expert_manifest else None),
                expert_category_name=(expert_manifest.category_name if expert_manifest else None),
                editable=bool(m.editable),
            )
        )
    return out


@skills_router.get("/{skill_name}")
async def get_skill(
    skill_name: str,
    request: Request,
    user_id: str = Depends(get_current_user),
    workspace_id: str = "default",
) -> dict[str, Any]:
    wid = _clean_workspace_id(workspace_id)
    skill_name = normalize_skill_ref(skill_name)
    workspace_root = _resolve_workspace_root(request=request, user_id=user_id, workspace_id=wid)
    web_deps = get_web_deps(request)
    settings_full = web_deps.settings_full
    entries, extension_manager = await _list_runtime_skills(
        request,
        user_id=user_id,
        workspace_root=workspace_root,
        settings_full=settings_full,
    )
    manifests = [e.manifest for e in entries]
    target = next((m for m in manifests if m.name == skill_name), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    origin = _skill_origin(
        target,
        workspace_root,
        settings_full,
        extension_manager=extension_manager,
        web_deps=web_deps,
    )
    # v5 渐进式披露：专家包技能加载时仅读 frontmatter（body=None），
    # 此处按需从 file_path 重新读取完整 SKILL.md 内容。API 层不持有
    # SkillRuntime/DescriptorRegistry，无法复用执行层 _ensure_body_loaded
    # 的注册表回写——只读端点读完即弃，直接 parse 即可。
    body = target.body or ""
    if target.body is None and target.file_path:
        try:
            from prismclaw.runtime.skills.parser import parse_skill_file

            full_manifest = parse_skill_file(target.file_path, fallback_name=target.name)
            body = full_manifest.body or ""
        except Exception:
            pass
    return {
        "name": target.name,
        "description": target.description or "",
        "emoji": target.emoji,
        "source": _skill_source(
            target,
            workspace_root,
            settings_full,
            extension_manager=extension_manager,
            web_deps=web_deps,
        ),
        "origin": origin,
        "location": target.file_path or None,
        "body": body,
        "editable": bool(target.editable),
    }


@skills_router.post("/install")
async def install_skill_from_url(
    payload: InstallSkillRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
    workspace_id: str = "default",
) -> dict[str, Any]:
    wid = _clean_workspace_id(workspace_id)
    workspace_root = _resolve_workspace_root(request=request, user_id=user_id, workspace_id=wid)
    web_deps = get_web_deps(request)
    skill_settings = web_deps.settings_full.skills

    raw = (payload.url or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="url required")

    slug = raw
    version = (payload.version or "").strip() or None
    try:
        if "://" in raw:
            from urllib.parse import parse_qs, urlparse

            u = urlparse(raw)
            if "clawhub.ai" not in (u.netloc or ""):
                raise HTTPException(status_code=400, detail="Only clawhub.ai URL is supported")
            parts = [p for p in (u.path or "").split("/") if p]
            if parts:
                slug = parts[-1]
            q = parse_qs(u.query or "")
            if not version:
                v = q.get("version", q.get("v", []))
                if v and isinstance(v, list) and isinstance(v[0], str) and v[0].strip():
                    version = v[0].strip()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid URL") from None

    if "@" in slug and not version:
        maybe_slug, maybe_ver = slug.split("@", 1)
        if maybe_slug.strip():
            slug = maybe_slug.strip()
        if maybe_ver.strip():
            version = maybe_ver.strip()

    if not slug or "/" in slug:
        raise HTTPException(status_code=400, detail="Invalid slug")

    install_dir = workspace_root / skill_settings.workspace_skills_dir
    install_dir.mkdir(parents=True, exist_ok=True)

    client = await web_deps.skill_hub_client_factory(base_url=str(skill_settings.clawhub_base_url))
    try:
        dest = await web_deps.skill_install_fn(client, slug, version, install_dir)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Install failed: {exc}") from exc
    finally:
        with contextlib.suppress(Exception):
            await client.close()

    # Invalidate the cached runner deps so the next request rebuilds the
    # capability catalog with the newly installed skill visible.
    _invalidate_expert_runtime_state(request.app, web_deps.settings_full)

    return {"ok": True, "name": slug, "dest": str(dest)}


@skills_router.post("/upload")
async def upload_skill(
    request: Request,
    file: Annotated[UploadFile, File(...)],
    user_id: Annotated[str, Depends(get_current_user)],
    workspace_id: str = "default",
) -> dict[str, Any]:
    wid = _clean_workspace_id(workspace_id)
    workspace_root = _resolve_workspace_root(request=request, user_id=user_id, workspace_id=wid)
    web_deps = get_web_deps(request)
    skill_settings = web_deps.settings_full.skills

    raw_name = (file.filename or "").strip()
    lower_name = raw_name.lower()
    data = await file.read()

    max_bytes = 30 * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail="File too large (max 30MB)")

    install_dir = workspace_root / skill_settings.workspace_skills_dir
    install_dir.mkdir(parents=True, exist_ok=True)

    if lower_name.endswith(".md"):
        dest = _unique_child_dir(install_dir, Path(raw_name).stem or "skill")
        dest.mkdir(parents=True, exist_ok=False)
        (dest / "SKILL.md").write_bytes(data)
        try:
            manifest = web_deps.skill_parse_fn(dest / "SKILL.md")
        except Exception as exc:
            shutil.rmtree(dest, ignore_errors=True)
            raise HTTPException(status_code=400, detail=f"Invalid SKILL.md: {exc}") from exc
        _invalidate_expert_runtime_state(request.app, web_deps.settings_full)
        return {"ok": True, "name": manifest.name, "dest": str(dest)}

    if lower_name.endswith(".zip"):
        tmp_dir = Path(tempfile.mkdtemp())
        try:
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    _safe_extract_zip(zf, tmp_dir)
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid zip file") from None

            root = _resolve_extracted_skill_root(tmp_dir)
            base_name = root.name or Path(raw_name).stem or "skill"
            dest = _unique_child_dir(install_dir, base_name)
            shutil.copytree(root, dest)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        try:
            manifest = web_deps.skill_parse_fn(dest / "SKILL.md")
        except Exception as exc:
            shutil.rmtree(dest, ignore_errors=True)
            raise HTTPException(status_code=400, detail=f"Invalid SKILL.md: {exc}") from exc
        _invalidate_expert_runtime_state(request.app, web_deps.settings_full)
        return {"ok": True, "name": manifest.name, "dest": str(dest)}

    raise HTTPException(status_code=400, detail="Only .zip or .md is supported")


@skills_router.delete("/{skill_name}", response_model=SimpleOkResponse)
async def delete_skill(
    skill_name: str,
    request: Request,
    user_id: str = Depends(get_current_user),
    workspace_id: str = "default",
) -> SimpleOkResponse:
    wid = _clean_workspace_id(workspace_id)
    skill_name = normalize_skill_ref(skill_name)
    workspace_root = _resolve_workspace_root(request=request, user_id=user_id, workspace_id=wid)
    web_deps = get_web_deps(request)
    settings_full = web_deps.settings_full
    entries, extension_manager = await _list_runtime_skills(
        request,
        user_id=user_id,
        workspace_root=workspace_root,
        settings_full=settings_full,
    )
    manifests = [e.manifest for e in entries]
    target = next((m for m in manifests if m.name == skill_name), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    origin = _skill_origin(
        target,
        workspace_root,
        settings_full,
        extension_manager=extension_manager,
        web_deps=web_deps,
    )
    if (
        origin is SkillSource.BUNDLED
        or origin is SkillSource.WORKSPACE
        or origin is SkillSource.BUILTIN
    ):
        raise HTTPException(status_code=403, detail="Cannot delete bundled skills")
    if origin is SkillSource.EXPERT:
        raise HTTPException(status_code=403, detail="Cannot delete expert-provided skills")
    try:
        mp = Path(target.file_path).expanduser().resolve()  # noqa: ASYNC240 - 纯路径规范化
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid skill path") from None
    skill_dir = mp.parent
    allowed_roots: list[Path] = []
    with contextlib.suppress(Exception):
        allowed_roots.append(workspace_root.expanduser().resolve())
    if not any(r in skill_dir.parents for r in allowed_roots if r):
        raise HTTPException(status_code=403, detail="Delete not allowed for this skill")
    try:
        shutil.rmtree(skill_dir)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to delete skill") from None
    _invalidate_expert_runtime_state(request.app, settings_full)
    return SimpleOkResponse()


def _build_skill_editor(
    request: Request,
    workspace_root: Path,
    settings_full: Settings,
    extension_manager: ExtensionManager | None,
) -> SkillEditor:
    web_deps = get_web_deps(request)
    origin_fns = web_deps.skill_origin_fns or {}

    def _invalidate() -> None:
        _invalidate_expert_runtime_state(request.app, settings_full)

    # Project root is the authoritative anchor set at lifespan startup
    # (app.state.project_root); project- and expert-provided skills live under
    # it, not under the per-user cloud workspace root.
    project_root = getattr(request.app.state, "project_root", None)
    if project_root is None:
        from prismclaw.infra.paths import resolve_project_root

        project_root = resolve_project_root()

    return SkillEditor(
        workspace_root,
        settings_full,
        parse_fn=web_deps.skill_parse_fn,
        classify_origin_fn=origin_fns["classify_skill_origin"],
        extension_manager=extension_manager,
        invalidate_fn=_invalidate,
        project_root=project_root,
    )


async def _resolve_target_manifest(
    request: Request,
    *,
    user_id: str,
    workspace_root: Path,
    settings_full: Settings,
    skill_name: str,
) -> tuple[SkillManifest | None, ExtensionManager | None]:
    skill_name = normalize_skill_ref(skill_name)
    entries, extension_manager = await _list_runtime_skills(
        request,
        user_id=user_id,
        workspace_root=workspace_root,
        settings_full=settings_full,
    )
    target = next((e.manifest for e in entries if e.manifest.name == skill_name), None)
    return target, extension_manager


@skills_router.patch("/{skill_name}")
async def edit_skill(
    skill_name: str,
    payload: EditSkillRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
    workspace_id: str = "default",
) -> dict[str, Any]:
    wid = _clean_workspace_id(workspace_id)
    workspace_root = _resolve_workspace_root(request=request, user_id=user_id, workspace_id=wid)
    web_deps = get_web_deps(request)
    settings_full = web_deps.settings_full
    target, extension_manager = await _resolve_target_manifest(
        request,
        user_id=user_id,
        workspace_root=workspace_root,
        settings_full=settings_full,
        skill_name=skill_name,
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    editor = _build_skill_editor(request, workspace_root, settings_full, extension_manager)
    try:
        result = editor.edit(
            target,
            description=payload.description,
            body=payload.body,
            fields=payload.fields,
        )
    except EditForbiddenError as exc:
        raise HTTPException(status_code=403, detail=exc.message) from exc
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    except EditFailedError as exc:
        raise HTTPException(status_code=500, detail=exc.message) from exc
    except SkillEditError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {
        "ok": True,
        "name": result.name,
        "backup_id": result.backup_id,
        "description": result.description,
    }


@skills_router.get("/{skill_name}/backups")
async def list_skill_backups(
    skill_name: str,
    request: Request,
    user_id: str = Depends(get_current_user),
    workspace_id: str = "default",
) -> dict[str, Any]:
    wid = _clean_workspace_id(workspace_id)
    workspace_root = _resolve_workspace_root(request=request, user_id=user_id, workspace_id=wid)
    web_deps = get_web_deps(request)
    settings_full = web_deps.settings_full
    target, extension_manager = await _resolve_target_manifest(
        request,
        user_id=user_id,
        workspace_root=workspace_root,
        settings_full=settings_full,
        skill_name=skill_name,
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    editor = _build_skill_editor(request, workspace_root, settings_full, extension_manager)
    try:
        entries = editor.backups(target)
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    except SkillEditError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {
        "ok": True,
        "name": skill_name,
        "backups": [e.to_dict() for e in entries],
    }


@skills_router.post("/{skill_name}/restore")
async def restore_skill(
    skill_name: str,
    payload: RestoreSkillRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
    workspace_id: str = "default",
) -> dict[str, Any]:
    wid = _clean_workspace_id(workspace_id)
    workspace_root = _resolve_workspace_root(request=request, user_id=user_id, workspace_id=wid)
    web_deps = get_web_deps(request)
    settings_full = web_deps.settings_full
    target, extension_manager = await _resolve_target_manifest(
        request,
        user_id=user_id,
        workspace_root=workspace_root,
        settings_full=settings_full,
        skill_name=skill_name,
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    editor = _build_skill_editor(request, workspace_root, settings_full, extension_manager)
    try:
        result = editor.restore(target, payload.backup_id)
    except EditForbiddenError as exc:
        raise HTTPException(status_code=403, detail=exc.message) from exc
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    except EditFailedError as exc:
        raise HTTPException(status_code=500, detail=exc.message) from exc
    except SkillEditError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {
        "ok": True,
        "name": result.name,
        "backup_id": result.backup_id,
        "pre_restore_backup_id": result.pre_restore_backup_id,
    }
