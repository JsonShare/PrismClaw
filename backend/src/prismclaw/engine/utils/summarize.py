"""Core-owned message / tree.order snapshot helpers.

These are deliberately **core-owned** (not trajectory-owned) so hot-path
call-sites in `context_helpers.py` and `turn_preparation.py` don't need to
`import prismclaw.engine.trajectory.*` — keeping the dependency arrow pointed
trajectory → core (the解耦 contract).

SDD DT-3: do NOT hold references into the mutable pipeline objects. Every
element we return is a freshly-constructed Python str/int/tuple. Any in-place
mutation after return CANNOT affect the captured values.
"""
from __future__ import annotations

from typing import Any


def summarize_messages(
    messages: list[dict[str, Any]],
    *,
    n_head: int = 2,
    n_tail: int = 2,
    content_slice: int = 80,
) -> tuple[int, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Return (count, roles, head_summaries, tail_summaries).

    🛡️ Safety guarantee: return values are built from newly-constructed
    str/int/tuple only. No pointer back into input dicts/lists survives.
    """
    total = 0
    formatted: list[str] = []
    roles: list[str] = []
    for m in messages:
        total += 1
        if not isinstance(m, dict):
            r = "<?>"
            c_preview = str(m)[:content_slice]
        else:
            raw_role = m.get("role")
            r = "?" if raw_role is None else str(raw_role)
            raw_c = m.get("content")
            if isinstance(raw_c, list):
                c_preview = f"[blocks×{len(raw_c)}]"
            elif isinstance(raw_c, str):
                c_preview = raw_c[:content_slice]  # str slice = NEW str object
            elif raw_c is None:
                c_preview = ""
            else:
                c_preview = str(raw_c)[:content_slice]
        roles.append(r)
        # formatted: explicitly construct new f-string every time
        formatted.append(f"[{r}] {c_preview}")

    count = total
    head_count = min(n_head, count)
    head = tuple(formatted[:head_count])
    if count == 0:
        tail: tuple[str, ...] = ()
    else:
        tail_start = max(0, count - n_tail)
        tail = tuple(formatted[tail_start:])
    return count, tuple(roles), head, tail


def summarize_tree_order(
    order_entries: list[Any],
    *,
    n_seq_head: int = 5,
    n_seq_tail: int = 5,
) -> tuple[int, tuple[str, ...], tuple[int, ...], tuple[int, ...], tuple[str, ...]]:
    """Return for a SessionTree.order entry list:

        (entry_count, entry_types, head_seqs, tail_seqs, tail_types)
    """
    total = 0
    types: list[str] = []
    seqs: list[int] = []
    for e in order_entries or []:
        total += 1
        tname = type(e).__name__
        try:
            s = getattr(e, "seq", -1)
            s = int(s) if isinstance(s, int) else -1
        except Exception:
            s = -1
        types.append(tname)
        seqs.append(s)

    head_seq = tuple(seqs[:n_seq_head])
    if total == 0:
        tail_seq: tuple[int, ...] = ()
        tail_types: tuple[str, ...] = ()
    else:
        ts_start = max(0, total - n_seq_tail)
        tail_seq = tuple(seqs[ts_start:])
        tail_types = tuple(types[ts_start:])
    return total, tuple(types), head_seq, tail_seq, tail_types


__all__ = ["summarize_messages", "summarize_tree_order"]
