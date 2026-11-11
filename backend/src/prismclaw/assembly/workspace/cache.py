from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

from prismclaw.engine.foundation.telemetry.hotpath import HotPathTelemetry

WarmState = Literal["warming", "ready", "stale"]
ResolvedState = Literal["base", "warming", "ready", "stale"]
WorkspaceFamilyKey = tuple[str, str]

_HOTPATH = HotPathTelemetry()


@dataclass(slots=True)
class WorkspaceRuntimeWarmEntry:
    generation: int
    status: WarmState
    scoped_deps: Any | None = None
    warm_task: asyncio.Task[Any] | None = None
    last_started_at: float = field(default_factory=time.monotonic)
    last_ready_at: float | None = None
    last_error: str | None = None


class WorkspaceRuntimeWarmCache:
    def __init__(self) -> None:
        self._entries: dict[WorkspaceFamilyKey, WorkspaceRuntimeWarmEntry] = {}

    @property
    def entries(self) -> dict[WorkspaceFamilyKey, WorkspaceRuntimeWarmEntry]:
        return self._entries

    def clear(self) -> None:
        for entry in self._entries.values():
            task = entry.warm_task
            if task is not None and not task.done():
                task.cancel()
        self._entries.clear()

    def status_for(self, family_key: WorkspaceFamilyKey, *, generation: int) -> ResolvedState:
        entry = self._entries.get(family_key)
        if entry is None or entry.generation != generation:
            return "base"
        if entry.status == "ready" and entry.scoped_deps is not None:
            return "ready"
        if entry.scoped_deps is not None:
            return "stale"
        if entry.status == "warming":
            return "warming"
        return "base"

    def best_available(
        self,
        family_key: WorkspaceFamilyKey,
        *,
        generation: int,
        base_deps: Any,
    ) -> tuple[Any, ResolvedState]:
        state = self.status_for(family_key, generation=generation)
        entry = self._entries.get(family_key)
        if state in {"ready", "stale"} and entry is not None and entry.scoped_deps is not None:
            return entry.scoped_deps, state
        return base_deps, state

    def ensure_warming(
        self,
        family_key: WorkspaceFamilyKey,
        *,
        generation: int,
        builder: Callable[[], Awaitable[Any]],
    ) -> WorkspaceRuntimeWarmEntry:
        current = self._entries.get(family_key)
        if (
            current is not None
            and current.generation == generation
            and current.warm_task is not None
            and not current.warm_task.done()
        ):
            return current
        if current is not None and current.generation == generation and current.status == "ready":
            return current

        fallback_deps = current.scoped_deps if current is not None else None
        warm_mode = "refresh" if fallback_deps is not None else "cold"
        entry = WorkspaceRuntimeWarmEntry(
            generation=generation,
            status="warming",
            scoped_deps=fallback_deps,
        )
        self._entries[family_key] = entry

        async def _build() -> Any:
            started_at = time.monotonic()
            result = "ready"
            try:
                scoped = await builder()
            except Exception as exc:
                result = "error"
                if self._entries.get(family_key) is entry:
                    entry.warm_task = None
                    entry.last_error = f"{type(exc).__name__}: {exc}"
                    if fallback_deps is not None:
                        entry.status = "stale"
                    else:
                        self._entries.pop(family_key, None)
                raise
            else:
                if self._entries.get(family_key) is entry:
                    entry.scoped_deps = scoped
                    entry.status = "ready"
                    entry.warm_task = None
                    entry.last_ready_at = time.monotonic()
                    entry.last_error = None
                return scoped
            finally:
                _HOTPATH.observe(
                    "workspace_runner_deps.build_ms",
                    (time.monotonic() - started_at) * 1000.0,
                    labels={"mode": warm_mode, "result": result},
                )

        entry.warm_task = asyncio.create_task(_build())
        return entry


def _ensure_workspace_runner_deps_cache(state: Any) -> WorkspaceRuntimeWarmCache:
    cache = getattr(state, "_workspace_runner_deps_cache", None)
    if isinstance(cache, WorkspaceRuntimeWarmCache):
        return cache
    created = WorkspaceRuntimeWarmCache()
    state._workspace_runner_deps_cache = created
    return created


def _workspace_family_key(
    *,
    user_id: str,
    workspace_root: Path,
) -> WorkspaceFamilyKey:
    return (
        str(user_id),
        str(workspace_root.expanduser().resolve()),
    )


def _workspace_generation(base_deps: Any) -> int:
    lifecycle = getattr(base_deps, "plugin_lifecycle_manager", None)
    generation = getattr(lifecycle, "generation", 0)
    return generation if isinstance(generation, int) else 0


def get_app_workspace_runner_deps_state(
    app: Any,
    *,
    user_id: str,
    workspace_root: Path,
) -> ResolvedState:
    state = getattr(app, "state", None)
    if state is None:
        return "base"
    base_deps = getattr(state, "runner_deps", None)
    if base_deps is None:
        return "base"
    cache = getattr(state, "_workspace_runner_deps_cache", None)
    if not isinstance(cache, WorkspaceRuntimeWarmCache):
        return "base"
    return cache.status_for(
        _workspace_family_key(user_id=user_id, workspace_root=workspace_root),
        generation=_workspace_generation(base_deps),
    )


def get_app_workspace_runner_deps(
    app: Any,
    *,
    user_id: str,
    workspace_root: Path,
    build_scoped_runner_deps: Callable[..., Any] | None = None,
) -> Any | None:
    """Sync lookup: never blocks, returns ready/stale deps or base deps."""
    _ = build_scoped_runner_deps
    state = getattr(app, "state", None)
    if state is None:
        return None

    base_deps = getattr(state, "runner_deps", None)
    settings = getattr(state, "settings", None)
    if base_deps is None or settings is None:
        return base_deps

    cache = _ensure_workspace_runner_deps_cache(state)
    deps, _state = cache.best_available(
        _workspace_family_key(user_id=user_id, workspace_root=workspace_root),
        generation=_workspace_generation(base_deps),
        base_deps=base_deps,
    )
    return deps


async def get_app_workspace_runner_deps_async(
    app: Any,
    *,
    user_id: str,
    workspace_root: Path,
    build_scoped_runner_deps: Callable[..., Awaitable[Any]] | None = None,
    wait_for_ready: bool = True,
) -> Any | None:
    """Async lookup with explicit warm-cache states.

    When ``wait_for_ready`` is true, this preserves the historic blocking
    contract. When false, it starts warming in the background and returns the
    best currently available deps (ready/stale/base).
    """
    state = getattr(app, "state", None)
    if state is None:
        return None

    base_deps = getattr(state, "runner_deps", None)
    settings = getattr(state, "settings", None)
    if base_deps is None or settings is None:
        return base_deps

    if build_scoped_runner_deps is None:
        from prismclaw.assembly.workspace.runner_deps import (
            build_workspace_scoped_runner_deps,
        )

        build_scoped_runner_deps = build_workspace_scoped_runner_deps

    cache = _ensure_workspace_runner_deps_cache(state)
    family_key = _workspace_family_key(
        user_id=user_id,
        workspace_root=workspace_root,
    )
    generation = _workspace_generation(base_deps)
    entry = cache.ensure_warming(
        family_key,
        generation=generation,
        builder=lambda: build_scoped_runner_deps(
            base_deps,
            settings=settings,
            workspace_root=workspace_root,
        ),
    )
    if wait_for_ready and entry.warm_task is not None:
        await asyncio.shield(entry.warm_task)
    deps, _state = cache.best_available(
        family_key,
        generation=generation,
        base_deps=base_deps,
    )
    return deps


async def get_app_workspace_extension_manager_async(
    app: Any,
    *,
    user_id: str,
    workspace_root: Path,
    build_scoped_runner_deps: Callable[..., Awaitable[Any]] | None = None,
    wait_for_ready: bool = True,
) -> Any | None:
    runner_deps = await get_app_workspace_runner_deps_async(
        app,
        user_id=user_id,
        workspace_root=workspace_root,
        build_scoped_runner_deps=build_scoped_runner_deps,
        wait_for_ready=wait_for_ready,
    )
    if runner_deps is None:
        return None
    return getattr(runner_deps, "extension_manager", None)


def get_app_workspace_extension_manager(
    app: Any,
    *,
    user_id: str,
    workspace_root: Path,
    build_scoped_runner_deps: Callable[..., Any] | None = None,
) -> Any | None:
    runner_deps = get_app_workspace_runner_deps(
        app,
        user_id=user_id,
        workspace_root=workspace_root,
        build_scoped_runner_deps=build_scoped_runner_deps,
    )
    if runner_deps is None:
        return None
    return getattr(runner_deps, "extension_manager", None)


def clear_workspace_runner_deps_cache(app: Any) -> None:
    state = getattr(app, "state", None)
    if state is None:
        return
    cache = getattr(state, "_workspace_runner_deps_cache", None)
    if isinstance(cache, WorkspaceRuntimeWarmCache):
        cache.clear()
        return
    state._workspace_runner_deps_cache = WorkspaceRuntimeWarmCache()
