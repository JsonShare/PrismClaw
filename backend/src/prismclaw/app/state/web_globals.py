from __future__ import annotations

from typing import Any, Awaitable, Callable


async def shutdown_web_channel_runtime_globals(
    *,
    grace_s: float = 0.25,
    close_client_fn: Callable[[Any], Awaitable[None]] | None = None,
    app_state: Any = None,
) -> None:
    """Async counterpart to :func:`reset_web_channel_runtime_globals`, used
    during FastAPI shutdown.

    Unlike :func:`reset_web_channel_runtime_globals`, which is fire-and-forget
    (run on startup to clear per-process leftover state from a previous
    in-process reload), this function *awaits* teardown of async resources so
    the event loop does not reap them as pending frames and log
    ``Task was destroyed but it is pending!``.

    Today this drives :class:`SessionQueue.shutdown` which in turn:
    * marks every pending tool approval stale and yields the loop so callers
      blocked on ``ApprovalRuntime.wait_for_decision`` can observe the
      decision and return;
    * cancels detached fire-and-forget tasks (followup questions, one-shot
      reference pushes) that were spawned outside the TaskManager for
      latency reasons.
    """
    from prismclaw.app.web.chat.chat_protocol import _session_queue as _chat_session_queue
    from prismclaw.app.web.websocket.websocket import registry as _ws_registry

    session_queue = _chat_session_queue

    # If the FastAPI app wired a WebDeps-bound session queue (the worker queue
    # that actually carries live conversations), prefer that one over the
    # module-level fallback queue.
    web_deps = None
    if app_state is not None:
        web_deps = getattr(app_state, "web_deps", None)
    wired_queue = None
    if web_deps is not None:
        wired_queue = getattr(web_deps, "session_queue", None)
    if wired_queue is not None:
        session_queue = wired_queue

    if session_queue is not None and hasattr(session_queue, "shutdown"):
        shutdown_fn = getattr(session_queue, "shutdown")
        if callable(shutdown_fn):
            try:
                await shutdown_fn(grace_s=grace_s)
            except Exception:
                # Shutdown must never raise — we want the rest of the
                # lifespan cleanup to proceed even if draining conversations
                # hits an unexpected error.
                import logging

                logging.getLogger(__name__).warning(
                    "web runtime shutdown error", exc_info=True
                )
    # Finally clear WS registry (no async work needed)
    try:
        _ws_registry.clear()
    except Exception:
        pass
    _ = close_client_fn


def reset_web_channel_runtime_globals() -> None:
    from prismclaw.app.web.chat.chat_protocol import _session_queue
    from prismclaw.app.web.websocket.websocket import registry as _ws_registry

    _session_queue.reset()
    _ws_registry.clear()
