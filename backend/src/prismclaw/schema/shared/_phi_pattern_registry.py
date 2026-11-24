"""领域 PII/PHI 模式注册表：核心声明扩展点，领域包注册具体模式。

Core phi_sanitizer provides universal PII patterns (phone, email, SSN, ID card, etc.).
Domain-specific patterns (e.g. record numbers, bed numbers, admission dates)
are registered by domain bundles during on_build_runtime().

Usage:
    from prismclaw.schema.shared._phi_pattern_registry import (
        register_phi_patterns,
        get_domain_phi_patterns,
    )

    # In domain bundle's on_build_runtime():
    register_phi_patterns("my_domain", [
        ("MRN", re.compile(r'...'), "[PHI_REDACTED_MRN]"),
        ...
    ])
"""

from __future__ import annotations

import re

# Type alias matching phi_sanitizer's pattern tuple: (label, compiled_pattern, replacement)
PhiPatternEntry = tuple[str, re.Pattern[str], str]

_PATTERNS: list[tuple[str, list[PhiPatternEntry]]] = []


def register_phi_patterns(domain: str, patterns: list[PhiPatternEntry]) -> None:
    """Register domain-specific PII/PHI patterns.

    Args:
        domain:   Domain key, e.g. "my_domain"
        patterns: List of (label, compiled_regex, replacement) tuples
    """
    _PATTERNS.append((domain, patterns))


def get_domain_phi_patterns() -> list[PhiPatternEntry]:
    """Return all registered domain-specific patterns combined (flat list)."""
    result: list[PhiPatternEntry] = []
    for _, patterns in _PATTERNS:
        result.extend(patterns)
    return result


def unregister_phi_patterns(domain_name: str) -> None:
    """Unregister PHI patterns for a domain — called by DomainRuntimeContext on rollback."""
    global _PATTERNS
    _PATTERNS = [(d, pats) for d, pats in _PATTERNS if d != domain_name]


def clear_registry() -> None:
    """Clear all registered patterns (test-only)."""
    _PATTERNS.clear()
