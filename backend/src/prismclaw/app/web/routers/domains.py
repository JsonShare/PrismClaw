"""域模块 API — 前端菜单/页面描述 + 加载状态查询。"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/domains", tags=["domains"])


@router.get("/manifests")
async def get_domain_manifests(request: Request) -> list[dict[str, Any]]:
    """返回所有已加载域的前端描述（菜单、页面、图标）。"""
    bundles = getattr(request.app.state, "domain_bundles", None) or []
    manifests: list[dict[str, Any]] = []
    for bundle in bundles:
        try:
            manifest = bundle.get_manifest()
            manifests.append({
                "key": manifest.key,
                "label": manifest.label,
                "icon": manifest.icon,
                "path": getattr(manifest, "path", ""),
                "menus": [
                    {
                        "key": m.key,
                        "label": m.label,
                        "icon": getattr(m, "icon", ""),
                        "parent": getattr(m, "parent", ""),
                    }
                    for m in manifest.menus
                ],
                "pages": [
                    {"key": p.key, "path": p.path, "js_asset": p.js_asset, "css_asset": p.css_asset}
                    for p in manifest.pages
                ],
            })
        except Exception:
            logger.exception("Failed to serialize domain manifest for bundle %s", bundle)
    return manifests


@router.get("/status")
async def get_domain_status(request: Request) -> dict[str, str]:
    """返回域加载状态（含错误信息）。"""
    registry = getattr(request.app.state, "domain_registry", None)
    if registry is None:
        return {}
    return registry.errors
