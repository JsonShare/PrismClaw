from __future__ import annotations

import hashlib
import re
from typing import Any

from prismclaw.schema.content.compaction import CompressionValidationResult
from prismclaw.schema.content.preservation import PreservationContract, PreservationItem, SemanticCapsule

_IDENTIFIER_RE = re.compile(r"(?:[A-Za-z0-9_/.-]+\.[A-Za-z0-9_]+|[A-Fa-f0-9]{7,40})")


def build_preservation_contract(
    *,
    scope: str,
    source_id: str | None,
    source_text: str,
    state_updates: dict[str, Any] | None = None,
    source_refs: list[str] | None = None,
    preserved_identifiers: list[str] | None = None,
    loss_budget: str = "medium",
) -> PreservationContract:
    invariants: list[PreservationItem] = []
    payload = state_updates or {}
    invariants.extend(_items_from_state_payload(payload))
    for ident in preserved_identifiers or _extract_identifiers(source_text):
        # Absolute paths and URLs are reference identifiers (file paths,
        # browsed source URLs, etc.); requiring them to appear verbatim in
        # every summary is too strict and causes compaction to fail on
        # auto-generated output paths.  Relative code paths and symbol names
        # remain required so semantic preservation still guards code identity.
        is_reference = (
            ident.startswith("/")
            or "://" in ident
            or ident.startswith("www.")
            or ident.startswith("http")
        )
        invariants.append(PreservationItem(kind="identifier", content=ident, required=not is_reference))
    contract_payload = "|".join([scope, source_id or "", source_text[:120], str(len(invariants))])
    contract_id = f"pc_{hashlib.sha1(contract_payload.encode('utf-8')).hexdigest()[:12]}"
    return PreservationContract(
        contract_id=contract_id,
        scope=scope,  # type: ignore[arg-type]
        source_id=source_id,
        loss_budget=loss_budget,  # type: ignore[arg-type]
        invariants=_dedupe_items(invariants),
        source_refs=[ref for ref in (source_refs or []) if isinstance(ref, str) and ref.strip()],
        notes=_build_contract_notes(scope=scope, state_updates=payload, invariants=invariants),
    )


def build_semantic_capsule(
    *,
    capsule_type: str,
    summary_text: str,
    contract: PreservationContract,
    source_text: str,
    source_span_refs: list[str] | None = None,
    recovery_handle: dict[str, Any] | None = None,
    loss_classification: str = "bounded_loss",
    metadata: dict[str, Any] | None = None,
) -> SemanticCapsule:
    preserved = _detect_preserved_invariants(
        summary_text=summary_text,
        source_text=source_text,
        contract=contract,
        recovery_handle=dict(recovery_handle or {}),
    )
    capsule_payload = "|".join(
        [capsule_type, contract.contract_id, summary_text[:160], str(len(preserved))]
    )
    return SemanticCapsule(
        capsule_id=f"sc_{hashlib.sha1(capsule_payload.encode('utf-8')).hexdigest()[:12]}",
        capsule_type=capsule_type,  # type: ignore[arg-type]
        summary_text=summary_text,
        preserved_invariants=preserved,
        source_span_refs=[ref for ref in (source_span_refs or []) if isinstance(ref, str) and ref.strip()],
        loss_classification=loss_classification,  # type: ignore[arg-type]
        recovery_handle=dict(recovery_handle or {}),
        metadata=dict(metadata or {}),
    )


def validate_semantic_capsule(
    contract: PreservationContract,
    capsule: SemanticCapsule,
) -> CompressionValidationResult:
    required = [item.content for item in contract.invariants if item.required and item.content.strip()]
    preserved = set(capsule.preserved_invariants)
    missing = [item for item in required if item not in preserved]
    warnings: list[str] = []
    if capsule.loss_classification == "aggressive" and not missing:
        warnings.append("loss_classification_high_but_required_invariants_preserved")
    return CompressionValidationResult(
        ok=not missing,
        checked_count=len(required),
        preserved_count=len(required) - len(missing),
        missing_invariants=missing,
        warnings=warnings,
    )


def fidelity_metadata(
    *,
    contract: PreservationContract,
    capsule: SemanticCapsule,
    validation: CompressionValidationResult,
) -> dict[str, Any]:
    return {
        "preservation_contract": contract.model_dump(mode="json"),
        "semantic_capsule": capsule.model_dump(mode="json"),
        "validation": validation.model_dump(mode="json"),
    }


def _items_from_state_payload(payload: dict[str, Any]) -> list[PreservationItem]:
    items: list[PreservationItem] = []
    primary_goal = payload.get("primary_goal")
    if isinstance(primary_goal, str) and primary_goal.strip():
        items.append(PreservationItem(kind="fact", content=primary_goal.strip(), required=False))
    mapping = {
        "constraints": "constraint",
        "decisions": "decision",
        "open_questions": "question",
        "facts": "fact",
    }
    for key, kind in mapping.items():
        value = payload.get(key)
        if not isinstance(value, list):
            continue
        for row in value[:6]:
            if not isinstance(row, dict):
                continue
            content = row.get("content")
            if isinstance(content, str) and content.strip():
                items.append(PreservationItem(kind=kind, content=content.strip()))
    return items


def _extract_identifiers(text: str) -> list[str]:
    if not text:
        return []
    return list(dict.fromkeys(match.group(0) for match in _IDENTIFIER_RE.finditer(text)))[:12]


def _detect_preserved_invariants(
    *,
    summary_text: str,
    source_text: str,
    contract: PreservationContract,
    recovery_handle: dict[str, Any],
) -> list[str]:
    preserved: list[str] = []
    summary_normalized = _normalize(summary_text)
    source_normalized = _normalize(source_text)
    recovery_text = _normalize(str(recovery_handle.get("state_updates", {})))
    for item in contract.invariants:
        target = item.content.strip()
        if not target:
            continue
        normalized = _normalize(target)
        if normalized and normalized in summary_normalized:
            preserved.append(target)
            continue
        if item.kind == "identifier" and target in summary_text:
            preserved.append(target)
            continue
        if item.kind in {"fact", "constraint", "decision", "question"}:
            if normalized in recovery_text or normalized in source_normalized:
                preserved.append(target)
    return list(dict.fromkeys(preserved))


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _dedupe_items(items: list[PreservationItem]) -> list[PreservationItem]:
    deduped: list[PreservationItem] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item.kind, item.content.strip())
        if not key[1] or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _build_contract_notes(
    *,
    scope: str,
    state_updates: dict[str, Any],
    invariants: list[PreservationItem],
) -> list[str]:
    notes = [f"scope={scope}", f"invariant_count={len(invariants)}"]
    if state_updates:
        notes.append("state_updates_present")
    return notes
