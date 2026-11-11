"""配置管理 REST API — 域配置的读写端点。

端点列表:
  GET    /api/config/domains              列出所有域的生效配置
  GET    /api/config/domains/{domain}     读取域生效配置（defaults + override 合并）
  PUT    /api/config/domains/{domain}     保存工作区覆盖
  DELETE /api/config/domains/{domain}     重置全部覆盖
  DELETE /api/config/domains/{domain}/keys/{key_path}  重置单个键路径

所有端点都需要认证（Depends(get_current_user)）——配置可能包含密钥。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from prismclaw.app.auth.auth import get_current_user
from prismclaw.infra.config.resolver import (
    get_effective_config,
    reset_override,
    reset_override_key,
    save_override,
)
from prismclaw.infra.config.merge import validate_domain_name

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class DomainConfigResponse(BaseModel):
    """域生效配置响应体。"""
    domain: str
    workspace_id: str | None = None
    config: dict[str, Any]


class DomainOverrideRequest(BaseModel):
    """域工作区覆盖请求体。"""
    workspace_id: str = "default"
    overrides: dict[str, Any]


class DomainResetRequest(BaseModel):
    """域覆盖重置请求体（DELETE 全量覆盖时用 query param，此模型仅作文档）。"""
    workspace_id: str = "default"


class DomainKeyResetPath(BaseModel):
    """域单键重置路径参数。"""
    key_path: str


# ---------------------------------------------------------------------------
# 域名白名单（仅允许已有默认配置的域）
# ---------------------------------------------------------------------------

def _validate_domain_exists(domain: str) -> str:
    """验证域名存在（有默认配置文件）。"""
    validate_domain_name(domain)
    from prismclaw.infra.config.resolver import _defaults_config_path
    path = _defaults_config_path(domain)
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"Domain config not found: {domain}",
        )
    return domain


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

config_router = APIRouter(prefix="/config", tags=["config"])


# ── 域配置 ────────────────────────────────────────────────────────

@config_router.get("/domains", response_model=list[DomainConfigResponse])
async def list_domain_configs(
    workspace_id: str = "default",
    user_id: str = Depends(get_current_user),
) -> list[DomainConfigResponse]:
    """列出所有域的生效配置。

    域配置现在位于各分包目录下 (backend/src/subpackages/{domain}/configs/{domain}.json)，
    通过扫描分包目录发现所有已注册域。
    """
    from prismclaw.infra.paths import resolve_project_root

    subpkg_dir = resolve_project_root() / "backend" / "src" / "subpackages"
    results: list[DomainConfigResponse] = []
    if subpkg_dir.is_dir():
        for domain_dir in sorted(subpkg_dir.iterdir()):
            if not domain_dir.is_dir():
                continue
            domain = domain_dir.name
            # 跳过 __pycache__ 等非域目录
            if not domain.replace("_", "").isalnum() or domain.startswith("_"):
                continue
            config_file = domain_dir / "configs" / f"{domain}.json"
            if not config_file.is_file():
                continue
            try:
                cfg = get_effective_config(domain, workspace_id=workspace_id, user_id=user_id)
                sanitized = _sanitize_domain_config(cfg)
                results.append(DomainConfigResponse(
                    domain=domain,
                    workspace_id=workspace_id,
                    config=sanitized,
                ))
            except Exception:
                logger.warning("Failed to read config for domain=%s", domain, exc_info=True)
    return results


@config_router.get("/domains/{domain}", response_model=DomainConfigResponse)
async def read_domain_config(
    domain: str,
    workspace_id: str = "default",
    user_id: str = Depends(get_current_user),
) -> DomainConfigResponse:
    """读取域的生效配置（defaults + override 合并）。"""
    _validate_domain_exists(domain)
    cfg = get_effective_config(domain, workspace_id=workspace_id, user_id=user_id)
    sanitized = _sanitize_domain_config(cfg)
    return DomainConfigResponse(
        domain=domain,
        workspace_id=workspace_id,
        config=sanitized,
    )


@config_router.put("/domains/{domain}", response_model=DomainConfigResponse)
async def save_domain_override(
    domain: str,
    payload: DomainOverrideRequest,
    user_id: str = Depends(get_current_user),
) -> DomainConfigResponse:
    """保存工作区覆盖。覆盖写入 {workspace}/.work/configs/{domain}.json。"""
    _validate_domain_exists(domain)
    overrides = payload.overrides
    if not isinstance(overrides, dict):
        raise HTTPException(status_code=400, detail="overrides must be a JSON object")

    save_override(
        domain=domain,
        workspace_id=payload.workspace_id,
        user_id=user_id,
        overrides=overrides,
    )
    # 回读生效配置确认
    cfg = get_effective_config(
        domain,
        workspace_id=payload.workspace_id,
        user_id=user_id,
    )
    sanitized = _sanitize_domain_config(cfg)
    return DomainConfigResponse(
        domain=domain,
        workspace_id=payload.workspace_id,
        config=sanitized,
    )


@config_router.delete("/domains/{domain}", response_model=DomainConfigResponse)
async def reset_domain_override(
    domain: str,
    workspace_id: str = "default",
    user_id: str = Depends(get_current_user),
) -> DomainConfigResponse:
    """重置全部覆盖（删除工作区覆盖文件，恢复到默认配置）。"""
    _validate_domain_exists(domain)
    reset_override(
        domain=domain,
        workspace_id=workspace_id,
        user_id=user_id,
    )
    # 回读生效配置确认
    cfg = get_effective_config(domain, workspace_id=workspace_id, user_id=user_id)
    sanitized = _sanitize_domain_config(cfg)
    return DomainConfigResponse(
        domain=domain,
        workspace_id=workspace_id,
        config=sanitized,
    )


@config_router.delete(
    "/domains/{domain}/keys/{key_path:path}",
    response_model=DomainConfigResponse,
)
async def reset_domain_override_key(
    domain: str,
    key_path: str,
    workspace_id: str = "default",
    user_id: str = Depends(get_current_user),
) -> DomainConfigResponse:
    """重置覆盖中的单个键路径。

    key_path 格式: "search.cnki" 或 "search.qianfan.api_key"
    删除后若覆盖文件只剩 schema_version，则自动删除整个文件。
    """
    _validate_domain_exists(domain)
    if not key_path or not key_path.strip():
        raise HTTPException(status_code=400, detail="key_path is required")

    reset_override_key(
        domain=domain,
        workspace_id=workspace_id,
        user_id=user_id,
        key_path=key_path.strip(),
    )
    # 回读生效配置确认
    cfg = get_effective_config(domain, workspace_id=workspace_id, user_id=user_id)
    sanitized = _sanitize_domain_config(cfg)
    return DomainConfigResponse(
        domain=domain,
        workspace_id=workspace_id,
        config=sanitized,
    )


# ---------------------------------------------------------------------------
# 脱敏工具
# ---------------------------------------------------------------------------

_SENSITIVE_KEY_PATTERNS = frozenset({
    "key", "api_key", "password", "secret", "token", "credential",
})


def _sanitize_domain_config(config: dict[str, Any]) -> dict[str, Any]:
    """脱敏域配置——掩码 API 密钥等敏感值。

    规则:
      - 遍历 api_keys 列表中的每个对象，将 key 字段掩码为 "sk-***...{last4}"
      - password / token 等字段同理
      - 非列表/非字符串值不处理
    """
    import copy
    sanitized = copy.deepcopy(config)
    _mask_sensitive_values(sanitized)
    return sanitized


def _mask_sensitive_values(obj: Any, _depth: int = 0) -> None:
    """递归掩码敏感值（原地修改 dict/list）。"""
    if _depth > 10:
        return  # 防止无限递归
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and k in _SENSITIVE_KEY_PATTERNS:
                obj[k] = _mask_string(v)
            elif isinstance(v, (dict, list)):
                _mask_sensitive_values(v, _depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                _mask_sensitive_values(item, _depth + 1)


def _mask_string(value: str) -> str:
    """掩码字符串：保留前 3 和后 4 字符，中间用 *** 替代。"""
    if len(value) <= 8:
        return "***"
    return f"{value[:3]}***{value[-4:]}"



