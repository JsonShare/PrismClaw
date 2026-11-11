from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

_UNSTABLE_KEYS = frozenset(
    {
        "timestamp",
        "created_at",
        "updated_at",
        "telemetry",
        "profiler",
        "elapsed_ms",
    }
)


@dataclass(frozen=True, slots=True)
class PreparedTurnDigestInput:
    messages_for_llm: list[Any]
    system_blocks: list[Any]
    tools_for_llm: list[Any]
    context_payload: Any | None = None


def canonicalize_for_digest(value: Any, *, excluded_keys: frozenset[str] | None = None) -> Any:
    excluded = _UNSTABLE_KEYS if excluded_keys is None else excluded_keys
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", exclude_none=True)
    elif is_dataclass(value):
        value = asdict(value)
    elif hasattr(value, "__dict__") and not isinstance(value, type):
        value = vars(value)

    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key in sorted(value.keys(), key=lambda item: str(item)):
            str_key = str(key)
            if str_key in excluded:
                continue
            normalized[str_key] = canonicalize_for_digest(value[key], excluded_keys=excluded)
        return normalized

    if isinstance(value, set):
        normalized_items = [canonicalize_for_digest(item, excluded_keys=excluded) for item in value]
        return sorted(normalized_items, key=_stable_sort_key)

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [canonicalize_for_digest(item, excluded_keys=excluded) for item in value]

    return value


def canonical_json_dumps(value: Any, *, excluded_keys: frozenset[str] | None = None) -> str:
    normalized = canonicalize_for_digest(value, excluded_keys=excluded_keys)
    return json.dumps(
        normalized,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def digest_hex(
    value: Any,
    *,
    algorithm: str = "sha256",
    length: int | None = None,
    excluded_keys: frozenset[str] | None = None,
) -> str:
    payload = canonical_json_dumps(value, excluded_keys=excluded_keys).encode("utf-8")
    digest = hashlib.new(algorithm, payload).hexdigest()
    return digest[:length] if length is not None else digest


def build_prepared_turn_digest(
    *,
    messages_for_llm: list[Any],
    system_blocks: list[Any],
    tools_for_llm: list[Any],
    context_payload: Any | None = None,
) -> str:
    extra_excluded = _UNSTABLE_KEYS | {"id", "index"}
    normalized_messages = _normalized_list(messages_for_llm, excluded_keys=extra_excluded)
    normalized_system = _normalized_list(system_blocks, excluded_keys=extra_excluded)
    normalized_tools = _sorted_normalized_list(tools_for_llm)
    normalized_context_payload = (
        canonicalize_for_digest(context_payload, excluded_keys=extra_excluded)
        if context_payload is not None
        else None
    )
    return digest_hex(
        PreparedTurnDigestInput(
            messages_for_llm=normalized_messages,
            system_blocks=normalized_system,
            tools_for_llm=normalized_tools,
            context_payload=normalized_context_payload,
        ),
    )


def build_tool_pool_id(tools_for_llm: list[dict[str, Any]]) -> str:
    return digest_hex({"tools": _sorted_normalized_list(tools_for_llm)})


def build_replacement_state_digest(decisions: list[dict[str, Any]]) -> str | None:
    if not decisions:
        return None
    return digest_hex(
        {"replacement_state": _sorted_normalized_list(decisions)},
        algorithm="sha1",
        length=16,
    )


def _stable_sort_key(value: Any) -> str:
    return canonical_json_dumps(value)


def _sorted_normalized_list(
    values: list[Any],
    *,
    excluded_keys: frozenset[str] | None = None,
) -> list[Any]:
    normalized = _normalized_list(values, excluded_keys=excluded_keys)
    return sorted(normalized, key=_stable_sort_key)


def _normalized_list(
    values: list[Any],
    *,
    excluded_keys: frozenset[str] | None = None,
) -> list[Any]:
    normalized = [
        canonicalize_for_digest(value, excluded_keys=excluded_keys)
        for value in values
    ]
    return normalized
