"""Domain-sensitive file extension registry — core declares extension point, domains register extensions.

Core RuleGate checks file extensions for security-sensitive formats.
Cryptographic and signature-related extensions are universal (domain-agnostic).
Domain-specific sensitive extensions (healthcare formats, legal formats, etc.)
are registered by domain bundles during on_build_runtime().

Usage:
    from prismclaw.engine.security.gate.routing._extension_registry import (
        register_sensitive_extensions,
        get_domain_sensitive_extensions,
    )

    # In domain bundle on_build_runtime():
    register_sensitive_extensions("medical", {".dcm", ".dicom", ".hl7", ...})
"""
from __future__ import annotations


_EXTENSIONS: list[tuple[str, frozenset[str]]] = []


def register_sensitive_extensions(domain: str, extensions: frozenset[str]) -> None:
    """Register domain-specific sensitive file extensions.

    Args:
        domain:      Domain key, e.g. "medical"
        extensions:  Set of sensitive extensions, e.g. {".dcm", ".dicom"}
    """
    _EXTENSIONS.append((domain, extensions))


def get_domain_sensitive_extensions() -> frozenset[str]:
    """Return all registered domain-specific extensions combined."""
    result: set[str] = set()
    for _, exts in _EXTENSIONS:
        result.update(exts)
    return frozenset(result)


def unregister_sensitive_extensions(domain_name: str) -> None:
    """Unregister sensitive extensions for a domain — called by DomainRuntimeContext on rollback."""
    global _EXTENSIONS
    _EXTENSIONS = [(d, exts) for d, exts in _EXTENSIONS if d != domain_name]


def clear_registry() -> None:
    """Clear all registered extensions (test-only)."""
    _EXTENSIONS.clear()
