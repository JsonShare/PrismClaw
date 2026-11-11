from __future__ import annotations

from typing import Any


def redis_is_required(settings: Any = None) -> bool:
    """Whether Redis is required for the current runtime. Always True."""
    return True


def build_runtime_mode_snapshot(app_or_state: Any, *, settings: Any = None) -> dict[str, Any]:
    """Build a snapshot describing the current runtime mode.

    Args:
        app_or_state: An app or state object. If it has a ``.state`` attribute,
            that will be used instead.
        settings: Optional settings/config object. When provided, it should
            expose the following attribute shape (works with both
            ``infra.settings.Settings`` and ``schema.config.config.AgentRunConfig``):

            - ``.evolution.enabled``
            - ``.evolution.curator.enabled``
            - ``.evolution.dream.enabled``
            - ``.affinity.enabled``

            If *None*, the settings are read from ``app_or_state.state.settings``.
    """
    state = getattr(app_or_state, "state", app_or_state)
    effective_settings = settings or getattr(state, "settings", None)
    redis_available = getattr(state, "redis_client", None) is not None
    disabled_capabilities: list[dict[str, str]] = []
    reason_codes: list[str] = []

    if not redis_available:
        reason_codes.append("redis_unavailable")
        if effective_settings is not None:
            if getattr(state, "memory_store", None) is not None:
                disabled_capabilities.append(
                    {"name": "working_memory_live_sync", "reason": "redis_unavailable"}
                )
                if effective_settings.evolution.enabled:
                    disabled_capabilities.append(
                        {"name": "sop_tracking", "reason": "redis_unavailable"}
                    )
                    if effective_settings.evolution.curator.enabled:
                        disabled_capabilities.append(
                            {"name": "curator", "reason": "redis_unavailable"}
                        )
            if effective_settings.evolution.enabled and effective_settings.evolution.dream.enabled:
                disabled_capabilities.append(
                    {"name": "dream_dispatch", "reason": "redis_unavailable"}
                )
            if effective_settings.affinity.enabled:
                disabled_capabilities.append(
                    {"name": "affinity_routing", "reason": "redis_unavailable"}
                )

    mode = "standard"
    if not redis_available:
        mode = "fallback" if disabled_capabilities else "offline"

    workspace_backend = "redis"

    snapshot_backend = "postgres"
    if getattr(state, "run_snapshot_store", None) is None:
        snapshot_backend = "disabled"

    return {
        "mode": mode,
        "reason_codes": reason_codes,
        "redis": {
            "available": redis_available,
            "required": True,
        },
        "backends": {
            "session": "redis",
            "lock": "redis",
            "memory": (
                "postgres"
                if effective_settings is not None and getattr(state, "memory_store", None) is not None
                else "disabled"
            ),
            "workspace": workspace_backend,
            "run_snapshot": snapshot_backend,
        },
        "disabled_capabilities": disabled_capabilities,
    }


def format_runtime_mode_lines(snapshot: dict[str, Any] | None) -> list[str]:
    if not isinstance(snapshot, dict):
        return []
    mode = str(snapshot.get("mode", "unknown") or "unknown")
    redis = snapshot.get("redis") if isinstance(snapshot.get("redis"), dict) else {}
    disabled = (
        snapshot.get("disabled_capabilities")
        if isinstance(snapshot.get("disabled_capabilities"), list)
        else []
    )
    lines = [
        "",
        "🧩 **运行模式**",
        f"Mode:       `{mode}`",
        (
            "Redis:      "
            f"available={bool(redis.get('available'))} required={bool(redis.get('required'))}"
        ),
    ]
    if disabled:
        capability_names = []
        for item in disabled:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "") or "").strip()
            reason = str(item.get("reason", "") or "").strip()
            if not name:
                continue
            capability_names.append(f"{name}({reason})" if reason else name)
        if capability_names:
            lines.append(f"Disabled:   {', '.join(capability_names)}")
    return lines
