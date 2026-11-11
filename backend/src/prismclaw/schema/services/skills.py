"""Skills subsystem contracts.

Protocols for skill invocation and descriptor access,
and shared Pydantic data models moved from skills.models to break
the engine → skills import dependency.
"""
from __future__ import annotations

from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from prismclaw.schema.agent.domains import (
    InvocationPolicy,
    SkillExecutionMode,
    SkillSource,
)


# ---------------------------------------------------------------------------
# Protocol definitions
# ---------------------------------------------------------------------------

class SkillOriginDescriptorProtocol(Protocol):
    """Duck-typing compatible with skills.origin.SkillOriginDescriptor."""

    @property
    def origin(self) -> str | None: ...

    @property
    def plugin_name(self) -> str | None: ...


# ---------------------------------------------------------------------------
# Shared data models (moved from skills.models to break engine → skills dep)
# ---------------------------------------------------------------------------

class SkillInvocation(BaseModel):
    skill_name: str
    task_context: str
    args: dict[str, Any] = Field(default_factory=dict)
    source: Literal["model", "user", "system"] = "model"


class SkillInvocationState(BaseModel):
    skill_name: str
    allowed_tools_override: list[str] | None = None
    is_active: bool = False
    clear_after_terminal_result: bool = True
    # ---- 方案 C:scope/plugin 一等支持 ----
    # 当前 skill 属于哪个 plugin(若不是 plugin 技能则为 None),
    # 用于在下一层技能 resolve 时优先命中同 plugin 内版本的 command/skill,
    # 避免跨 plugin 重名(cold-start-interview/matter-workspace 等)导致的错 body 命中。
    plugin_name: str | None = None
    # ---- command/skill 统一建模 ----
    # 区分当前调用来源于技能激活还是命令 dispatch:
    #   "skill" — 标准技能激活路径 (SkillRuntime.build_skill_execution)
    #   "command" — 命令 dispatch 路径 (PluginCommandRuntime.dispatch)
    # hook matcher 只看 skill_name，不受此字段影响;
    # governance 和 system_prompt 层据此区分行为(命令不触发 skill-gated 解锁等)。
    invocation_context: Literal["skill", "command"] = "skill"
    # ---- 编排遵从度埋点:执行期 ----
    # 当前 skill 是工作流编排类技能(body 内声明了子技能调用序列)时为 True
    is_workflow: bool = False
    # 加载期预提取的声明序列副本(=SkillManifest.declared_invoke_sequence)
    # 写入后不再变化,与 actual 配对供 compute_workflow_compliance 使用
    declared_invoke_sequence: list[str] = Field(default_factory=list)
    # 实际调用的子技能 name 序列,调用一次 append 一次(可含重复名)
    actual_invoke_sequence: list[str] = Field(default_factory=list)
    # 执行期结束时 compute_workflow_compliance(declared,actual) 的结果快照
    # workflow 技能会填充;非 workflow 技能为 None(不参与遵从度统计)
    compliance_result: dict[str, Any] | None = None


class SkillExecutionStrategy(BaseModel):
    skill_name: str
    execution_mode: SkillExecutionMode = SkillExecutionMode.INLINE
    allowed_tools: list[str] | None = None
    preload_skills: list[str] = Field(default_factory=list)
    subagent_manifest: dict[str, Any] | None = None
    append_messages: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""
    invocation_state: SkillInvocationState | None = None
    # Step 1-9: 执业档案 / plugin 传播字段
    # 本 skill 属于哪个 plugin(仅当 source=='plugin' 时非空,取自 loaded_from)
    plugin_name: str | None = None
    # 执业档案原始文本( <plugins>/<plugin>/prismclaw.md ),
    # 读入后已存入 append_messages 的一条消息中,这里保留副本方便 audit/debug。
    plugin_profile_text: str | None = None
    # preload body 注入使用的字符预算上限(默认 30K, 硬截断)
    preload_budget_chars: int = 30000


class SkillRequirements(BaseModel):
    bins: list[str] = Field(default_factory=list)
    any_bins: list[str] = Field(default_factory=list)
    env: list[str] = Field(default_factory=list)
    os: list[str] = Field(default_factory=list)


class SkillAgentConfig(BaseModel):
    name: str | None = None
    description: str = ""
    model: str | None = None
    allowed_tools: list[str] | None = None
    max_turns: int | None = None
    timeout_seconds: float | None = None
    preload_skills: list[str] = Field(default_factory=list)
    can_handoff_to: list[str] | None = None


class SkillManifest(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    name: str
    # 中文显示名（frontmatter name_zh）。name 仍是 ASCII 标识符，用于目录键、
    # 斜杠手势与 hook matcher；本字段只供 UI 与提示词展示，不参与任何查找。
    name_zh: str | None = None
    description: str = ""
    # None = 未加载（frontmatter-only 扫描，渐进披露）；"" = 已加载且文件无正文
    body: str | None = None
    file_path: str = ""
    allowed_tools: list[str] | None = None
    execution_mode: SkillExecutionMode = SkillExecutionMode.INLINE
    preload_skills: list[str] = Field(default_factory=list)
    agent_config: SkillAgentConfig | None = None
    requirements: SkillRequirements = Field(default_factory=SkillRequirements)
    emoji: str | None = None
    invocation_policy: InvocationPolicy = InvocationPolicy.AUTO
    paths: list[str] | None = None
    category: str | None = None
    source: SkillSource = SkillSource.BUNDLED
    loaded_from: str = ""
    priority: int = Field(default=0, description="Higher = more important in budget allocation")
    editable: bool = Field(default=False, description="When False (default) the skill cannot be edited via the skill editor; set 'editable: true' in SKILL.md frontmatter to opt in")
    customizes: str | None = Field(default=None, description="For customize commands: the sub-plugin this command customizes")
    # ---- 编排遵从度埋点:加载期预提取 ----
    # body 内显式声明的子技能调用名序列,按出现顺序
    declared_invoke_sequence: list[str] = Field(default_factory=list)
    # ---- v5 渐进式披露:延迟 content pipeline 参数 ----
    # frontmatter-only 模式下 body=""，pipeline 参数暂存于此，
    # body 按需加载时从 dict 取出重新执行 _apply_content_pipeline。
    # 纯 JSON 可序列化 dict，不含闭包。默认空 dict 表示无延迟 pipeline。
    pipeline_context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source", mode="before")
    @classmethod
    def _coerce_source_before(cls, v: Any) -> Any:
        if isinstance(v, SkillSource):
            return v
        coerced = SkillSource.coerce(v)
        return coerced if coerced is not None else SkillSource.BUNDLED

    @field_validator("source", mode="after")
    @classmethod
    def _coerce_source_after(cls, v: Any) -> SkillSource:
        if isinstance(v, SkillSource):
            return v
        coerced = SkillSource.coerce(v)
        return coerced if coerced is not None else SkillSource.BUNDLED

    @field_serializer("source")
    def _serialize_source_safe(self, v: Any) -> SkillSource:
        if isinstance(v, SkillSource):
            return v
        coerced = SkillSource.coerce(v)
        return coerced if coerced is not None else SkillSource.BUNDLED

    @field_serializer("execution_mode")
    def _serialize_execution_mode_safe(self, v: Any) -> SkillExecutionMode:
        if isinstance(v, SkillExecutionMode):
            return v
        try:
            return SkillExecutionMode(v)
        except (ValueError, TypeError):
            return SkillExecutionMode.INLINE

    @field_serializer("invocation_policy")
    def _serialize_invocation_policy_safe(self, v: Any) -> InvocationPolicy:
        if isinstance(v, InvocationPolicy):
            return v
        try:
            return InvocationPolicy(v)
        except (ValueError, TypeError):
            return InvocationPolicy.AUTO


# ---------------------------------------------------------------------------
# Skill name resolver (moved from skills.resolver to break engine → skills dep)
# ---------------------------------------------------------------------------

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ResolvableEntry:
    """Minimal interface needed for resolution: a canonical ``name``."""

    name: str


def normalize_skill_ref(name: str) -> str:
    """Normalize a skill reference to its comparable form.

    Strips surrounding whitespace and any leading slashes so command-style
    references (``/xlsx``, ``//a:b``) compare equal to their canonical catalog
    names (``xlsx``, ``a:b``). Internal structure is preserved: a namespaced
    ``plugin:skill`` keeps its colon, and only the *leading* slash that the
    reference implementation uses as a command prefix is removed.

    A canonical reference is always a single space-free token, so anything past
    the first whitespace run is stray argument noise a model may have appended
    (``xlsx switch acme`` → ``xlsx``) and is discarded. This keeps the cleaning
    at the one designated rewrite gate rather than scattering it across callers.

    **Separator normalization**: the catalog's canonical namespaced form is
    ``plugin:skill`` (colon) — see :func:`_plugin_capability_id`. Models and
    users frequently type ``plugin.skill`` (dot, a natural namespace separator
    in programming languages), so every ``.`` inside the token is folded to
    ``:`` before the match pass. This is safe because no real skill or plugin
    name contains ``.`` (kebab-case everywhere).

    The operation is total and idempotent:
    ``normalize_skill_ref(normalize_skill_ref(x)) == normalize_skill_ref(x)``.
    """
    stripped = name.strip().lstrip("/")
    if not stripped:
        return ""
    token = stripped.split(None, 1)[0]
    # Fold dot → colon so ``plugin.skill`` resolves against the catalog's
    # canonical ``plugin:skill`` naming. Idempotent (no double-colon risk).
    return token.replace(".", ":")


def resolve_skill_name(
    name: str,
    catalog: Sequence[ResolvableEntry],
) -> str | None:
    """Resolve a skill reference to its canonical catalog name.

    Args:
        name: The reference to resolve (may carry a leading slash / whitespace).
        catalog: Sequence of entries exposing a canonical ``name``.

    Returns:
        The canonical ``name`` of the matched entry, or ``None`` when no entry
        matches. An exact match always wins over a case-insensitive one.
    """
    if not name or not catalog:
        return None

    normalized = normalize_skill_ref(name)
    if not normalized:
        return None

    # Pass 1: exact match on the normalized reference.
    for entry in catalog:
        if entry.name == normalized:
            return entry.name

    # Pass 2: case-insensitive match on the normalized reference.
    folded = normalized.lower()
    for entry in catalog:
        if entry.name.lower() == folded:
            return entry.name

    return None


# ---------------------------------------------------------------------------
# MCP skill naming (moved from skills.mcp_skills to break engine → skills dep)
# ---------------------------------------------------------------------------

MCP_SKILL_NAME_SEPARATOR = ":"
"""Canonical separator for MCP skill catalog names (``{server}:{prompt}``)."""


def mcp_skill_name(server_name: str, prompt_name: str) -> str:
    """Build the canonical catalog name for an MCP prompt skill."""
    return f"{server_name}{MCP_SKILL_NAME_SEPARATOR}{prompt_name}"


def mcp_prompt_name(skill_name: str, server_name: str) -> str:
    """Recover the raw prompt name from a canonical MCP skill name.

    Raises ``ValueError`` when the name is not namespaced under the given
    server — that indicates a bug in manifest construction, and silently
    passing the un-split name to ``prompts/get`` would fail confusingly.
    """
    prefix = f"{server_name}{MCP_SKILL_NAME_SEPARATOR}"
    if not skill_name.startswith(prefix):
        raise ValueError(
            f"MCP skill name {skill_name!r} is not namespaced under "
            f"server {server_name!r} (expected prefix {prefix!r})"
        )
    return skill_name[len(prefix):]


# Signature: (server_name, prompt_name, arguments, *, timeout_s=...) -> rendered text.
McpPromptResolver = Callable[..., str]
