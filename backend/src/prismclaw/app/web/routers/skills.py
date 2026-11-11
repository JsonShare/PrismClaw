"""Skills API routes – extracted from routes.py (Phase 3 router split)."""

from __future__ import annotations

import io
import json
import logging
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from prismclaw.app.auth.auth import get_current_user
from prismclaw.app.web.deps import get_web_deps
from prismclaw.app.web._routes_helpers import (
    _clean_workspace_id,
    _resolve_workspace_root,
)
from prismclaw.schema.agent.domains import SkillSource
from prismclaw.schema.services.skills import SkillManifest, normalize_skill_ref
from prismclaw.runtime.skills.editor import (
    EditFailed,
    EditForbidden,
    SkillEditError,
    SkillEditor,
    SkillNotFound,
)


logger = logging.getLogger(__name__)


def _get_plugin_trust_fns(web_deps: Any) -> tuple[Any, Any]:
    """R1: resolve plugin trust functions from WebDeps."""
    fns = web_deps.plugin_trust_fns
    if fns is None:
        return None, None
    return fns.get("is_source_trusted_for_load"), fns.get("resolve_workspace_trusted")

skills_router = APIRouter(prefix="/skills", tags=["skills"])

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
    enabled: bool = True
    location: str | None = None
    group_path: list[str] = []
    group_label: str | None = None
    category: str | None = None
    workspace_trusted: bool | None = None
    marketplace_name: str | None = None
    marketplace_description: str | None = None
    plugin_description: str | None = None
    invocation_policy: str = "auto"
    editable: bool = False


class InstallSkillRequest(BaseModel):
    url: str
    version: str | None = None


class ToggleSkillRequest(BaseModel):
    name: str
    enabled: bool


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


def _skills_state_file(workspace_root: Path) -> Path:
    return workspace_root / ".work" / "skills_state.json"


def _load_disabled_skills(workspace_root: Path) -> set[str]:
    p = _skills_state_file(workspace_root)
    if not p.is_file():
        return set()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return set()
    disabled = raw.get("disabled") if isinstance(raw, dict) else None
    if not isinstance(disabled, list):
        return set()
    out: set[str] = set()
    for item in disabled:
        if isinstance(item, str) and item.strip():
            out.add(item.strip())
    return out


def _save_disabled_skills(workspace_root: Path, disabled: set[str]) -> None:
    state_file = _skills_state_file(workspace_root)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {"disabled": sorted(disabled)}
    state_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _discard_disabled_skill_names(workspace_root: Path, *names: str | None) -> None:
    disabled = _load_disabled_skills(workspace_root)
    changed = False
    for raw in names:
        name = raw.strip() if isinstance(raw, str) else ""
        if not name:
            continue
        if name in disabled:
            disabled.discard(name)
            changed = True
    if changed:
        _save_disabled_skills(workspace_root, disabled)


def _skill_source(
    manifest: SkillManifest,
    workspace_root: Path,
    settings: Any,
    extension_manager: Any | None = None,
    *,
    web_deps: Any = None,
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
    if origin is SkillSource.PLUGIN:
        return "插件提供"
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
    settings: Any,
    extension_manager: Any | None = None,
    *,
    web_deps: Any = None,
) -> str | None:
    fns = web_deps.skill_origin_fns
    return fns["classify_skill_origin"](
        manifest,
        workspace_root,
        settings,
        extension_manager=extension_manager,
    )


async def _get_workspace_runner_deps(app: Any, *, user_id: str, workspace_root: Path) -> Any | None:
    web_deps = app.state.web_deps
    fn = web_deps.workspace_runner_deps_fn
    if fn is not None:
        return await fn(app, user_id=user_id, workspace_root=workspace_root, wait_for_ready=True)
    return None


async def _list_runtime_skills(
    request: Request,
    *,
    user_id: str,
    workspace_root: Path,
    settings_full: Any,
) -> tuple[list[Any], Any | None]:
    _ = settings_full
    runner_deps = await _get_workspace_runner_deps(
        request.app,
        user_id=user_id,
        workspace_root=workspace_root,
    )
    if runner_deps is None:
        raise RuntimeError(
            "workspace runner deps are required for skill listing"
        )
    extension_manager = getattr(runner_deps, "extension_manager", None) if runner_deps is not None else None
    capability_catalog = (
        getattr(runner_deps, "capability_catalog", None) if runner_deps is not None else None
    )
    if capability_catalog is None:
        raise RuntimeError(
            "workspace runner deps must provide capability_catalog for skill listing"
        )
    return capability_catalog.list_skill_entries(workspace_root), extension_manager


def _invalidate_plugin_runtime_state(app: Any, settings: Any) -> None:
    web_deps = app.state.web_deps
    web_deps.apply_settings_fn(app, settings, invalidate_plugin_runtime=True)


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


@skills_router.get("/list", response_model=list[SkillListItem])
async def list_skills(
    request: Request,
    user_id: str = Depends(get_current_user),
    workspace_id: str = "default",
) -> list[SkillListItem]:
    wid = _clean_workspace_id(workspace_id)
    workspace_root = _resolve_workspace_root(request=request, user_id=user_id, workspace_id=wid)
    web_deps = get_web_deps(request)
    settings_full = web_deps.settings_full
    entries, extension_manager = await _list_runtime_skills(
        request,
        user_id=user_id,
        workspace_root=workspace_root,
        settings_full=settings_full,
    )
    disabled = _load_disabled_skills(workspace_root)

    # Resolve workspace trust state for filtering and display
    workspace_path = getattr(settings_full, "workspaces", None)
    workspace_default = getattr(workspace_path, "default", "~/.prismclaw/workspaces") if workspace_path else "~/.prismclaw/workspaces"
    _is_source_trusted_for_load, _resolve_workspace_trusted = _get_plugin_trust_fns(web_deps)
    workspace_trusted = _resolve_workspace_trusted(workspace_default, settings_full) if _resolve_workspace_trusted else True

    # Origin to plugin source mapping for trust checking.
    # SkillSource.* (Enum 是 str 子类) 也可直接作为 key。
    # "bundled" -> builtin, "personal" -> local, "managed" -> local, "project" -> local, "plugin" -> marketplace
    _ORIGIN_TO_PLUGIN_SOURCE: dict[str, str] = {
        SkillSource.BUNDLED: "builtin",
        SkillSource.WORKSPACE: "builtin",
        SkillSource.BUILTIN: "builtin",
        SkillSource.PERSONAL: "local",
        SkillSource.MANAGED: "local",
        SkillSource.PROJECT: "local",
        SkillSource.PLUGIN: "marketplace",
        SkillSource.MARKETPLACE: "marketplace",
        SkillSource.MCP: "local",
    }

    out: list[SkillListItem] = []
    for entry in entries:
        m = entry.manifest
        origin = _skill_origin(
            m,
            workspace_root,
            settings_full,
            extension_manager=extension_manager,
            web_deps=web_deps,
        )

        # Check if this skill's origin requires trust
        plugin_source = _ORIGIN_TO_PLUGIN_SOURCE.get(origin, "local")
        is_trusted_for_load = _is_source_trusted_for_load(plugin_source, workspace_trusted=workspace_trusted) if _is_source_trusted_for_load else True

        # Filter out skills from untrusted sources in untrusted workspaces
        if not is_trusted_for_load:
            continue

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
                enabled=True if origin is SkillSource.PLUGIN else m.name not in disabled,
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
                workspace_trusted=workspace_trusted,
                marketplace_name=entry.marketplace_name,
                marketplace_description=entry.marketplace_description,
                plugin_description=entry.plugin_description,
                invocation_policy=str(m.invocation_policy.value) if m.invocation_policy is not None else "auto",
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
) -> dict:
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
    disabled = _load_disabled_skills(workspace_root)
    origin = _skill_origin(
        target,
        workspace_root,
        settings_full,
        extension_manager=extension_manager,
        web_deps=web_deps,
    )
    # v5 渐进式披露：插件技能加载时仅读 frontmatter（body=None），
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
        "enabled": True if origin == "plugin" else target.name not in disabled,
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
) -> dict:
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
        raise HTTPException(status_code=400, detail="Invalid URL")

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
        try:
            await client.close()
        except Exception:
            pass

    manifest_name = slug
    try:
        installed_manifest = web_deps.skill_parse_fn(dest / "SKILL.md")
        manifest_name = installed_manifest.name
    except Exception:
        pass
    _discard_disabled_skill_names(workspace_root, slug, manifest_name)
    # Invalidate the cached runner deps so the next request rebuilds the
    # capability catalog with the newly installed skill visible.
    _invalidate_plugin_runtime_state(request.app, web_deps.settings_full)

    return {"ok": True, "name": slug, "dest": str(dest)}


@skills_router.post("/upload")
async def upload_skill(
    request: Request,
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user),
    workspace_id: str = "default",
) -> dict:
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
        _discard_disabled_skill_names(workspace_root, manifest.name)
        _invalidate_plugin_runtime_state(request.app, web_deps.settings_full)
        return {"ok": True, "name": manifest.name, "dest": str(dest)}

    if lower_name.endswith(".zip"):
        tmp_dir = Path(tempfile.mkdtemp())
        try:
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    _safe_extract_zip(zf, tmp_dir)
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid zip file")

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
        _discard_disabled_skill_names(workspace_root, manifest.name)
        _invalidate_plugin_runtime_state(request.app, web_deps.settings_full)
        return {"ok": True, "name": manifest.name, "dest": str(dest)}

    raise HTTPException(status_code=400, detail="Only .zip or .md is supported")


@skills_router.post("/toggle", response_model=SimpleOkResponse)
async def toggle_skill(
    payload: ToggleSkillRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
    workspace_id: str = "default",
) -> SimpleOkResponse:
    wid = _clean_workspace_id(workspace_id)
    workspace_root = _resolve_workspace_root(request=request, user_id=user_id, workspace_id=wid)
    name = normalize_skill_ref((payload.name or "").strip())
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    web_deps = get_web_deps(request)
    settings_full = web_deps.settings_full
    entries, extension_manager = await _list_runtime_skills(
        request,
        user_id=user_id,
        workspace_root=workspace_root,
        settings_full=settings_full,
    )
    manifests = [e.manifest for e in entries]
    target = next((m for m in manifests if m.name == name), None)
    if target is not None and _skill_origin(
        target,
        workspace_root,
        settings_full,
        extension_manager=extension_manager,
        web_deps=web_deps,
    ) == "plugin":
        if payload.enabled:
            _discard_disabled_skill_names(workspace_root, name)
            return SimpleOkResponse()
        raise HTTPException(status_code=403, detail="Cannot disable plugin-provided skills")
    disabled = _load_disabled_skills(workspace_root)
    if payload.enabled:
        disabled.discard(name)
    else:
        disabled.add(name)
    _save_disabled_skills(workspace_root, disabled)
    return SimpleOkResponse()


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
    if origin is SkillSource.BUNDLED or origin is SkillSource.WORKSPACE or origin is SkillSource.BUILTIN:
        raise HTTPException(status_code=403, detail="Cannot delete bundled skills")
    if origin is SkillSource.PLUGIN:
        raise HTTPException(status_code=403, detail="Cannot delete plugin-provided skills")
    try:
        mp = Path(target.file_path).expanduser().resolve()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid skill path")
    skill_dir = mp.parent
    allowed_roots: list[Path] = []
    try:
        allowed_roots.append(workspace_root.expanduser().resolve())
    except Exception:
        pass
    if not any(r in skill_dir.parents for r in allowed_roots if r):
        raise HTTPException(status_code=403, detail="Delete not allowed for this skill")
    try:
        shutil.rmtree(skill_dir)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to delete skill")
    disabled = _load_disabled_skills(workspace_root)
    disabled.discard(skill_name)
    _save_disabled_skills(workspace_root, disabled)
    _invalidate_plugin_runtime_state(request.app, settings_full)
    return SimpleOkResponse()


def _build_skill_editor(
    request: Request,
    workspace_root: Path,
    settings_full: Any,
    extension_manager: Any,
) -> SkillEditor:
    web_deps = get_web_deps(request)
    origin_fns = web_deps.skill_origin_fns or {}

    def _invalidate() -> None:
        _invalidate_plugin_runtime_state(request.app, settings_full)

    # Project root is the authoritative anchor set at lifespan startup
    # (app.state.project_root); project- and plugin-provided skills live under
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
    settings_full: Any,
    skill_name: str,
) -> tuple[Any, Any]:
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
) -> dict:
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
    except EditForbidden as exc:
        raise HTTPException(status_code=403, detail=exc.message)
    except SkillNotFound as exc:
        raise HTTPException(status_code=404, detail=exc.message)
    except EditFailed as exc:
        raise HTTPException(status_code=500, detail=exc.message)
    except SkillEditError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
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
) -> dict:
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
    except SkillNotFound as exc:
        raise HTTPException(status_code=404, detail=exc.message)
    except SkillEditError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
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
) -> dict:
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
    except EditForbidden as exc:
        raise HTTPException(status_code=403, detail=exc.message)
    except SkillNotFound as exc:
        raise HTTPException(status_code=404, detail=exc.message)
    except EditFailed as exc:
        raise HTTPException(status_code=500, detail=exc.message)
    except SkillEditError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    return {
        "ok": True,
        "name": result.name,
        "backup_id": result.backup_id,
        "pre_restore_backup_id": result.pre_restore_backup_id,
    }
