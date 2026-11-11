"""Domain advisory rule registry — core declares extension point, domains register implementations.

Usage:
    from prismclaw.engine.execution.request._disclaimer_registry import (
        DomainAdvisoryRule,
        register_advisory_rule,
        get_advisory_rules,
    )

    # In domain bundle on_build_runtime():
    register_advisory_rule(DomainAdvisoryRule(
        domain="medical",
        title="医疗免责声明（强制）",
        body='当任务涉及医疗信息（药物剂量、禁忌证、治疗建议、诊断标准）时，'
             '必须附加免责声明：'
             '"以上信息仅供参考，不构成医疗建议。具体用药请遵医嘱。" '
             '无论置信度高低都必须附加此声明。',
    ))
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DomainAdvisoryRule:
    """Domain-specific advisory rule registered by domain bundles.

    Rendered as numbered items in the sub-agent advisory section,
    appended after the core evidence & accuracy rules.
    """
    domain: str
    title: str   # e.g., "Medical disclaimer (MANDATORY)"
    body: str    # Full rule body after the bold title


_RULES: list[DomainAdvisoryRule] = []


def register_advisory_rule(rule: DomainAdvisoryRule) -> None:
    """Register a domain advisory rule. Called by domain bundles during on_build_runtime()."""
    _RULES.append(rule)


def get_advisory_rules() -> list[DomainAdvisoryRule]:
    """Return all registered domain advisory rules (copy)."""
    return list(_RULES)


def unregister_advisory_rules(domain_name: str) -> None:
    """Unregister all advisory rules for a domain — called by DomainRuntimeContext on rollback."""
    global _RULES
    _RULES = [r for r in _RULES if r.domain != domain_name]


def clear_registry() -> None:
    """Clear all registered rules (test-only)."""
    _RULES.clear()
