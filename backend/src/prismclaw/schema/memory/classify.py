"""Memory scope classification — write-time stamping (SDD §5.9).

Moved from prismclaw.persistence.memory.base to break the engine→persistence
dependency for pure data-model imports.
"""

from __future__ import annotations

# SDD B5 (docs/memory-dream-sdd.md §5.9): scope 默认二值 — user（用户画像，跨
# 会话）/ project（项目语境，跨会话）。这里只决定默认值；gateway 仲裁允许
# LLM 细化为 user/project/agent（见 engine/memory/gateway.py），故分类侧不
# 保留 agent 分支。
_SCOPE_USER_TYPES = {"preference"}

# SDD D7: 受控 type 词表。未上线、无存量数据，因此不保留历史别名映射：
# 写入边界只接受 CANONICAL_TYPES，未知词由各边界归一（LLM 提取）或拒绝
# （memory 工具）。
# auto_sop 是系统内部类型，不属于模型可写词表：sop_extraction 写入、curator
# 靠它识别可归档/毕业的 SOP 行（见 persistence/curator/repository.py）。两侧
# 必须引用同一常量，否则 curator 会静默漏掉全部新 SOP。
SOP_MEMORY_TYPE = "auto_sop"

# 模型可写词表（memory.save 的 enum 与 capture 提取校验共用）。auto_sop 不
# 在其中：模型无权把任意记忆标记为 SOP，否则会误触发 curator 归档/毕业。
CANONICAL_TYPES: frozenset[str] = frozenset(
    {"fact", "preference", "constraint", "procedure", "observation", "decision"}
)


def normalize_type(entry_type: str | None) -> str:
    """大小写/空白归一 + 受控词表强校验 + 缺省 fact。

    非兼容变更：非法值直接 raise ValueError，不再让各调用方分散 check。
    所有写入路径（memory_tools / capture / dream / sop_extraction /
    _row_mapping）统一走这一个入口，单点把关。
    """
    normalized = str(entry_type or "").strip().lower()
    if not normalized:
        return "fact"
    if normalized in CANONICAL_TYPES or normalized == SOP_MEMORY_TYPE:
        return normalized
    raise ValueError(
        f"未知 memory type: {normalized!r}（允许值：{', '.join(sorted(CANONICAL_TYPES))}）"
    )


def classify_scope(entry_type: str | None) -> str:
    """Deterministic type→scope mapping for write-time stamping (SDD §5.9).

    Deterministic default only: preference → user; everything else → project.
    Gateway arbitration may refine it afterwards — see module note above.
    """
    return "user" if normalize_type(entry_type) in _SCOPE_USER_TYPES else "project"
