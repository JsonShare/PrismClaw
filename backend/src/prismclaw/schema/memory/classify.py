from __future__ import annotations

"""Memory scope classification — write-time stamping (SDD §5.9).

Moved from prismclaw.persistence.memory.base to break the engine→persistence
dependency for pure data-model imports.
"""

# SDD B5 (docs/memory-dream-sdd.md §5.9): scope 三值 — user（用户画像，跨会话）
# / project（项目语境，跨会话）/ agent（通用经验，跨用户——通道因多租户权限
# 设计未决而延后，SDD 开放问题 #2）。写入时确定性打标，检索按 scope 路由。
_SCOPE_USER_TYPES = {"user_preference", "preference"}
_SCOPE_AGENT_TYPES: set[str] = set()  # agent-scope writers deferred (permissions open question)

# SDD D7: 受控 type 词表。写入时归一，未知词拒绝；存量杂乱词
# （facts/fact/procedures/procedure/process/auto_sop/patient_medical_fact 并存）
# 通过别名映射在读/写两侧兼容，不迁移存量行。
_TYPE_ALIASES: dict[str, str] = {
    "fact": "fact",
    "facts": "fact",
    "general": "fact",
    "patient_medical_fact": "fact",
    "preference": "preference",
    "user_preference": "preference",
    "constraint": "constraint",
    "procedure": "procedure",
    "procedures": "procedure",
    "process": "procedure",
    "workflow": "procedure",
    "sop": "procedure",
    "auto_sop": "procedure",
    "observation": "observation",
}

CANONICAL_TYPES: frozenset[str] = frozenset(
    {"fact", "preference", "constraint", "procedure", "observation"}
)


def normalize_type(entry_type: str | None) -> str:
    """type 归一（确定性小写 + 别名映射）；未收录词原样返回（写入侧校验拒绝）。"""
    normalized = str(entry_type or "").strip().lower()
    if normalized in _TYPE_ALIASES:
        return _TYPE_ALIASES[normalized]
    return normalized or "fact"


def classify_scope(entry_type: str | None) -> str:
    """Deterministic type→scope mapping for write-time stamping (SDD §5.9).

    user_preference → user; everything else → project (default). The
    agent channel has no writers yet — see module note above.
    """
    normalized = normalize_type(entry_type)
    if normalized in _SCOPE_USER_TYPES:
        return "user"
    if normalized in _SCOPE_AGENT_TYPES:
        return "agent"
    return "project"
