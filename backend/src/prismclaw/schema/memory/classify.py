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
    """大小写/空白归一 + 缺省 fact；不做别名映射——词表即契约。"""
    normalized = str(entry_type or "").strip().lower()
    return normalized or "fact"


def classify_scope(entry_type: str | None) -> str:
    """Deterministic type→scope mapping for write-time stamping (SDD §5.9).

    Deterministic default only: preference → user; everything else → project.
    Gateway arbitration may refine it afterwards — see module note above.
    """
    return "user" if normalize_type(entry_type) in _SCOPE_USER_TYPES else "project"
