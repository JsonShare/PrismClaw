from __future__ import annotations

import difflib
import logging
from pathlib import Path
from typing import Any

from prismclaw.engine.execution.capabilities.catalog import (
    CapabilityCatalogService,
    SkillResolution,
)
from prismclaw.engine.execution.skills.agent_adapter import (
    agent_manifest_to_skill_payload,
    build_agent_manifest_from_skill,
)
from prismclaw.engine.foundation.metrics import emit as emit_metric
from prismclaw.schema.services.skills import (
    McpPromptResolver,
    SkillExecutionStrategy,
    SkillInvocation,
    SkillInvocationState,
    SkillManifest,
    mcp_prompt_name,
)
from prismclaw.engine.execution.skills.compliance import compute_workflow_compliance
from prismclaw.engine.execution.skills.doc_inline import _inline_referenced_docs
from prismclaw.engine.execution.skills.profile_loader import _load_plugin_profile
from prismclaw.engine.execution.skills.plugin_utils import _resolve_plugin_name
from prismclaw.engine.execution.skills._preload import _build_preload_messages

logger = logging.getLogger(__name__)


def _extract_plugin_name(manifest: Any) -> str | None:
    """从 SkillManifest 解析插件名，兼容 loaded_from 为路径或纯名称两种情况。

    搜索策略：
    1. 非 plugin source → 返回 None
    2. loaded_from 不含路径分隔符 → 直接作为插件名
    3. loaded_from 为路径 → 向上遍历目录层级，通过检测 PRISMCLAW.md 或 .plugin
       子目录定位插件根目录，返回其名称。
    """
    if getattr(manifest, "source", None) != "plugin":
        return None
    raw = getattr(manifest, "loaded_from", None)
    if not raw or not isinstance(raw, str):
        return None

    # Case: loaded_from 是纯名称（不含路径分隔符）
    if "/" not in raw and "\\" not in raw:
        if raw not in {".", "..", "plugin.json", ".plugin", ""}:
            return raw
        return None

    # Case: loaded_from 是文件路径 → 向上搜索插件根目录
    p = Path(raw)
    excluded_dirs = {
        ".", "..", ".plugin", "skills", "agents", "commands",
        "hooks", "safety", "tools", "output-styles", "scripts",
        "config", "managed-agent-cookbooks",
    }
    excluded_files = {"SKILL.md", "plugin.json", ".plugin", "PRISMCLAW.md"}

    depth = 0
    for parent in p.parents:
        depth += 1
        if depth > 8:
            break
        name = parent.name
        if not name or name in excluded_dirs or name in excluded_files:
            continue
        if name.endswith(".md") or name.endswith(".yaml") or name.endswith(".yml"):
            continue
        # 检测该目录是否为插件根目录（包含 PRISMCLAW.md 或 .plugin 子目录）
        has_prismclaw = (parent / "PRISMCLAW.md").is_file()
        has_plugin_dir = (parent / ".plugin").is_dir()
        if has_prismclaw or has_plugin_dir:
            return name
    return None


def _coerce_allowed_tools(raw: Any) -> list[str] | None:
    if raw is None or not isinstance(raw, list):
        return None
    allowed = [item.strip() for item in raw if isinstance(item, str) and item.strip()]
    return allowed or None


def _render_skill_messages(
    *,
    skill_name: str,
    task_context: str,
    instructions: str,
    allowed_tools: list[str] | None,
    skill_dir: str | None = None,
) -> list[dict[str, Any]]:
    lines = [
        f"已激活技能：{skill_name}",
        "你已激活一个专用技能。下方指令是强制性的——"
        "请将其视为当前任务的执行契约。",
        "",
        "硬性规则（必须遵守，没有例外）：",
        "1. 只能使用技能指令中描述的脚本、命令和工作流。"
        "这些是已获授权的实现方式，专为本任务编写。",
        "2. 不要编写自己的脚本替代技能脚本。"
        "例如：docx 技能提供了 runner.js，就不要写 python-docx 代码；"
        "xlsx 技能提供了脚本，就不要写 openpyxl 代码。",
        "3. 即使技能工作流看起来复杂或绕，也要严格遵循——"
        "这种复杂度正是该技能存在的理由。不要为了“简化”而重新造轮子。",
        "4. 执行任何操作前，先阅读技能的参考文件（references/*.md、"
        "assets/schemas/*.md），弄清脚本所需的确切输入。",
    ]
    if skill_dir:
        lines.extend(
            [
                "",
                f"技能目录：{skill_dir}",
                "指令中的所有相对路径（例如 scripts/、references/、"
                "assets/）都相对于该目录。请读取这些文件，"
                "并使用绝对路径运行其中命令（或先 cd 到该目录）；"
                "不要在用户工作区根目录下查找它们。",
            ]
        )
    if allowed_tools:
        lines.append("该技能激活期间只能使用以下工具：" + "、".join(allowed_tools))
    if task_context.strip():
        lines.extend(["", "任务上下文：", task_context.strip()])
    if instructions.strip():
        lines.extend(["", "技能指令：", instructions.strip()])
    return [{"role": "user", "content": "\n".join(lines)}]


def _coerce_skill_manifest(skill: Any) -> SkillManifest:
    if isinstance(skill, SkillManifest):
        return skill
    return SkillManifest.model_validate(skill)



class SkillRuntime:
    def __init__(
        self,
        *,
        capability_catalog: CapabilityCatalogService | None = None,
        mcp_prompt_resolver: McpPromptResolver | None = None,
        descriptor_registry: Any | None = None,
        skill_parser: Any | None = None,
    ) -> None:
        self._capability_catalog = capability_catalog
        self._mcp_prompt_resolver = mcp_prompt_resolver
        self._descriptor_registry = descriptor_registry
        self._skill_parser = skill_parser
        self._plugin_instructions_map: dict[str, str] = {}

    def register_plugin_instructions(self, plugin_name: str, instructions: str | None) -> None:
        """注册插件级指令（来自 PRISMCLAW.md）。

        当该插件的技能被激活时，指令会被前置到技能指令文本中，
        确保安全规则、输出规范等约束在 LLM 执行前生效。
        """
        if not plugin_name or not isinstance(plugin_name, str):
            return
        if instructions and isinstance(instructions, str):
            self._plugin_instructions_map[plugin_name] = instructions

    def get_plugin_instructions(self, plugin_name: str | None) -> str:
        """获取指定插件的注册指令。"""
        if not plugin_name:
            return ""
        return self._plugin_instructions_map.get(plugin_name, "")

    def resolve_skill(
        self, skill_name: str, *, workspace_path: Path, plugin_prefer: str | None = None
    ) -> Any | None:
        _ = workspace_path
        if self._capability_catalog is None:
            return None
        manifest = self._capability_catalog.resolve_skill_manifest(
            skill_name, plugin_prefer=plugin_prefer
        )
        if manifest is None:
            return None
        # frontmatter-only 扫描（项目级/插件级统一）下 body 为 None；
        # resolve_skill 的调用语义就是"即将使用该技能"，因此在这里统一补载 body，
        # 避免 _build_preload_messages / subagent preload 等消费者拿到空 body 静默跳过。
        return self._ensure_body_loaded(manifest)

    def list_skill_names(self, *, workspace_path: Path) -> list[str]:
        """Return the list of all currently available skill names."""
        _ = workspace_path
        if self._capability_catalog is None:
            return []
        entries = self._capability_catalog.list_skill_entries()
        return [e.name for e in entries]

    def _resolution_diagnostics(
        self,
    ) -> tuple[list[str], dict[str, tuple[str, ...]]]:
        """(全部技能名, 包名→成员技能名) 供 not_found 近邻候选。"""
        if self._capability_catalog is None:
            return [], {}
        entries = self._capability_catalog.list_skill_entries()
        skill_names = [e.name for e in entries]
        members: dict[str, set[str]] = {}
        for entry in entries:
            if entry.marketplace_name:
                members.setdefault(entry.marketplace_name, set()).add(entry.name)
        return skill_names, {name: tuple(sorted(m)) for name, m in members.items()}

    def _resolution_error_message(
        self, skill_name: str, resolution: SkillResolution | None
    ) -> str:
        """activate_skill 失败时的路由消息——错误自带下一步指引，绝不自反。"""
        if resolution is not None and resolution.kind == "ambiguous":
            return (
                f"'{skill_name}' 是插件包名，不是技能名。"
                f"该包包含 {len(resolution.member_names)} 个技能："
                f"{', '.join(resolution.member_names)}。"
                f"请用上述精确技能名重新调用 activate_skill。"
            )
        msg = f"Skill '{skill_name}' not found."
        if resolution is None or resolution.kind == "not_found":
            skill_names, mp_members = self._resolution_diagnostics()
            near = difflib.get_close_matches(
                skill_name, [*skill_names, *sorted(mp_members)], n=3, cutoff=0.6
            )
            cand_parts: list[str] = []
            for cand in near:
                if cand in mp_members:
                    cand_parts.append(
                        f"插件包 '{cand}'（技能：{', '.join(mp_members[cand])}）"
                    )
                else:
                    cand_parts.append(f"'{cand}'")
            if cand_parts:
                msg += f" 最接近的可用名称：{'；'.join(cand_parts)}。"
        msg += "用 skill_search 按主题检索，或直接使用通用工具（读写文件、搜索、编辑等）完成任务。"
        return msg

    def _ensure_body_loaded(self, manifest: SkillManifest) -> SkillManifest:
        """如果 body 未加载（None，frontmatter-only 模式），从文件重新读取并更新注册表中的 manifest。

        v5 渐进式披露：直接操作 DescriptorRegistry 中的 manifest 引用
        （不是传入的 manifest 副本），确保后续所有消费者都能读到完整 body。

        调用链唯一入口：``build_skill_execution`` 在最开始调用此方法，
        后续所有 body 消费者（_resolve_instructions、build_agent_manifest_from_skill 等）
        无需再次守卫。
        """
        if manifest.body is not None:
            return manifest

        parse_skill_file = self._skill_parser
        if parse_skill_file is None:
            logger.error("skill_parser not injected; cannot load body for %s", manifest.name)
            return manifest

        try:
            full = parse_skill_file(manifest.file_path)
        except Exception as exc:
            logger.error("failed to load skill body on demand: %s (%s)", manifest.name, exc)
            return manifest

        # 执行延迟 content pipeline
        ctx = self._get_pipeline_context(manifest)
        processed_body = self._apply_delayed_pipeline(full, ctx)
        if processed_body is not None:
            full = full.model_copy(update={"body": processed_body})

        # 更新注册表中的 manifest（关键：操作注册表引用，不是传入副本）
        if self._descriptor_registry is not None:
            descriptor = self._descriptor_registry.skills.get(manifest.name)
            if descriptor is not None and descriptor.manifest is not None:
                # 用 model_copy 更新，避免 validate_assignment 的逐字段验证开销
                descriptor.manifest = descriptor.manifest.model_copy(update={
                    "body": full.body,
                    "preload_skills": full.preload_skills,
                    "declared_invoke_sequence": full.declared_invoke_sequence,
                })
                if descriptor.manifest.agent_config is not None:
                    descriptor.manifest.agent_config = descriptor.manifest.agent_config.model_copy(
                        update={"preload_skills": list(full.preload_skills)}
                    )
                logger.debug(
                    "skill body loaded on demand: %s (%d chars)", manifest.name, len(full.body)
                )
                # Token 上限告警：技能 body 过大时会挤占 LLM 上下文
                from prismclaw.schema.shared.utils import estimate_tokens
                body_tokens = estimate_tokens(full.body or "")
                if body_tokens > 10000:
                    logger.warning(
                        "skill body exceeds token budget: %s (~%d tokens, recommended < 10000). "
                        "Large skill bodies consume significant LLM context.",
                        manifest.name, body_tokens,
                    )
                return descriptor.manifest

        # 注册表不可用时，直接返回完整 manifest（已含 body/pipeline 处理结果）
        logger.warning(
            "descriptor_registry not available, returning full manifest: %s", manifest.name
        )
        return full

    def _get_pipeline_context(self, manifest: SkillManifest) -> dict[str, Any]:
        """从 DescriptorRegistry 获取 manifest 对应的 pipeline_context。"""
        if self._descriptor_registry is None:
            return {}
        descriptor = self._descriptor_registry.skills.get(manifest.name)
        return descriptor.pipeline_context if descriptor else {}

    def _apply_delayed_pipeline(self, manifest: SkillManifest, ctx: dict[str, Any]) -> str | None:
        """从 pipeline_context 重新执行 content pipeline。

        v5 渐进式披露：pipeline_context 在 load_strategy.py 中保存，
        是纯 JSON 可序列化 dict，不含闭包。
        """
        if not ctx:
            return None

        try:
            from prismclaw.engine.plugins.loading.load_strategy import _apply_content_pipeline
            from prismclaw.engine.plugins.ingest.path_remap import PathRemapRule
        except ImportError:
            logger.warning("_apply_content_pipeline not importable, skipping delayed pipeline")
            return None

        # 从 pipeline_context 恢复参数
        skill_dir = Path(manifest.file_path).parent if manifest.file_path else None
        install_path = Path(ctx["install_path"]) if ctx.get("install_path") else None
        plugin_data_dir = Path(ctx["plugin_data_dir"]) if ctx.get("plugin_data_dir") else None
        marketplace_data_dir = (
            Path(ctx["marketplace_data_dir"]) if ctx.get("marketplace_data_dir") else None
        )
        # 反序列化 remap_rules
        remap_rules = [
            PathRemapRule(source_prefix=r["source_prefix"], target_prefix=r["target_prefix"])
            for r in ctx.get("remap_rules", [])
        ]
        resolved_user_config = ctx.get("resolved_user_config", {})
        sensitive_keys = frozenset(ctx.get("sensitive_user_config_keys", []))

        try:
            return _apply_content_pipeline(
                manifest.body,
                install_path=install_path or Path("."),
                plugin_data_dir=plugin_data_dir,
                marketplace_data_dir=marketplace_data_dir,
                skill_dir=skill_dir,
                effective_remap_rules=remap_rules,
                resolved_user_config=resolved_user_config,
                sensitive_user_config_keys=sensitive_keys,
                ensure_plugin_data_dir=lambda: None,
                ensure_marketplace_data_dir=lambda: None,
            )
        except Exception as exc:
            logger.warning("delayed content pipeline failed for %s: %s", manifest.name, exc)
            return None

    def _resolve_instructions(
        self,
        manifest: SkillManifest,
        invocation: SkillInvocation,
    ) -> str:
        """Resolve the instruction text injected into the model context.

        Filesystem/plugin skills carry their instructions in ``body``. MCP
        skills carry only discovery metadata in ``body``; the real content is
        fetched from the server via ``prompts/get`` at invocation time so
        prompt-template arguments are honored. A failed fetch raises — there
        is deliberately no fallback to the metadata body, which would
        silently inject a hollow skill.

        For filesystem/plugin skills, any ``references/*.md`` paths mentioned
        in ``body`` are auto-inlined as an appendix so the LLM does not need
        to call ``read`` (which would fail with 'path escapes workspace' —
        skill files live outside the user workspace).

        v5 渐进式披露：调用方（build_skill_execution）已确保 body 已加载，
        此方法无需再次守卫空 body。
        """
        if manifest.source != "mcp":
            return _inline_referenced_docs(manifest.body, manifest.file_path)
        if self._mcp_prompt_resolver is None:
            raise RuntimeError(
                f"MCP 技能 '{manifest.name}' 无法调用："
                "当前运行环境未接入 MCP prompt 解析器。"
            )
        arguments = (
            {key: str(value) for key, value in invocation.args.items()}
            if invocation.args
            else None
        )
        # Catalog names are namespaced ``{server}:{prompt}``; prompts/get
        # needs the raw prompt name.
        prompt_name = mcp_prompt_name(manifest.name, manifest.loaded_from)
        return self._mcp_prompt_resolver(
            manifest.loaded_from, prompt_name, arguments
        )

    def build_skill_execution(
        self,
        invocation: SkillInvocation,
        *,
        workspace_path: Path,
        parent_active_state: SkillInvocationState | None = None,
    ) -> SkillExecutionStrategy:
        # 方案 C:若上层有 active 的 skill(通常是工作流),把它的 plugin_name 传下去,
        # resolve 时优先选同 plugin 内重名的 command/skill,避免错命中其它 plugin 版本。
        plugin_prefer: str | None = None
        if parent_active_state is not None:
            plugin_prefer = getattr(parent_active_state, "plugin_name", None)
        resolution: SkillResolution | None = None
        if self._capability_catalog is not None:
            resolution = self._capability_catalog.resolve_skill_reference(
                invocation.skill_name, plugin_prefer=plugin_prefer
            )
        if resolution is None or resolution.kind != "resolved":
            raise LookupError(
                self._resolution_error_message(invocation.skill_name, resolution)
            )
        manifest = _coerce_skill_manifest(resolution.entry.manifest)
        # v5 渐进式披露：确保 body 已加载（frontmatter-only 模式下 body 为空）
        # 必须在 _resolve_instructions 和 build_agent_manifest_from_skill 之前，
        # 因为两者都依赖 manifest.body 已填充
        if manifest.body is None and manifest.source != "mcp" and manifest.file_path:
            manifest = self._ensure_body_loaded(manifest)
        allowed_tools = _coerce_allowed_tools(manifest.allowed_tools)
        description = manifest.description or ""
        instructions = self._resolve_instructions(manifest, invocation)
        # 注入插件级指令：前置 PRISMCLAW.md 定义的安全规则/输出规范
        # loaded_from 可能是文件路径(如 ".../medical-clinical/skills/...")
        # 或直接是 plugin 名(如 "medical-clinical")，需正确解析
        plugin_name = _extract_plugin_name(manifest)
        if plugin_name:
            plugin_instructions = self.get_plugin_instructions(plugin_name)
            if plugin_instructions:
                instructions = f"{plugin_instructions}\n\n---\n\n{instructions}"
        summary = f"Activated skill '{invocation.skill_name}'."
        if resolution.marketplace_name:
            # 别名 pass 命中（模型用了包名）：显式揭示实际匹配路径，
            # 避免"猜包名也能过"的隐性激励，同时让观测链路可追溯。
            summary += f" (matched via package '{resolution.marketplace_name}')"
            logger.info(
                "skill resolution matched via package: skill=%s package=%s",
                invocation.skill_name,
                resolution.marketplace_name,
            )
        if description:
            summary += f" {description}"
        skill_dir: str | None = None
        if manifest.source != "mcp" and manifest.file_path:
            skill_dir = str(Path(manifest.file_path).expanduser().resolve().parent)
        append_messages = _render_skill_messages(
            skill_name=invocation.skill_name,
            task_context=invocation.task_context,
            instructions=instructions,
            allowed_tools=allowed_tools,
            skill_dir=skill_dir,
        )
        subagent_manifest = None
        if manifest.execution_mode != "inline":
            agent_manifest = build_agent_manifest_from_skill(manifest)
            subagent_manifest = agent_manifest_to_skill_payload(agent_manifest)
            summary = (
                f"Prepared {manifest.execution_mode} execution plan for skill "
                f"'{invocation.skill_name}'."
            )
            if description:
                summary += f" {description}"
        # --- 编排遵从度:若调用方传入了当前 active 的父工作流 state,
        # 把本次被调用的子技能名追加到 actual_invoke_sequence ---
        if parent_active_state is not None and parent_active_state.is_workflow:
            try:
                parent_active_state.actual_invoke_sequence.append(invocation.skill_name)
            except Exception:  # pragma: no cover - 防御性容错,埋点失败不应阻塞业务
                pass
        declared_ref = getattr(manifest, "declared_invoke_sequence", ())
        declared_list = list(declared_ref) if isinstance(declared_ref, (list, tuple)) else []
        resolved_plugin_name = _resolve_plugin_name(manifest)
        invocation_state = SkillInvocationState(
            skill_name=invocation.skill_name,
            allowed_tools_override=allowed_tools,
            is_active=True,
            clear_after_terminal_result=True,
            # 方案 C:把本 skill 的 plugin_name 写入 state,供下一层子 skill resolve 时继承 plugin_prefer
            plugin_name=resolved_plugin_name if resolved_plugin_name else None,
            # 标准技能激活路径
            invocation_context="skill",
            # len(declared) > 0 表示这是编排类工作流技能
            is_workflow=bool(declared_list),
            # 保存声明序列副本,供 invoke 结束时 self-contained 比对
            declared_invoke_sequence=declared_list,
        )
        # ====================================================================
        # preload body 注入（上限读 subcaps.skill_preload，默认 7.5K）+ 执业档案加载 + plugin_name 传播
        # 语义分工再次明确:
        # ・manifest.preload_skills  → 注入 LLM 上下文用(值与 declared_invoke_sequence 同步)
        # ・declared_invoke_sequence → 给 compute_workflow_compliance 审计埋点用
        # ====================================================================
        try:
            from prismclaw.infra.settings import load_settings
            budget = int(load_settings().agent.context.budget.subcaps.skill_preload)
        except Exception:
            budget = 7_500
        preload_text = _build_preload_messages(
            self,
            list(manifest.preload_skills or ()),
            workspace_path=workspace_path,
            token_limit=budget,
        )
        resolved_plugin_name = _resolve_plugin_name(manifest)
        # 执业档案按 project_root 解析（与 PluginSettings 一致）；
        # workspace_path 拼不出插件配置目录，不能作为 hint。
        profile_text = _load_plugin_profile(resolved_plugin_name)
        # 合并 preload + profile 为一条额外 user 消息,追加进 append_messages
        extras_segments: list[str] = []
        if preload_text:
            extras_segments.append(preload_text)
        if profile_text:
            extras_segments.extend(
                [
                    "\n---\n",
                    f"## 插件运行配置：{resolved_plugin_name}",
                    profile_text,
                ]
            )
        if extras_segments:
            append_messages.append(
                {"role": "user", "content": "\n".join(s for s in extras_segments if s).strip()}
            )
        return SkillExecutionStrategy(
            skill_name=invocation.skill_name,
            execution_mode=manifest.execution_mode,
            allowed_tools=allowed_tools,
            # 方案 §0.1 #2: 保留 preload_skills(与 declared_invoke_sequence 同步)
            preload_skills=list(manifest.preload_skills or declared_list),
            subagent_manifest=subagent_manifest,
            append_messages=append_messages,
            summary=summary,
            invocation_state=invocation_state,
            plugin_name=resolved_plugin_name,
            plugin_profile_text=profile_text,
            preload_budget_chars=budget,
        )

    def execute_skill_execution(
        self,
        plan: SkillExecutionStrategy,
        *,
        clear_after_terminal_result: bool = True,
    ) -> SkillInvocationState | None:
        if plan.execution_mode != "inline":
            return None
        if plan.invocation_state is not None:
            plan.invocation_state.clear_after_terminal_result = clear_after_terminal_result
            return plan.invocation_state
        return SkillInvocationState(
            skill_name=plan.skill_name,
            allowed_tools_override=plan.allowed_tools,
            is_active=True,
            clear_after_terminal_result=clear_after_terminal_result,
            invocation_context="skill",
        )

    def invoke(
        self,
        invocation: SkillInvocation,
        *,
        workspace_path: Path,
        clear_after_terminal_result: bool = True,
        parent_active_state: SkillInvocationState | None = None,
    ) -> tuple[SkillExecutionStrategy, SkillInvocationState | None]:
        plan = self.build_skill_execution(
            invocation,
            workspace_path=workspace_path,
            parent_active_state=parent_active_state,
        )
        state = self.execute_skill_execution(plan, clear_after_terminal_result=clear_after_terminal_result)
        # --- 编排遵从度 emit 出口 ---
        # 对 workflow 类技能在 invoke 结束时做 declared vs actual 比对,
        # 结果 → ① state.compliance_result ② metrics 系统 (histogram+counter) ③ 审计日志。
        # 非 workflow 技能跳过,避免产生大量空噪音。
        if state is not None and state.is_workflow:
            try:
                result = compute_workflow_compliance(
                    state.declared_invoke_sequence,
                    state.actual_invoke_sequence,
                )
                object.__setattr__(state, "compliance_result", result)
            except Exception:  # pragma: no cover - 防御性容错
                result = None
            if result is not None:
                ratio = float(result.get("compliance_ratio", 0.0))
                missing = result.get("missing", [])
                extra = result.get("extra", [])
                order_match = bool(result.get("order_match", False))
                # 埋点结果接入 metrics 系统 (histogram 看分布 + counter 看累计偏差)
                # label 基数可控: 只有声明了子技能调用的工作流技能才会触发,非 workflow 技能跳过。
                metric_labels = {
                    "skill": state.skill_name,
                    "order_match": "true" if order_match else "false",
                }
                emit_metric(
                    "skill.workflow.compliance_ratio",
                    ratio,
                    kind="histogram",
                    labels=metric_labels,
                )
                emit_metric(
                    "skill.workflow.missing_count",
                    float(len(missing)),
                    kind="counter",
                    labels=metric_labels,
                )
                emit_metric(
                    "skill.workflow.extra_count",
                    float(len(extra)),
                    kind="counter",
                    labels=metric_labels,
                )
                logger.info(
                    "[workflow_compliance] skill=%s ratio=%.3f order_match=%s "
                    "declared=%s actual=%s missing=%s extra=%s",
                    state.skill_name,
                    ratio,
                    order_match,
                    result.get("declared_count", 0),
                    result.get("actual_count", 0),
                    missing,
                    extra,
                )
        return plan, state
