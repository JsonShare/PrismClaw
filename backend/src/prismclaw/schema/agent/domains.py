"""核心领域枚举与语义化常量集合。

本模块集中保存原先以裸字符串散落在生产代码各处的领域词汇：
* Skill source / invocation policy / execution mode / expert error type
* SKILL.md frontmatter 字段名（含 metadata.prismclaw 嵌套字段）
* namespace / run_id / session_id 的前缀集合

统一使用 ``(str, Enum)`` 继承：
* 100% 向下兼容字符串——``Enum.VALUE == "value"`` 返回 ``True``
* 无需 ``.value`` 即可用于 JSON/Pydantic 序列化（Python 3.10 兼容；
  后续升级到 3.11+ 可将 ``(str, Enum)`` 替换为 ``StrEnum``。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from enum import StrEnum

logger = logging.getLogger(__name__)


# ==========================================================================
# 1. Skill 来源（SkillManifest.source 与 classify_skill_origin() 共用
# ==========================================================================
class SkillSource(StrEnum):
    """SkillManifest.source 与 origin 字段共用词汇。

    历史命名混乱说明：
    * source 是 SkillManifest 存储字段：bundled/mcp/expert
    * origin 是 Web 层 / classify_skill_origin 对外分组字段：
      bundled/personal/managed/project/mcp/expert
    合并为一个 Enum：新增时补全即可，避免两套映射。
    """

    BUNDLED = "bundled"
    EXPERT = "expert"
    MCP = "mcp"
    PROJECT = "project"
    PERSONAL = "personal"
    MANAGED = "managed"
    WORKSPACE = "workspace"
    BUILTIN = "builtin"

    @classmethod
    def _missing_(cls, value: object) -> SkillSource | None:
        if isinstance(value, str):
            norm = value.strip().lower().replace("-", "_")
            for member in cls:
                if member.value == norm:
                    return member
        return None

    @classmethod
    def coerce(cls, raw: object, default: SkillSource | None = None) -> SkillSource | None:
        """origin 入口：字符串/枚举都归一化,失败返回 default。"""
        if isinstance(raw, cls):
            return raw
        member = cls._missing_(raw)
        if member is not None:
            return member
        return default

    @classmethod
    def valid_values(cls) -> tuple[str, ...]:
        return tuple(m.value for m in cls)


# ==========================================================================
# 2. Invocation Policy：技能准入策略
# ==========================================================================
class InvocationPolicy(StrEnum):
    AUTO = "auto"
    USER_ONLY = "user_only"
    MODEL_ONLY = "model_only"
    DISABLED = "disabled"

    @classmethod
    def _missing_(cls, value: object) -> InvocationPolicy | None:
        # None / 非字符串 → AUTO
        if value is None:
            return cls.AUTO
        if not isinstance(value, str):
            return cls.AUTO
        norm = value.strip().lower().replace("-", "_")
        if not norm:
            return cls.AUTO
        for member in cls:
            if member.value == norm:
                return member
        return None


def resolve_invocation_policy(frontmatter: Mapping[str, object]) -> InvocationPolicy:
    """解析 SKILL.md 调用策略。

    显式 ``InvocationPolicy`` 字段优先；否则由 disable / user_invocable /
    disable_model_invocation 三个布尔字段派生。
    """
    raw = frontmatter.get(FM.INVOCATION_POLICY)
    if raw is not None:
        if isinstance(raw, InvocationPolicy):
            return raw
        if isinstance(raw, str):
            member = InvocationPolicy._missing_(raw)
            if member is not None:
                return member
        logger.warning("Unknown invocation_policy %r; disabling skill", raw)
        return InvocationPolicy.DISABLED

    def _flag(name: FM, default: bool) -> bool:
        value = frontmatter.get(name)
        return value if isinstance(value, bool) else default

    disable = _flag(FM.DISABLE, False)
    user_invocable = _flag(FM.USER_INVOCABLE, True)
    disable_model = _flag(FM.DISABLE_MODEL_INVOCATION, False)
    if disable or (not user_invocable and disable_model):
        return InvocationPolicy.DISABLED
    if not user_invocable:
        return InvocationPolicy.MODEL_ONLY
    if disable_model:
        return InvocationPolicy.USER_ONLY
    return InvocationPolicy.AUTO


# ==========================================================================
# 3. Skill execution mode：inline / fork / subagent（含同义词映射）
# ==========================================================================
class SkillExecutionMode(StrEnum):
    INLINE = "inline"
    FORK = "fork"
    SUBAGENT = "subagent"

    @classmethod
    def _missing_(cls, value: object) -> SkillExecutionMode | None:
        """用 EXECUTION_MODE_SYNONYMS 支持 agent/sub-agent/workflow 等同义词。

        注:EXECUTION_MODE_SYNONYMS 定义在类体之后(因为需要引用 cls 成员),
        但 _missing_ 是运行时调用,因此全局查找没问题。
        """
        if not isinstance(value, str):
            return cls.INLINE
        norm = value.strip().lower().replace("-", "_")
        if not norm:
            return cls.INLINE
        # 1) 标准值命中
        for member in cls:
            if member.value == norm:
                return member
        # 2) 同义词命中(查模块级字典,去掉 hyphen→underscore 后的 key 也查一次)
        syns: dict[str, SkillExecutionMode] = EXECUTION_MODE_SYNONYMS
        if norm in syns:
            return syns[norm]
        # 含 hyphen 的原写形式(EXECTION_MODE_SYNONYMS 直接写 "sub-agent" key 也查)
        raw_norm = value.strip().lower()
        if raw_norm in syns:
            return syns[raw_norm]
        return None


# execution_mode 解析别名 → 规范值映射（parser 的 _parse_execution_mode 与
# SkillExecutionMode._missing_ 共用同一张表，确保 SkillExecutionMode("agent")
# 直接返回 SUBAGENT）。
EXECUTION_MODE_SYNONYMS: dict[str, SkillExecutionMode] = {
    "agent": SkillExecutionMode.SUBAGENT,
    "workflow": SkillExecutionMode.FORK,
    "sub-agent": SkillExecutionMode.SUBAGENT,
    "sub_agent": SkillExecutionMode.SUBAGENT,
    "forked": SkillExecutionMode.FORK,
}


def _exe_mode_normalize(raw: object) -> SkillExecutionMode:
    if isinstance(raw, SkillExecutionMode):
        return raw
    if not isinstance(raw, str):
        return SkillExecutionMode.INLINE
    norm = raw.strip().lower().replace("-", "_")
    if not norm:
        return SkillExecutionMode.INLINE
    syn = EXECUTION_MODE_SYNONYMS.get(norm)
    if syn is not None:
        return syn
    for m in SkillExecutionMode:
        if m.value == norm:
            return m
    return SkillExecutionMode.INLINE


def exe_mode_valid_values() -> tuple[str, ...]:
    """Parser 错误消息里打印的「合法值 + 同义词」列表"""
    return tuple(sorted({*(m.value for m in SkillExecutionMode), *EXECUTION_MODE_SYNONYMS.keys()}))


# ==========================================================================
# 4. 专家包加载错误类型
# ==========================================================================
class PackageErrorType(StrEnum):
    """``PackageError.type`` 的规范取值。"""

    MANIFEST_PARSE = "manifest_parse"
    MANIFEST_VALIDATE = "manifest_validate"
    DEPENDENCY = "dependency"
    ACTIVATION = "activation"
    ACTIVATION_RUNTIME = "activation_runtime"


# ==========================================================================
# 5. SKILL.md frontmatter 字段名常量（parser 中禁止再写裸字符串）
# ==========================================================================
class FM(StrEnum):
    """SKILL.md frontmatter 字段名（含 nested metadata.prismclaw.*）。

    命名规范：A__* 是 agent 子键，META__* 是 metadata.prismclaw.* 子键，
    R__* 是 metadata.prismclaw.requires.* 子键。
    """

    NAME = "name"
    DESCRIPTION = "description"
    BODY = "body"
    CATEGORY = "category"
    PATHS = "paths"
    EXECUTION_MODE = "execution_mode"
    INVOCATION_POLICY = "invocation_policy"
    DISABLE = "disable"
    DISABLE_MODEL_INVOCATION = "disable-model-invocation"
    USER_INVOCABLE = "user-invocable"
    EDITABLE = "editable"
    ALLOWED_TOOLS = "allowed_tools"
    AGENT = "agent"
    SUBAGENT = "subagent"
    A__NAME = "name"
    A__DESCRIPTION = "description"
    A__MODEL = "model"
    A__MAX_TURNS = "max_turns"
    A__TIMEOUT_SECONDS = "timeout_seconds"
    META = "metadata"
    META__PRISMCLAW = "prismclaw"
    META__REQUIRES = "requires"
    META__EMOJI = "emoji"
    META__EXECUTION_MODE = "execution_mode"
    META__ALLOWED_TOOLS = "allowed_tools"
    META__OS = "os"
    R__BINS = "bins"
    R__ANY_BINS = "any_bins"
    R__ENV = "env"
    ARGUMENT_HINT = "argument_hint"
    REFERENCES = "references"
    TAGS = "tags"
    AUTHOR = "author"
    VERSION = "version"
    PRIORITY = "priority"
    CUSTOMIZES = "customizes"


# ==========================================================================
# 6. Expert discovery 源
# ==========================================================================
class PDS(StrEnum):
    """Expert discovery source（discovery_engine 入口分类。"""

    BUILTIN = "builtin"
    SESSION = "session"
    WORKSPACE = "workspace"
    PERSONAL = "personal"


def normalize_key(raw: object) -> str:
    """Enum ``_missing_`` 共用 helper：小写 + kebab→snake + strip"""
    if not isinstance(raw, str):
        return ""
    return raw.strip().lower().replace("-", "_")


# 为消费端降低心智负担的短别名（保持类型相同）
SS: type[SkillSource] = SkillSource
IP: type[InvocationPolicy] = InvocationPolicy
SEM: type[SkillExecutionMode] = SkillExecutionMode


__all__ = [
    "SkillSource",
    "InvocationPolicy",
    "SkillExecutionMode",
    "EXECUTION_MODE_SYNONYMS",
    "_exe_mode_normalize",
    "exe_mode_valid_values",
    "PackageErrorType",
    "FM",
    "PDS",
    "normalize_key",
    "SS",
    "IP",
    "SEM",
]
