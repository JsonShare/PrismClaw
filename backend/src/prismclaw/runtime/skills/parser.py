from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from prismclaw.schema.agent.domains import (
    FM,
    InvocationPolicy,
    SkillExecutionMode,
    _exe_mode_normalize,
    exe_mode_valid_values,
)
from prismclaw.schema.shared import _extract_frontmatter
from prismclaw.schema.services.skills import (
    SkillAgentConfig,
    SkillManifest,
    SkillRequirements,
)
from prismclaw.schema.shared.exceptions import SkillParseError

logger = logging.getLogger(__name__)


__all__ = ["parse_skill_file", "parse_skill_frontmatter", "_extract_frontmatter"]


def _parse_invocation_policy(frontmatter: dict[str, Any]) -> InvocationPolicy:
    """从 frontmatter 读 invocation_policy,返回规范枚举值。

    兼容:frontmatter 里的 kebab-case(user-only) 会归一化,未写或空 → AUTO。
    """
    return InvocationPolicy.coerce(frontmatter.get(FM.INVOCATION_POLICY))


def _parse_requirements(skill_meta: dict[str, Any]) -> SkillRequirements:
    requires: dict[str, Any] = skill_meta.get(FM.META__REQUIRES, {}) or {}
    return SkillRequirements(
        bins=requires.get(FM.R__BINS, []),
        any_bins=requires.get(FM.R__ANY_BINS, []),
        env=requires.get(FM.R__ENV, []),
        os=skill_meta.get(FM.META__OS, []),
    )


def _coerce_string_list(raw: Any) -> list[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        return None
    values = [item.strip() for item in raw if isinstance(item, str) and item.strip()]
    return values or None


def _coerce_optional_int(raw: Any) -> int | None:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


def _coerce_optional_float(raw: Any) -> float | None:
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    return None


def _parse_execution_mode(
    frontmatter: dict[str, Any],
    skill_meta: dict[str, Any],
    file_path_str: str,
) -> SkillExecutionMode:
    """读 execution_mode(含 metadata.prismclaw.execution_mode)并归一化为 SkillExecutionMode 枚举。

    兼容同义词(agent→SUBAGENT / workflow→FORK / sub-agent / forked 等);若值非法则抛
    SkillParseError,输出信息里直接列出合法值+同义词。
    """
    raw = (
        frontmatter.get(FM.EXECUTION_MODE)
        or skill_meta.get(FM.META__EXECUTION_MODE)
        or SkillExecutionMode.INLINE
    )
    mode = _exe_mode_normalize(raw)
    if isinstance(raw, str) and raw.strip():
        norm = raw.strip().lower().replace("-", "_")
        # _exe_mode_normalize 对任何输入都有返回,若既不是规范值也不在 synonym 中 → INLINE(兜底)
        # 这里要判断兜底是不是非法值:既不是规范值也不是 synonym 也不是空
        if mode is SkillExecutionMode.INLINE and norm != "inline":
            is_synonym = norm in {k.replace("-", "_") for k in ("agent", "workflow",
                                                               "sub-agent", "sub_agent",
                                                               "forked")}
            if not is_synonym:
                valid = ", ".join(exe_mode_valid_values())
                raise SkillParseError(
                    f"execution_mode must be one of: {valid} "
                    f"(got {raw!r})",
                    file_path=file_path_str,
                )
    return mode


def _parse_agent_config(
    frontmatter: dict[str, Any], skill_meta: dict[str, Any]
) -> SkillAgentConfig | None:
    raw_agent = (
        frontmatter.get(FM.AGENT)
        or frontmatter.get(FM.SUBAGENT)
        or skill_meta.get(FM.AGENT)
        or skill_meta.get(FM.SUBAGENT)
    )
    if raw_agent is not None and not isinstance(raw_agent, dict):
        return None
    agent = dict(raw_agent or {})
    allowed_tools = _coerce_string_list(agent.get(FM.A__ALLOWED_TOOLS))
    can_handoff_to = _coerce_string_list(agent.get(FM.A__CAN_HANDOFF_TO))
    name_val = agent.get(FM.A__NAME)
    desc_val = agent.get(FM.A__DESCRIPTION)
    model_val = agent.get(FM.A__MODEL)
    max_turns_val = agent.get(FM.A__MAX_TURNS)
    timeout_val = agent.get(FM.A__TIMEOUT_SECONDS)
    has_explicit_agent_fields = any(
        value is not None
        for value in (
            name_val,
            desc_val,
            model_val,
            max_turns_val,
            timeout_val,
            allowed_tools,
            can_handoff_to,
        )
    )
    if not has_explicit_agent_fields:
        return None
    return SkillAgentConfig(
        name=name_val if isinstance(name_val, str) else None,
        description=desc_val if isinstance(desc_val, str) else "",
        model=model_val if isinstance(model_val, str) else None,
        allowed_tools=allowed_tools,
        max_turns=_coerce_optional_int(max_turns_val),
        timeout_seconds=_coerce_optional_float(timeout_val),
        # 语义分工:由外层 parse_skill_file 在拿到 declared_invoke_sequence 后同步赋值,
        # 不在此内部直接算,避免重复解析 body。空值仅为兜底占位。
        preload_skills=[],
        can_handoff_to=can_handoff_to,
    )


_PARSE_CACHE_MAX = 512
# (resolved_path, mtime_ns, size) → (frontmatter, body, file_path_str)
# list_skills() 每轮都会被能力装配/技能激活调用；技能文件在运行期几乎不变，
# 用 (mtime, size) 作为失效键可以跳过重复的 read_text + YAML 解析。
_parse_cache: dict[tuple[str, int, int], tuple[dict[str, Any], str, str]] = {}


def _parse_skill_common(
    path: str | Path,
    *,
    fallback_name: str | None = None,
) -> tuple[dict[str, Any], str, str]:
    """读取 SKILL.md 并解析 frontmatter，返回 (frontmatter_dict, body, file_path_str)。

    被 parse_skill_file 和 parse_skill_frontmatter 共享，消除重复。
    命中 _parse_cache 时返回 frontmatter 的浅拷贝——上层构建 manifest 时只读
    该 dict，浅拷贝避免多个 manifest 共享同一份可变映射。
    """
    resolved = Path(path).resolve()
    file_path_str = str(resolved)

    try:
        stat = resolved.stat()
        cache_key: tuple[str, int, int] | None = (
            file_path_str,
            stat.st_mtime_ns,
            stat.st_size,
        )
    except OSError:
        cache_key = None

    if cache_key is not None:
        cached = _parse_cache.get(cache_key)
        if cached is not None:
            return dict(cached[0]), cached[1], cached[2]

    try:
        content = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillParseError(str(exc), file_path=file_path_str) from exc

    try:
        frontmatter_str, body = _extract_frontmatter(content)
    except SkillParseError as exc:
        raise SkillParseError(exc.message, file_path=file_path_str) from exc

    try:
        frontmatter: dict[str, Any] = yaml.safe_load(frontmatter_str)
    except yaml.YAMLError as exc:
        raise SkillParseError(f"Invalid YAML: {exc}", file_path=file_path_str) from exc

    if not isinstance(frontmatter, dict):
        raise SkillParseError("Frontmatter is not a YAML mapping", file_path=file_path_str)

    if cache_key is not None:
        if len(_parse_cache) >= _PARSE_CACHE_MAX:
            _parse_cache.clear()
        _parse_cache[cache_key] = (frontmatter, body, file_path_str)

    return frontmatter, body, file_path_str


def _build_manifest_from_frontmatter(
    frontmatter: dict[str, Any],
    file_path_str: str,
    *,
    fallback_name: str | None = None,
    parent_dir_name: str | None = None,
body: str | None = None,
        declared_invoke_sequence: list[str] | None = None,
) -> SkillManifest:
    """从已解析的 frontmatter dict 构建 SkillManifest。

    被 parse_skill_file 和 parse_skill_frontmatter 共享，消除重复。
    declared_invoke_sequence=None 时自动从 body 提取。
    """
    explicit_name = frontmatter.get(FM.NAME)
    if isinstance(explicit_name, str) and explicit_name.strip():
        name = explicit_name
    elif fallback_name:
        name = fallback_name
    else:
        name = parent_dir_name or "unknown"
    description: str = frontmatter.get(FM.DESCRIPTION, "")

    skill_meta: dict[str, Any] = (
        (frontmatter.get(FM.META, {}) or {}).get(FM.META__PRISMCLAW, {})
    ) or {}
    _raw_tools = frontmatter.get(FM.ALLOWED_TOOLS) or skill_meta.get(FM.META__ALLOWED_TOOLS)
    if _raw_tools is None and "allowed-tools" in frontmatter:
        _raw_tools = frontmatter.get("allowed-tools")
        logger.info("frontmatter: alias 'allowed-tools' → 'allowed_tools'")
    allowed_tools = _coerce_string_list(_raw_tools)
    execution_mode = _parse_execution_mode(frontmatter, skill_meta, file_path_str)
    agent_config = _parse_agent_config(frontmatter, skill_meta)

    requirements = _parse_requirements(skill_meta)
    emoji: str | None = skill_meta.get(FM.META__EMOJI)
    invocation_policy = _parse_invocation_policy(frontmatter)
    paths = _coerce_string_list(frontmatter.get(FM.PATHS))
    priority = _coerce_optional_int(frontmatter.get(FM.PRIORITY)) or 0
    _raw_editable = frontmatter.get(FM.EDITABLE)
    editable = _raw_editable if isinstance(_raw_editable, bool) else False
    customizes: str | None = frontmatter.get(FM.CUSTOMIZES) or None

    if declared_invoke_sequence is None:
        declared_invoke_sequence = _extract_declared_invoke_sequence(body)

    # ========================================================================
    # 语义分工(严格对应方案 §0.1 决策 #2 + Phase4 遵从度埋点):
    # preload_skills          = 运行时给 LLM 看哪些子技能 body(控制 prompt 注入),
    #                            消费端 → runtime._build_preload_messages (30K 预算)
    # declared_invoke_sequence = 审计用声明清单(跟实际调用比对算 compliance),
    #                            消费端 → compute_workflow_compliance + metrics
    # 当前值从同一来源( body 内的子技能引用提取 )同步得到;
    # 未来若需要"只 preload 前 N 个高频子技能省 token"时,两者可合理分叉。
    # ========================================================================
    preload_skills = list(declared_invoke_sequence)
    if agent_config is not None:
        agent_config.preload_skills = list(preload_skills)

    # 未知字段告警：检测 frontmatter 中不被识别的顶级键
    # name_zh / description_zh 是显示用元数据：写入 SkillManifest.name_zh
    # 供 UI 与提示词展示，不参与目录键、斜杠手势或 hook matcher 的查找。
    _KNOWN_FM_KEYS: set[str] = {
        "name", "description", "body", "category", "paths",
        "execution_mode", "invocation_policy", "allowed_tools", "allowed-tools",
        "agent", "subagent", "metadata", "priority", "customizes", "editable",
        "argument_hint", "references", "tags", "author", "version",
        "name_zh", "description_zh", "homepage", "keywords", "license",
    }
    _unknown = set(frontmatter.keys()) - _KNOWN_FM_KEYS
    if _unknown:
        logger.warning(
            "SKILL %s: unknown frontmatter keys %s will be ignored",
            name, sorted(_unknown),
        )

    return SkillManifest(
        name=name,
        name_zh=frontmatter.get("name_zh"),
        description=description,
        body=body,
        file_path=file_path_str,
        allowed_tools=allowed_tools,
        execution_mode=execution_mode,
        preload_skills=preload_skills,
        agent_config=agent_config,
        requirements=requirements,
        emoji=emoji,
        invocation_policy=invocation_policy,
        paths=paths,
        category=frontmatter.get(FM.CATEGORY),
        declared_invoke_sequence=declared_invoke_sequence,
        priority=priority,
        editable=editable,
        customizes=customizes,
    )


def parse_skill_file(
    path: str | Path,
    *,
    fallback_name: str | None = None,
) -> SkillManifest:
    return _parse_manifest(path, load_body=True, fallback_name=fallback_name)


def parse_skill_frontmatter(
    path: str | Path,
    *,
    fallback_name: str | None = None,
) -> SkillManifest:
    """仅解析 SKILL.md 的 YAML frontmatter，不提取 body 和 declared_invoke_sequence。

    与 parse_skill_file() 的区别：
    - body=None（不读 body 部分，节省解析 + content pipeline 开销；None 表示未加载）
    - declared_invoke_sequence=[]（不从 body 正则提取）
    - preload_skills=[]（与 declared_invoke_sequence 同步为空）

    body 在首次引用时通过 SkillRuntime._ensure_body_loaded() 按需加载。
    """
    return _parse_manifest(path, load_body=False, fallback_name=fallback_name)


# ---------------------------------------------------------------------------
# Manifest 级缓存 — 解析结果的复用边界
# ---------------------------------------------------------------------------
#
# ``_parse_cache`` 只缓存「读文件 + YAML 解析」；每次调用仍会重建 manifest、
# 重跑未知字段告警。``list_skills()`` 是能力装配/技能激活的每轮入口，
# 同一文件在一次进程生命周期里会被反复扫描（日志里 476 条重复
# ``unknown frontmatter keys`` 即此）。
#
# 这一层按 **内容身份**（path, mtime_ns, size, 是否加载 body）缓存**已构建
# 的 manifest**：文件不变则解析、告警、body 提取全部只发生一次；文件一改
# （mtime/size 变化）自动 miss 并重建。缓存值保存一份深拷贝作为「原始
# 快照」，每次返回新的深拷贝——manifest 是可变的（discovery 会写
# source/loaded_from，技能运行时可能更新 body），共享引用会造成跨调用
# 污染。

_MANIFEST_CACHE_MAX = 512
_manifest_cache: dict[tuple[str, int, int, bool], SkillManifest] = {}


def _parse_manifest(
    path: str | Path,
    *,
    load_body: bool,
    fallback_name: str | None,
) -> SkillManifest:
    try:
        resolved = Path(path).resolve()
        stat = resolved.stat()
        cache_key: tuple[str, int, int, bool] | None = (
            str(resolved),
            stat.st_mtime_ns,
            stat.st_size,
            load_body,
        )
    except OSError:
        cache_key = None

    if cache_key is not None:
        cached = _manifest_cache.get(cache_key)
        if cached is not None:
            return cached.model_copy(deep=True)

    frontmatter, body, file_path_str = _parse_skill_common(path, fallback_name=fallback_name)
    manifest = _build_manifest_from_frontmatter(
        frontmatter,
        file_path_str,
        fallback_name=fallback_name,
        parent_dir_name=Path(path).resolve().parent.name,
        body=body if load_body else None,
        declared_invoke_sequence=None if load_body else [],
    )

    if cache_key is not None:
        if len(_manifest_cache) >= _MANIFEST_CACHE_MAX:
            _manifest_cache.clear()
        # 存快照，避免后续调用方对返回对象的原地修改污染缓存。
        _manifest_cache[cache_key] = manifest.model_copy(deep=True)
    return manifest


# ---------------------------------------------------------------------------
# 编排遵从度埋点 — 加载期从 body 预提取声明的子技能调用序列
# ---------------------------------------------------------------------------

_INVOKE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # 0) 中文自然语言:调用技能 / 使用技能 : "contract-review"
    re.compile(r'''(?:调用技能|使用技能)\s*[:：]\s*["']?([A-Za-z0-9_./-]+)'''),
    # 1) Markdown 伪代码箭头:→ contract-review / -> contract-review
    re.compile(r'''(?:→|->|➡️)\s*([A-Za-z][A-Za-z0-9_./-]*)'''),
    # 2) "Step N: <skill-name>" 类枚举式分步(仅匹配以字母开头、且含至少一个 -_/ 的 skill-like 命名)
    re.compile(
        r'''Step\s*\d+\s*[:：]\s*([A-Za-z][A-Za-z0-9]*(?:[-_./][A-Za-z0-9]+)+)'''
    ),
)


# 过滤普通英文词,避免 "Step 3: Review" 等伪声明被误抓
_SKIP_COMMON_ENGLISH: frozenset[str] = frozenset({
    "review", "draft", "check", "update", "submit", "verify",
    "create", "build", "analyze", "close", "open", "write",
    "read", "sign", "file", "send", "reply", "report",
})


def _extract_declared_invoke_sequence(body: str) -> list[str]:
    """从 SKILL.md body 文本预提取显式声明的子技能调用序列。

    提取规则:按 6 组正则依次扫描,命中后按文本位置排序去重相邻同名。
    """
    if not body:
        return []

    hits: list[tuple[int, str]] = []

    for pat in _INVOKE_PATTERNS:
        for m in pat.finditer(body):
            name = m.group(1)
            if not name:
                continue
            if name.isalpha() and name.lower() in _SKIP_COMMON_ENGLISH:
                continue
            hits.append((m.start(1), name))

    hits.sort(key=lambda x: x[0])
    result: list[str] = []
    last_name: str | None = None
    positions = [h[0] for h in hits]
    for idx, (_pos, name) in enumerate(hits):
        if name == last_name and idx > 0:
            # 同一位置附近相邻同名 → 多模式都命中了同一次声明,跳过
            prev_pos = positions[idx - 1] if idx > 0 else -1
            if prev_pos != -1 and (_pos - prev_pos) < 80:
                continue
        result.append(name)
        last_name = name
    return result
