"""DEF-08: 内置模板注册表。

TemplateRegistry 是全局单例，存储 template_id → (image_tag, SandboxTemplate) 映射。
在服务启动时（load_skills 完成后）由 build_builtin_templates() 填充。
PreWarmPool 通过此注册表获取 image_tag，解决启动时序问题。
"""
from __future__ import annotations

from typing import Any

from prismclaw.engine.runtime.template import SandboxTemplate


class TemplateRegistry:
    """模板注册表——存储 template_id → (image_tag, SandboxTemplate) 映射。

    在服务启动时（load_skills 完成后）由 build_builtin_templates() 填充。
    PreWarmPool 通过此注册表获取 image_tag。
    """

    def __init__(self) -> None:
        self._templates: dict[str, SandboxTemplate] = {}
        self._image_tags: dict[str, str] = {}

    def register(self, template: SandboxTemplate, image_tag: str) -> None:
        self._templates[template.template_id] = template
        self._image_tags[template.template_id] = image_tag

    def get_template(self, template_id: str) -> SandboxTemplate | None:
        return self._templates.get(template_id)

    def get_image_tag(self, template_id: str) -> str | None:
        return self._image_tags.get(template_id)

    def all_templates(self) -> list[SandboxTemplate]:
        return list(self._templates.values())


# ── 内置模板定义 ────────────────────────────────────────────────────

_DEFAULT_BASE_IMAGE = "debian:bookworm-slim"


def build_builtin_templates(
    skills_by_template: dict[str, list[Any]] | None = None,
) -> dict[str, SandboxTemplate]:
    """构造内置模板集合。

    skills_by_template: template_id → [Skill, ...] 的映射。
    如果为 None，使用默认空依赖列表。

    基础设施模板（default-v1 / restricted-v1）不可删除。
    域特定模板通过 register_domain_template() 注册，由对应 DomainBundle
    在 on_build_runtime() 中完成。
    """
    if skills_by_template is None:
        skills_by_template = {}

    templates: dict[str, SandboxTemplate] = {}

    from prismclaw.engine.runtime.template import build_template_from_skills

    # default-v1: 通用场景（基础设施，不可删除）
    default_skills = skills_by_template.get("default-v1", [])
    default_tmpl = build_template_from_skills(
        template_id="default-v1",
        display_name="Default Template",
        base_image=_DEFAULT_BASE_IMAGE,
        skills=default_skills,
    )
    templates[default_tmpl.template_id] = default_tmpl

    # restricted-v1: 受限场景（基础设施，fail-closed 默认，不可删除）
    restricted_skills = skills_by_template.get("restricted-v1", [])
    restricted_tmpl = build_template_from_skills(
        template_id="restricted-v1",
        display_name="Restricted Template",
        base_image=_DEFAULT_BASE_IMAGE,
        skills=restricted_skills,
    )
    templates[restricted_tmpl.template_id] = restricted_tmpl

    return templates


def register_builtin_templates(
    registry: TemplateRegistry,
    skills_by_template: dict[str, list[Any]] | None = None,
    image_tag_prefix: str = "prismclaw",
) -> None:
    """构造内置模板并注册到全局注册表。"""
    templates = build_builtin_templates(skills_by_template)
    for tmpl in templates.values():
        image_tag = f"{image_tag_prefix}/{tmpl.template_id}:{tmpl.build_hash}"
        registry.register(tmpl, image_tag)


def register_domain_template(
    registry: TemplateRegistry,
    template: SandboxTemplate,
    image_tag_prefix: str = "prismclaw",
) -> None:
    """注册域特定模板到全局注册表。由 DomainBundle 在 on_build_runtime() 中调用。

    不允许覆盖基础设施模板（default-v1 / restricted-v1）。
    """
    if template.template_id in ("default-v1", "restricted-v1"):
        raise ValueError(f"Cannot override infrastructure template: {template.template_id}")
    image_tag = f"{image_tag_prefix}/{template.template_id}:{template.build_hash}"
    registry.register(template, image_tag)


# 全局单例
registry = TemplateRegistry()
