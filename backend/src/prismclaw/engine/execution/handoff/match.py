from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from prismclaw.engine.execution.handoff.contract import HandoffRequestEntry

_OVERLAP_MULTIPLIER = 10000
_SINGLE_MATCH_THRESHOLD_INT = 5000
_MERGE_DETECTION_THRESHOLD_INT = 3000


def _longest_common_substring_len(a: str, b: str) -> int:
    if not a or not b:
        return 0
    m, n = len(a), len(b)
    if m * n > 4_000_000:
        a, b = a[:2000], b[:2000]
        m, n = len(a), len(b)
    prev = [0] * (n + 1)
    best = 0
    for i in range(1, m + 1):
        curr = [0] * (n + 1)
        ai = a[i - 1]
        for j in range(1, n + 1):
            if ai == b[j - 1]:
                curr[j] = prev[j - 1] + 1
                if curr[j] > best:
                    best = curr[j]
        prev = curr
    return best


def lcs_overlap(a: str, b: str) -> float:
    a_norm = unicodedata.normalize("NFC", a)
    b_norm = unicodedata.normalize("NFC", b)
    if not a_norm or not b_norm:
        return 0.0
    lcs = _longest_common_substring_len(a_norm, b_norm)
    denom = min(len(a_norm), len(b_norm))
    if denom == 0:
        return 0.0
    return round(lcs / denom, 4)


def _overlap_int(a: str, b: str) -> int:
    return int(lcs_overlap(a, b) * _OVERLAP_MULTIPLIER)


@dataclass
class MatchResult:
    entry: HandoffRequestEntry
    overlap_int: int


def find_single_match(
    entries: list[HandoffRequestEntry], *, target: str, task: str
) -> MatchResult | None:
    candidates: list[MatchResult] = []
    for entry in entries:
        if entry.target != target:
            continue
        overlap_int = _overlap_int(entry.task, task)
        if overlap_int >= _SINGLE_MATCH_THRESHOLD_INT:
            candidates.append(MatchResult(entry=entry, overlap_int=overlap_int))
    if not candidates:
        return None
    candidates.sort(
        key=lambda r: (-r.overlap_int, r.entry.insertion_seq)
    )
    return candidates[0]


def find_merge_matches(
    entries: list[HandoffRequestEntry], *, target: str, task: str
) -> list[MatchResult]:
    candidates: list[MatchResult] = []
    for entry in entries:
        if entry.target != target:
            continue
        overlap_int = _overlap_int(entry.task, task)
        if overlap_int >= _MERGE_DETECTION_THRESHOLD_INT:
            candidates.append(MatchResult(entry=entry, overlap_int=overlap_int))
    return candidates


def order_merged_chains(
    entries: list[HandoffRequestEntry],
) -> list[HandoffRequestEntry]:
    return sorted(
        entries,
        key=lambda e: (
            -e.source_effective_hop_index,
            e.source_subagent,
            e.source_chain_id,
        ),
    )


__all__ = [
    "MatchResult",
    "find_merge_matches",
    "find_single_match",
    "lcs_overlap",
    "order_merged_chains",
]
