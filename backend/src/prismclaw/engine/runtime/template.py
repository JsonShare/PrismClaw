"""DEF-08: 沙箱模板定义与构建工具。"""
from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, Field


class SandboxTemplate(BaseModel):
    """沙箱模板定义——预装所有内置依赖的容器镜像。"""

    template_id: str  # e.g. "default-v1"
    display_name: str  # e.g. "默认办公工作区"
    base_image: str  # e.g. "debian:bookworm-slim"
    preinstalled_apt: list[str] = []
    preinstalled_pip: list[str] = []
    preinstalled_npm: list[str] = []
    preinstalled_bins: list[str] = []
    preloaded_mcp_servers: list[str] = []
    size_mb: int = 0
    build_hash: str = ""


class TenantTemplate(BaseModel):
    """机构定制模板——在基础模板上叠加机构专属依赖。"""
    template_id: str
    base_template_id: str
    tenant_id: str
    extra_apt: list[str] = Field(default_factory=list)
    extra_pip: list[str] = Field(default_factory=list)
    extra_npm: list[str] = Field(default_factory=list)
    extra_bins: list[str] = Field(default_factory=list)
    extra_mcp_servers: list[str] = Field(default_factory=list)
    build_hash: str = ""


def compute_build_hash(template: SandboxTemplate) -> str:
    """基于模板内容计算确定性哈希。"""
    content = (
        template.base_image
        + ",".join(template.preinstalled_apt)
        + ",".join(template.preinstalled_pip)
        + ",".join(template.preinstalled_npm)
        + ",".join(template.preinstalled_bins)
    )
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def build_template_from_skills(
    template_id: str,
    display_name: str,
    base_image: str,
    skills: list[Any],
    plugins: list[Any] | None = None,
) -> SandboxTemplate:
    """从 Skill/Plugin 清单聚合依赖，构建模板。"""
    apt_set: set[str] = set()
    pip_set: set[str] = set()
    npm_set: set[str] = set()
    bins_set: set[str] = set()
    mcp_set: set[str] = set()

    for skill in skills:
        reqs = getattr(skill, "requirements", None) or getattr(skill, "skill_requirements", None)
        if reqs is None:
            continue
        for item in getattr(reqs, "apt_packages", []) or []:
            apt_set.add(item)
        for item in getattr(reqs, "pip_packages", []) or []:
            pip_set.add(item)
        for item in getattr(reqs, "npm_packages", []) or []:
            npm_set.add(item)
        for item in getattr(reqs, "binaries", []) or []:
            bins_set.add(item)
        for item in getattr(reqs, "mcp_servers", []) or []:
            mcp_set.add(item)

    if plugins:
        for plugin in plugins:
            reqs = getattr(plugin, "requirements", None)
            if reqs is None:
                continue
            for item in getattr(reqs, "apt_packages", []) or []:
                apt_set.add(item)
            for item in getattr(reqs, "pip_packages", []) or []:
                pip_set.add(item)
            for item in getattr(reqs, "npm_packages", []) or []:
                npm_set.add(item)

    tmpl = SandboxTemplate(
        template_id=template_id,
        display_name=display_name,
        base_image=base_image,
        preinstalled_apt=sorted(apt_set),
        preinstalled_pip=sorted(pip_set),
        preinstalled_npm=sorted(npm_set),
        preinstalled_bins=sorted(bins_set),
        preloaded_mcp_servers=sorted(mcp_set),
    )
    tmpl.build_hash = compute_build_hash(tmpl)
    return tmpl


async def verify_container_template(runtime_client: Any, expected_template: SandboxTemplate) -> bool:
    """验证容器内的已安装包是否与模板规格一致。"""
    if not expected_template.preinstalled_pip:
        return True
    try:
        from prismclaw.schema.content import SandboxTier
        result = await runtime_client.bash(
            "pip3 list --format=json 2>/dev/null || pip list --format=json 2>/dev/null",
            tier=SandboxTier.STANDARD, soft_timeout=10, hard_timeout=15,
            template_id="__default__", workspace_id="",
        )
    except Exception:
        return False
    import json
    try:
        installed = json.loads(result.stdout)
        installed_names = {pkg.get("name", "").lower() for pkg in installed}
    except (json.JSONDecodeError, TypeError):
        return False
    for pkg_spec in expected_template.preinstalled_pip:
        pkg_name = pkg_spec.split("==")[0].split(">=")[0].split("<=")[0].split("~=")[0].strip().lower()
        if pkg_name and pkg_name not in installed_names:
            return False
    return True
