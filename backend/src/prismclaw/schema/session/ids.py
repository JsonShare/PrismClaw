from __future__ import annotations


def derive_memory_session_key(session_id: str) -> str:
    """Derive the user/workspace-scoped ``session_key`` from a *session_id*.

    Three conventions are in use across the codebase:

    1. **SessionRouter path** — ``session_id = "{session_key}:s:{suffix}"``
       (the ``:s:`` segment is the random disambiguator).
    2. **Web chat path** — ``session_id = "web:{user_id}:{conversation_id}"``
       (optionally with a workspace segment before the conversation id).
    3. **Bare group key** — the session_id already IS the group key
       (e.g. ``web:admin``); it must pass through unchanged.

    SDD context-governance W10 (2026-08-21, root cause of "memory never
    recalled in web chat"): the previous implementation only handled the
    ``:s:`` convention, so every web-chat conversation
    (``web:admin:{conversation_id}``) queried the memory store under its own
    conversation-scoped key — matching zero entries, silently isolating each
    conversation from the user's 328 durable memories. The writer path used
    the same broken derivation, so web-chat captures were stranded under
    per-conversation keys too.
    """
    if ":s:" in session_id:
        return session_id.rsplit(":s:", 1)[0]
    # Web chat: drop the trailing conversation-id segment. Canonical web ids are
    # ``web:{user_id}:{workspace_id}:{conversation_id}`` (3 colons) — bare group
    # keys ``web:{user_id}:{workspace_id}`` (2 colons) must pass through unchanged
    # so that memory stays scoped by workspace.
    if session_id.count(":") >= 3:
        return session_id.rsplit(":", 1)[0]
    return session_id


def build_web_session_key(user_id: str, workspace_id: str | None = None) -> str:
    """Build the canonical web ``session_key`` — always workspace-scoped.

    Memory buckets (L1 index / L2-L3 rows) are keyed by this value, so a
    workspace segment must be present for every web session. Previously the
    default workspace was special-cased to ``web:{user_id}``, which pooled all
    conversations (and therefore all tasks) into one memory bucket.
    """
    wid = (workspace_id or "").strip() or "default"
    return f"web:{user_id}:{wid}"


__all__ = ["derive_memory_session_key", "build_web_session_key"]
