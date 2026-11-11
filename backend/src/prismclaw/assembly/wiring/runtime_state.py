from __future__ import annotations

from typing import Any


def resolve_dream_boundary(runner_deps: Any) -> Any:
    explicit_boundary = getattr(runner_deps.dream, "boundary", None)
    if explicit_boundary is not None:
        return explicit_boundary

    context_engine = getattr(runner_deps, "context_engine", None)
    if context_engine is None:
        return None

    from prismclaw.runtime.facade.compression import DefaultContextEngine

    if not isinstance(context_engine, DefaultContextEngine):
        return None
    service_bundle = getattr(context_engine, "service_bundle", None)
    memory_retriever = getattr(service_bundle, "memory_retriever", None)
    aftermath_boundary = getattr(memory_retriever, "_aftermath_boundary", None)
    return getattr(aftermath_boundary, "_dream_boundary", None)


def resolve_dream_scheduler(runner_deps: Any) -> Any:
    return resolve_dream_boundary(runner_deps)
