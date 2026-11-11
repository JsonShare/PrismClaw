"""Unified tool call streaming aggregator.

There is exactly ONE way to assemble tool calls from LLM streaming
deltas in this codebase, and it lives here (contracts.llm).

Design rules (enforced by the code, not by convention):

* Tool call state is a :class:`ToolCallBuffer` instance.  The
  ``dict[int, dict]`` shape that was used previously is **gone** — if
  you see it anywhere it is a bug, not a compatibility layer.
* There is exactly ONE place where raw argument bytes are appended to
  an accumulating string: :meth:`ToolCallSlot.accumulate_arguments`.
  If you find another ``arguments += …`` anywhere, it is a bug.
* There is exactly ONE place where the accumulated argument string is
  parsed into a dict and optionally recovered:
  :meth:`ToolCallSlot.finalize_arguments`.  Downstream code (tool
  implementations, the streaming executor, skill invokers) MUST NOT
  contain any ``_raw`` unwrapping, JSON-recovery, brace-balancing, or
  other "defensive" argument parsing.  If the arguments were
  unrecoverable, finalize_arguments encodes that fact in a way the
  execution layer can detect, and *only* the execution layer is
  allowed to report it.
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
    :func:`_normalize_tool_call_delta` in ``llm.py``.
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

    def parse_arguments(self) -> dict[str, Any]:
        """Return the parsed argument dict.

        Pipeline (in order of attempt):

        1. ``json.loads`` on the accumulated string.
        2. :func:`_salvage_json_object` to recover a valid dict from a
           malformed payload (duplicated JSON, dropped closing brace).
        3. ``{"_raw": original_string}`` when nothing else works.

        There is deliberately no step 4, and no "try even harder"
        branch.  Every additional salvage heuristic we add has a
        non-zero chance of silently corrupting a valid-but-unusual
        payload; logging the failure and letting the operator see it
        is always better than guessing.
        """
        raw = self.arguments_str or "{}"

        # Tier 1 — clean parse.  99.9 % of calls.
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None

        if isinstance(parsed, dict):
            return parsed
        if parsed is not None:
            # json.loads succeeded but produced a list / string / null.
            # Never silently coerce; go straight to salvage because the
            # preceding bytes might be garbage followed by a valid dict.
            logger.warning(
                "tool_call_args_non_dict idx=%d bytes=%d type=%s name=%s",
                self.index,
                len(raw),
                type(parsed).__name__,
                self.name or "<unknown>",
            )

        # Tier 2 — salvage.
        recovered = _salvage_json_object(raw)
        if recovered is not None:
            logger.warning(
                "tool_call_args_recovered idx=%d bytes=%d name=%s",
                self.index,
                len(raw),
                self.name or "<unknown>",
            )
            return recovered

        # Tier 3 — unrecoverable.  Preserve the original bytes for
        # error reporting and wrap them exactly once.
        logger.warning(
            "tool_call_args_unrecoverable idx=%d bytes=%d name=%s",
            self.index,
            len(raw),
            self.name or "<unknown>",
        )
        return {"_raw": raw}

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_openai_dict(self) -> dict[str, Any]:
        """Return a dict in the OpenAI tool call wire format.

        The ``arguments`` field is always a JSON-encoded string,
        matching the transport format and the shape expected by
        ``_function_args`` in the execution layer.
        """
        args_parsed = self.parse_arguments()
        return {
            "id": self.id or f"call_{self.index}",
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(args_parsed, ensure_ascii=False),
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
    """
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
