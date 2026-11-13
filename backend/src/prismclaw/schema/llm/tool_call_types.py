"""Unified tool call streaming aggregator.

There is exactly ONE way to assemble tool calls from LLM streaming
deltas in this codebase, and it lives here (contracts.llm).

Design rules (enforced by the code, not by convention):

* Tool call state is a :class:`ToolCallBuffer` instance.  The
  ``dict[int, dict]`` shape that was used previously is **gone** — if
  you see it anywhere it is a bug, not a compatibility layer.
* There is exactly ONE place where raw argument bytes are appended to
  an accumulating string: :meth:`ToolCallSlot.absorb_argument_bytes`.
  If you find another ``arguments += …`` anywhere, it is a bug.
* There is exactly ONE place where the accumulated argument string is
  parsed: :meth:`ToolCallSlot.parse_arguments` (``json.loads`` →
  salvage → ``None``).  The execution layer's
  :func:`prismclaw.engine.execution.dispatch.execution_entry.repair_json_args`
  reuses the same salvage implementation for the strings that reach it
  out of band; it is not a second parser.
* :meth:`ToolCallSlot.to_openai_dict` always emits **valid JSON** for
  ``function.arguments`` (``{}`` when unrecoverable).  An invalid string
  here would be persisted into the assistant message and rejected by the
  provider on replay.
* There is exactly ONE JSON-salvage implementation in this repo:
  :func:`_salvage_json_object`.  The previous ``_try_recover_json`` in
  ``execution_entry.py`` was 95 % a duplicate; it has been deleted.
* No shims.  No "backwards-compatible" ``merge_tool_call_deltas`` or
  ``finalize_tool_calls`` free functions that accept a ``dict``.
  Those two functions were the #1 source of drift — every consumer
  tweaked them slightly, so the same input produced different results
  depending on which call-site you hit.  Callers use
  :class:`ToolCallBuffer` directly.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-slot state
# ---------------------------------------------------------------------------


@dataclass
class ToolCallSlot:
    """Accumulated state for a single streaming tool call slot.

    The fields are intentionally a 1:1 match with the OpenAI
    function-calling wire format, because LiteLLM normalises every
    provider into this shape before we see a chunk.  Adding or
    renaming a field here means you also need to audit
    :func:`_normalize_tool_call` in ``client.py``.
    """

    index: int
    id: str | None = None
    type: str = "function"
    name: str = ""
    arguments_str: str = ""

    # ------------------------------------------------------------------
    # Mutations — only two entry points
    # ------------------------------------------------------------------

    def absorb_meta(self, delta: dict[str, Any]) -> None:
        """Apply id/type/name fields from a delta, first-write-wins."""
        slot_id = delta.get("id")
        if slot_id and self.id is None:
            self.id = slot_id
        delta_type = delta.get("type")
        # Accept non-empty, non-default values only.
        if delta_type and delta_type != "function" and self.type == "function":
            self.type = delta_type
        fn = delta.get("function")
        if isinstance(fn, dict):
            delta_name = fn.get("name")
            if delta_name and not self.name:
                self.name = delta_name

    def absorb_argument_bytes(self, delta: str) -> None:
        """Append raw bytes from an arguments delta.

        This is the ONLY place in the entire codebase that is allowed
        to mutate ``arguments_str``.  If any other file contains
        ``…["arguments"] +=`` it is an architectural violation —
        search the repo and fix it.
        """
        if delta:
            self.arguments_str += delta

    # ------------------------------------------------------------------
    # Parsing + recovery — one pipeline, three tiers
    # ------------------------------------------------------------------

    def parse_arguments(self) -> dict[str, Any] | None:
        """Return the parsed argument dict, or ``None`` if unrecoverable.

        Pipeline (in order of attempt):

        1. ``json.loads`` on the accumulated string.
        2. :func:`_salvage_json_object` for truncated / concatenated
           payloads.
        3. ``None`` — not a valid dict.

        Salvage lives here *and* is reused by the execution layer's
        :func:`repair_json_args` (same implementation, same result).  The
        repaired dict must be what both history and execution see,
        otherwise the assistant message replayed to the provider would
        disagree with the args the tool actually ran with.
        """
        raw = self.arguments_str or "{}"

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed

        # json.loads failed, or produced a non-dict (list / string / null).
        return _salvage_json_object(raw)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_openai_dict(self) -> dict[str, Any]:
        """Return a dict in the OpenAI tool call wire format.

        ``arguments`` is **always** valid JSON: the parsed/salvaged dict
        when available, otherwise ``{}``.  Emitting the raw malformed
        string here would persist an invalid ``tool_calls[].function.
        arguments`` into the assistant message, which the provider rejects
        with a 400 on the next replay.
        """
        args_parsed = self.parse_arguments()
        arguments = json.dumps(args_parsed, ensure_ascii=False) if args_parsed else "{}"
        return {
            "id": self.id or f"call_{self.index}",
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": arguments,
            },
        }


# ---------------------------------------------------------------------------
# Container
# ---------------------------------------------------------------------------


class ToolCallBuffer:
    """Accumulates deltas from an LLM stream into tool call slots."""

    def __init__(self) -> None:
        self._slots: dict[int, ToolCallSlot] = {}

    # ------------------------------------------------------------------
    # Two writable operations: absorb deltas, finalise
    # ------------------------------------------------------------------

    def absorb(self, deltas: list[dict[str, Any]]) -> None:
        """Apply a list of normalised deltas from a single stream chunk."""
        for d in deltas:
            try:
                idx = int(d["index"])
            except (KeyError, TypeError, ValueError):
                logger.warning(
                    "tool_call_delta_missing_index keys=%s",
                    sorted(d.keys()) if hasattr(d, "keys") else type(d).__name__,
                )
                continue

            slot = self._slots.get(idx)
            if slot is None:
                slot = ToolCallSlot(index=idx)
                self._slots[idx] = slot

            slot.absorb_meta(d)
            fn = d.get("function")
            if isinstance(fn, dict):
                args_delta = fn.get("arguments")
                if isinstance(args_delta, str):
                    slot.absorb_argument_bytes(args_delta)

    def finalize(self) -> list[dict[str, Any]]:
        """Return the assembled tool calls in ascending index order."""
        return [self._slots[i].to_openai_dict() for i in sorted(self._slots)]

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._slots)

    def __bool__(self) -> bool:
        return bool(self._slots)


# ---------------------------------------------------------------------------
# Salvage helper — the one and only
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def _strip_md_fence(raw: str) -> str:
    """Return the inner JSON when *raw* is wrapped in a markdown fence."""
    match = _FENCE_RE.search(raw)
    return match.group(1).strip() if match else raw


def _strip_trailing_commas(raw: str) -> str:
    """Remove ``,}`` / ``,]`` before a closing delimiter (invalid JSON)."""
    return _TRAILING_COMMA_RE.sub(r"\1", raw)


def _salvage_json_object(raw: str) -> dict[str, Any] | None:
    """Extract the most complete valid JSON dict from a malformed string.

    Intentionally conservative.  We only handle cases that have been
    observed in production logs:

    * Concatenated objects: ``{"a": 1}{"b": 2}`` — returns the latter.
    * Truncated then duplicated: ``{"url":"x","n":8000{"url":"x","n":8000}``.
    * JSON surrounded by non-JSON garbage on either side.
    * **Stream truncation** (race condition): the LLM stream ends before
      emitting the final closing chunk(s), leaving the JSON object with
      an unterminated string or missing 1-2 closing braces.  We
      auto-repair these by appending the missing ``"`` and/or ``}``.

    Cases we explicitly do NOT try to recover (if you see them in the
    wild, add them *after* confirming they occur naturally):

    * Mismatched quotes, escaped characters inside strings, embedded
      control bytes — transport corruption, not tool-call duplication.
    * Arrays / strings / numbers as the top-level value — tool-call
      arguments are always objects.
    * More than 2 appended closing braces — anything beyond that is
      almost certainly a real problem, not a dropped pair.

    Two cheap normalisations run before the strategies below: markdown
    code-fence unwrapping (```json … ```) and trailing-comma removal.
    Both are applied only to a full-string parse attempt, so a stray
    ``",}"`` inside a string value can never be mangled in the
    fallback strategies.
    """
    _fence = _strip_md_fence(raw)
    if _fence != raw:
        raw = _fence
    try:
        _fast = json.loads(_strip_trailing_commas(raw))
    except (ValueError, TypeError):
        pass
    else:
        if isinstance(_fast, dict):
            return _fast

    candidates: list[tuple[int, str]] = []  # (start_pos, substring)

    # Strategy A — every balanced {…} span via bracket matching.
    # Handles the clean concatenation case perfectly.
    depth = 0
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(raw):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    candidates.append((start, raw[start : i + 1]))
                    start = -1

    # Strategy B — for each { position, try progressively shorter
    # prefixes with 0, 1, or 2 closing braces appended.  Handles the
    # "first object lost its closing brace" case.
    i = 0
    while i < len(raw):
        if raw[i] == "{":
            best_here: str | None = None
            for end in range(len(raw), i, -1):
                base = raw[i:end]
                for suffix in ("", "}", "}}"):
                    try:
                        obj = json.loads(base + suffix)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(obj, dict) and (
                        best_here is None or len(base + suffix) > len(best_here)
                    ):
                        best_here = base + suffix
                if best_here is not None:
                    candidates.append((i, best_here))
                    break
        i += 1

    # Strategy C — stream-truncation repair (race-condition recovery).
    # If the raw string looks like a truncated JSON object (unterminated
    # string + missing closing braces), append the missing delimiters
    # and try parsing.  This handles the case where the LLM stream ends
    # before emitting the final chunk(s) — a race condition that
    # produces strings like ``{"command": "pip install ... tail -1``
    # (missing the closing ``"`` and ``}``).
    for trunc_start in range(len(raw)):
        if raw[trunc_start] != "{":
            continue
        base = raw[trunc_start:]
        # Try cascading repairs: first close the string, then close the object.
        # Order matters: we try the most likely repairs for stream truncation.
        # 1. Missing closing brace only:  "}"
        # 2. Missing closing quote only: '"'
        # 3. Missing BOTH quote and brace: '"}'
        # 4. Missing quote + 2 braces (nested obj): '"}}'
        repair_suffixes = ['}', '"', '"}', '"}}', '}}']
        for suffix in repair_suffixes:
            candidate_str = base + suffix
            try:
                obj = json.loads(candidate_str)
            except (ValueError, TypeError):
                continue
            if isinstance(obj, dict) and len(obj) > 0:
                candidates.append((trunc_start, candidate_str))
                break  # Best repair for this trunc_start found; move on.

    # Ranking: later-starting object wins (providers that duplicate
    # always send the more-complete copy last).  Ties → longer wins.
    best: dict[str, Any] | None = None
    best_start = -1
    best_len = 0
    seen: set[str] = set()
    for start_pos, substr in candidates:
        if substr in seen:
            continue
        seen.add(substr)
        try:
            obj = json.loads(substr)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        # Reject empty-object salvage.  If the intended payload really was
        # ``{}``, ``json.loads`` on the raw string would have succeeded in
        # Tier 1 (above) and we would never reach the salvage pipeline at
        # all.  An empty dict here means the salvage heuristics glued an
        # unrelated ``{`` to a synthetic ``}`` suffix — 100 % false
        # positive, which would otherwise silently mask a real corruption
        # and leave downstream tools complaining about missing fields.
        if len(obj) == 0:
            continue
        if start_pos > best_start or (start_pos == best_start and len(substr) > best_len):
            best = obj
            best_start = start_pos
            best_len = len(substr)

    return best
