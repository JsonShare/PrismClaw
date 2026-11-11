"""核心领域枚举与语义化常量集合。

本模块集中保存原先以裸字符串散落在生产代码各处的领域词汇：
* Skill source / invocation policy / execution mode / plugin error type
* SKILL.md frontmatter 字段名（含 metadata.prismclaw 嵌套字段）
* capability graph 节点 metadata 的字典键
* namespace / run_id / session_id 的前缀集合

统一使用 ``(str, Enum)`` 继承：
* 100% 向下兼容字符串——``Enum.VALUE == "value"`` 返回 ``True``
* 无需 ``.value`` 即可用于 JSON/Pydantic 序列化（Python 3.10 兼容；
  后续升级到 3.11+ 可将 ``(str, Enum)`` 替换为 ``StrEnum``。
"""

from __future__ import annotations

from enum import Enum
from typing import Any


# ==========================================================================
# 1. Skill 来源（SkillManifest.source 与 classify_skill_origin() 共用
# ==========================================================================
class SkillSource(str, Enum):
    """SkillManifest.source 与 origin 字段共用词汇。

    历史命名混乱说明：
    * source 是 SkillManifest 存储字段：bundled/mcp/plugin
    * origin 是 Web 层 / classify_skill_origin 对外分组字段：
      bundled/personal/managed/project/mcp/plugin
    合并为一个 Enum：新增时补全即可，避免两套映射。
    """

    BUNDLED = "bundled"
    PLUGIN = "plugin"
    MCP = "mcp"
    PROJECT = "project"
    PERSONAL = "personal"
    MANAGED = "managed"
    MARKETPLACE = "marketplace"
    WORKSPACE = "workspace"
    BUILTIN = "builtin"

    @classmethod
    def _missing_(cls, value: Any) -> "SkillSource | None":
        if isinstance(value, str):
            norm = value.strip().lower().replace("-", "_")
            for member in cls:
                if member.value == norm:
                    return member
        return None

    @classmethod
    def coerce(cls, raw: Any, default: "SkillSource | None" = None) -> "SkillSource | None":
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
class InvocationPolicy(str, Enum):
    AUTO = "auto"
    USER_ONLY = "user_only"
    MODEL_ONLY = "model_only"
    DISABLED = "disabled"

    @classmethod
    def _missing_(cls, value: Any) -> "InvocationPolicy | None":
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

    @classmethod
    def coerce(cls, raw: Any) -> "InvocationPolicy":
        """parser 入口：任何异常或 None → AUTO。"""
        if isinstance(raw, cls):
            return raw
        member = cls._missing_(raw)
        return member if member is not None else cls.AUTO


# ==========================================================================
# 3. Skill execution mode：inline / fork / subagent（含同义词映射）
# ==========================================================================
class SkillExecutionMode(str, Enum):
    INLINE = "inline"
    FORK = "fork"
    SUBAGENT = "subagent"

    @classmethod
    def _missing_(cls, value: Any) -> "SkillExecutionMode | None":
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


# marketplace 历史同义词 → 规范值映射（parser 中 _parse_execution_mode 用;
# SkillExecutionMode._missing_ 也复用同一张表,确保 SkillExecutionMode("agent") 直接返回 SUBAGENT.
EXECUTION_MODE_SYNONYMS: dict[str, SkillExecutionMode] = {
    "agent": SkillExecutionMode.SUBAGENT,
    "workflow": SkillExecutionMode.FORK,
    "sub-agent": SkillExecutionMode.SUBAGENT,
    "sub_agent": SkillExecutionMode.SUBAGENT,
    "forked": SkillExecutionMode.FORK,
}


def _exe_mode_normalize(raw: Any) -> SkillExecutionMode:
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
# 4. Plugin error type：兼容 v3 常量与 v4 多子类规范值
# ==========================================================================
class PluginErrorType(str, Enum):
    """PluginError.type 全量规范值。

    消费端分支时用语义化 helper（is_manifest / is_dependency）即可。
    """

    # 多子类规范值
    MANIFEST_PARSE = "manifest_parse"
    MANIFEST_VALIDATE = "manifest_validate"
    DEPENDENCY = "dependency"
    ACTIVATION = "activation"
    ACTIVATION_RUNTIME = "activation_runtime"

    # 错误类型常量
    MANIFEST_PARSE_ERROR = "manifest_parse_error"
    HOOK_LOAD_FAILED = "hook_load_failed"
    MCP_CONFIG_INVALID = "mcp_config_invalid"
    PLUGIN_NOT_FOUND = "plugin_not_found"
    MARKETPLACE_BLOCKED_BY_POLICY = "marketplace_blocked_by_policy"
    PLUGIN_CACHE_MISS = "plugin_cache_miss"
    PATH_NOT_FOUND = "path_not_found"
    DEPENDENCY_MISSING = "dependency_missing"
    CIRCULAR_DEPENDENCY = "circular_dependency"
    CROSS_MARKETPLACE_DEPENDENCY = "cross_marketplace_dependency"
    GENERIC_ERROR = "generic_error"

    @classmethod
    def is_manifest(cls, err_type: Any) -> bool:
        return err_type in {cls.MANIFEST_PARSE, cls.MANIFEST_VALIDATE, cls.MANIFEST_PARSE_ERROR}

    @classmethod
    def is_dependency(cls, err_type: Any) -> bool:
        return err_type in {cls.DEPENDENCY, cls.DEPENDENCY_MISSING,
                          cls.CIRCULAR_DEPENDENCY, cls.CROSS_MARKETPLACE_DEPENDENCY}


# ==========================================================================
# 5. SKILL.md frontmatter 字段名常量（parser 中禁止再写裸字符串）
# ==========================================================================
class FM(str, Enum):
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
    EDITABLE = "editable"
    ALLOWED_TOOLS = "allowed_tools"
    AGENT = "agent"
    SUBAGENT = "subagent"
    A__NAME = "name"
    A__DESCRIPTION = "description"
    A__MODEL = "model"
    A__ALLOWED_TOOLS = "allowed_tools"
    A__CAN_HANDOFF_TO = "can_handoff_to"
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
# 6. namespace / run_id / session_id 前缀
# ==========================================================================
class NP(str, Enum):
    """各类 id/命名空间前缀。

    用例：NP.SESSION.matches(x) 代替 x.startswith("session:")
    """
    SESSION = "session:"
    MANAGED = "managed:"
    PLUGIN = "plugin:"
    WEB = "web:"
    TAKEOVER = "takeover:"

    def matches(self, s: Any) -> bool:
        return bool(s and isinstance(s, str) and s.startswith(self.value))

    @classmethod
    def classify(cls, s: Any) -> "NP | None":
        """返回前缀匹配的第一个 NP 成员;若都不匹配返回 None."""
        if not isinstance(s, str) or not s:
            return None
        for member in cls:
            if s.startswith(member.value):
                return member
        return None


# ==========================================================================
# 7. CapabilityNode.metadata 字典键
# ==========================================================================
class MK(str, Enum):
    """CapabilityNode.metadata 语义化键。"""
    PLUGIN_NAME = "plugin_name"
    PLUGIN_ID = "plugin_id"
    PLUGIN_SOURCE = "plugin_source"
    PLUGIN_VERSION = "plugin_version"
    ORIGIN = "origin"
    MANIFEST = "manifest"
    INVOCATION_POLICY = "invocation_policy"
    SOURCE_FILE = "source_file"
    SKILL_FILE_PATH = "skill_file_path"
    SKILL_GROUP_PATH = "skill_group_path"
    RUN_ONCE_KEY = "run_once_key"
    CAPABILITY = "capability"


# ==========================================================================
# 8. Plugin discovery 源
# ==========================================================================
class PDS(str, Enum):
    """Plugin discovery source（discovery_engine 入口分类。"""
    BUILTIN = "builtin"
    MARKETPLACE = "marketplace"
    SESSION = "session"
    WORKSPACE = "workspace"
    PERSONAL = "personal"


def normalize_key(raw: Any) -> str:
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
    "PluginErrorType",
    "FM",
    "NP",
    "MK",
    "PDS",
    "normalize_key",
    "SS",
    "IP",
    "SEM",
]
