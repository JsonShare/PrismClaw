"""声明式插件加载核心实现。

从 load_strategy.py 拆分而来，包含声明式插件的命令加载、
输出样式、content pipeline、MCP 服务器、指令加载，
以及 manifest+capability 构建和声明式插件加载主函数。
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from prismclaw.engine.plugins.loading.agent_loader import PluginAgentLoader
from prismclaw.engine.plugins.registry.identity import PluginID
from prismclaw.engine.plugins.loading.load_result import LoadedPlugin, PluginError
from prismclaw.engine.plugins.loading._manifest_parse import (
    _coerce_string_list,
    _load_json_file,
    _resolve_canonical_manifest,
    _sensitive_user_config_keys,
    _resolve_user_config,
    _resolve_relative_paths,
    _validate_hook_command_paths,
    _merge_hook_config,
)
from prismclaw.engine.plugins.manifest import PluginManifest

logger = logging.getLogger(__name__)


def _load_declared_commands(
    path: Path, plugin_namespace: str, manifest: PluginManifest
) -> list[dict[str, Any]]:
    """加载命令定义。

    v4：合并两个来源（对标参考实现 manifest.commands + commands/ 目录）：
    1. manifest.commands 字典声明
    2. commands/ 目录下的 markdown 文件
    """
    commands: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    # 1. manifest 中的 commands 字典
    manifest_commands = manifest.commands
    if isinstance(manifest_commands, dict):
        for cmd_name, cmd_spec in manifest_commands.items():
            if not isinstance(cmd_name, str) or not cmd_name:
                continue
            namespaced_name = (
                cmd_name
                if cmd_name.startswith("/") or ":" in cmd_name
                else f"/{plugin_namespace}:{cmd_name}"
            )
            if namespaced_name in seen_names:
                continue
            seen_names.add(namespaced_name)
            commands.append(
                {
                    "type": "plugin_command",
                    "name": namespaced_name,
                    "description": str(cmd_spec.description or ""),
                    "body": str(cmd_spec.content or ""),
                    "source": f"plugin:{plugin_namespace}",
                    "path": str(path),
                    "argument_hint": cmd_spec.argument_hint,
                    "allowed_tools": cmd_spec.allowed_tools,
                    "model": cmd_spec.model,
                    "invocation_policy": "auto",
                }
            )

    # 2. commands/ 目录下的 markdown 文件
    commands_dir = path / "commands"
    if not commands_dir.is_dir():
        return commands
    import yaml
    from prismclaw.schema.shared import _extract_frontmatter

    for entry in sorted(commands_dir.iterdir(), key=lambda p: p.name):
        if not entry.is_file() or entry.suffix != ".md":
            continue
        try:
            content = entry.read_text(encoding="utf-8")
            frontmatter_str, body_text = _extract_frontmatter(content)
            data = yaml.safe_load(frontmatter_str) if frontmatter_str.strip() else {}
            if not isinstance(data, dict):
                data = {}
            name = data.get("name") or entry.stem
            if name in seen_names:
                continue
            seen_names.add(name)
            # 子缺陷 1+3 修复：补齐 argument_hint/allowed_tools/model/invocation_policy，
            # 与 manifest commands 分支保持一致
            invocation_policy = data.get("invocation_policy")
            if invocation_policy is None:
                ui = data.get("user-invocable", True)
                dmi = data.get("disable-model-invocation", False)
                if not ui:
                    invocation_policy = "user_only"
                elif dmi:
                    invocation_policy = "disabled"
                else:
                    invocation_policy = "auto"
            commands.append(
                {
                    "type": "plugin_command",
                    "name": name,
                    "description": data.get("description", ""),
                    "body": body_text,
                    "source": f"plugin:{plugin_namespace}",
                    "path": str(entry),
                    "argument_hint": data.get("argument_hint"),
                    "allowed_tools": _coerce_string_list(data.get("allowed_tools")),
                    "model": data.get("model"),
                    "invocation_policy": invocation_policy,
                }
            )
        except Exception as exc:
            logger.debug("Failed to load command %s: %s", entry, exc)
    return commands


def _load_output_styles(path: Path, plugin_namespace: str) -> dict[str, str]:
    """加载 output-styles/*.md 为 {namespace.style_name: body} 映射。"""
    styles: dict[str, str] = {}
    styles_dir = path / "output-styles"
    if not styles_dir.is_dir():
        return styles
    for entry in sorted(styles_dir.iterdir(), key=lambda p: p.name):
        if not entry.is_file() or entry.suffix != ".md":
            continue
        try:
            styles[f"{plugin_namespace}.{entry.stem}"] = entry.read_text(encoding="utf-8")
        except OSError as exc:
            logger.debug("Failed to load output style %s: %s", entry, exc)
    return styles


def _apply_content_pipeline(
    body: str,
    *,
    install_path: Path,
    plugin_data_dir: Path,
    marketplace_data_dir: Path | None,
    skill_dir: Path | None,
    effective_remap_rules: list[Any],
    resolved_user_config: dict[str, Any],
    sensitive_user_config_keys: frozenset[str],
    ensure_plugin_data_dir: Any,
    ensure_marketplace_data_dir: Any,
) -> str | None:
    """公共 body 替换管道：path_remap → variable substitution → user_config substitution。

    返回处理后的 body，或 None 表示无需处理（既无 remap 也无变量占位符）。
    """
    from prismclaw.engine.plugins.ingest.path_remap import (
        apply_path_remap,
    )
    from prismclaw.engine.plugins.manifest.substitution import (
        PRISMCLAW_MARKETPLACE_DATA,
        PRISMCLAW_PLUGIN_DATA,
        build_plugin_variables,
        substitute_user_config_in_content,
        substitute_variables,
    )

    data_dir_placeholder = "${" + PRISMCLAW_PLUGIN_DATA + "}"
    marketplace_data_placeholder = "${" + PRISMCLAW_MARKETPLACE_DATA + "}"

    remapped = apply_path_remap(body, effective_remap_rules)
    if remapped == body and "${" not in body:
        return None
    if data_dir_placeholder in remapped:
        ensure_plugin_data_dir()
    if marketplace_data_placeholder in remapped:
        ensure_marketplace_data_dir()
    remapped = substitute_variables(
        remapped,
        build_plugin_variables(
            plugin_root=install_path,
            plugin_data_dir=plugin_data_dir,
            marketplace_data_dir=marketplace_data_dir,
            skill_dir=skill_dir,
        ),
    )
    return substitute_user_config_in_content(
        remapped, resolved_user_config, sensitive_user_config_keys
    )


def _load_mcp_servers(
    path: Path,
    manifest: PluginManifest,
    *,
    plugin_data_dir: Path | None = None,
    marketplace_data_dir: Path | None = None,
) -> dict[str, Any]:
    """加载 plugin.mcp.json 或 manifest.mcp_servers 配置。

    SDD context-governance W7-5: mcp.json 的 ``command``/``args``/``env``/
    ``url`` 与技能正文一样支持 ``${PRISMCLAW_PLUGIN_ROOT}`` 等变量。此前
    加载路径不做替换，stdrio server 收到字面量 ``${PRISMCLAW_PLUGIN_ROOT}/…``
    路径直接 ENOENT，表现为每次启动 "Connection closed" 失败。
    """
    # 1. manifest 内联声明
    inline = manifest.mcp_servers
    if isinstance(inline, dict) and inline:
        servers = dict(inline)
        return _substitute_mcp_server_configs(servers, path, plugin_data_dir, marketplace_data_dir)

    # 2. 文件系统
    mcp_path = None
    for candidate in [path / ".plugin" / "mcp.json", path / "mcp.json", path / ".mcp.json"]:
        if candidate.exists():
            mcp_path = candidate
            break
    if mcp_path is None:
        return {}
    try:
        data = _load_json_file(mcp_path)
        if isinstance(data, dict):
            servers = data.get("mcp_servers")
            if servers is None:
                servers = data.get("mcpServers")
            if isinstance(servers, dict):
                servers = dict(servers)
                return _substitute_mcp_server_configs(
                    servers, path, plugin_data_dir, marketplace_data_dir
                )
            return data
    except Exception as exc:
        logger.debug("Failed to load mcp config %s: %s", mcp_path, exc)
    return {}


def _substitute_mcp_server_configs(
    servers: dict[str, Any],
    plugin_root: Path,
    plugin_data_dir: Path | None,
    marketplace_data_dir: Path | None,
) -> dict[str, Any]:
    """对每个 server 配置的字符串字段做 ``${VAR}`` 变量替换。

    只处理 command / args / env / url / headers 中的字符串值；未知变量按
    :mod:`substitution` 的约定保留字面量（例如需要用户回填的占位符）。
    """
    from prismclaw.engine.plugins.manifest.substitution import (
        build_plugin_variables,
        substitute_variables,
    )

    variables = build_plugin_variables(
        plugin_root=plugin_root,
        plugin_data_dir=plugin_data_dir,
        marketplace_data_dir=marketplace_data_dir,
    )
    string_fields = ("command", "url")
    substituted: dict[str, Any] = {}
    for name, config in servers.items():
        if not isinstance(config, dict):
            substituted[name] = config
            continue
        cfg = dict(config)
        for field in string_fields:
            value = cfg.get(field)
            if isinstance(value, str):
                cfg[field] = substitute_variables(value, variables)
        for field in ("args",):
            values = cfg.get(field)
            if isinstance(values, list):
                cfg[field] = [
                    substitute_variables(v, variables) if isinstance(v, str) else v
                    for v in values
                ]
        for field in ("env", "headers"):
            mapping = cfg.get(field)
            if isinstance(mapping, dict):
                cfg[field] = {
                    k: substitute_variables(v, variables) if isinstance(v, str) else v
                    for k, v in mapping.items()
                }
        substituted[name] = cfg
    return substituted


def _load_plugin_instructions(install_path: Path) -> str | None:
    """加载 PRISMCLAW.md 作为插件指令。

    PRISMCLAW.md 包含安全规则、输出规范、方法论、护栏等关键行为定义。
    加载逻辑：
    1. 优先加载子插件级 PRISMCLAW.md（最高优先级，可覆盖 marketplace 级）
    2. 向上查找 marketplace 级 PRISMCLAW.md 作为继承指令（低优先级）
    3. 合并两级指令，明确标注层级关系
    4. 对内容调用 substitute_variables 解析 ${PRISMCLAW_PLUGIN_ROOT} 等变量

    HTML comment 剥离（2026-10-29 修复）：
    插件模板里用 ``<!-- ... -->`` comment 块写了"停止检查"等 prose 指令——这些
    是写给人类维护者的，不是写给模型的。LLM 对 prose 指令特别敏感，会无视
    comment 格式把它当有效指令执行，导致首次激活就拦用户。加载时剥离 comment
    块，让 PRISMCLAW.md 回归纯指令/规范文本。
    """
    from prismclaw.engine.plugins.manifest.substitution import (
        build_plugin_variables,
        substitute_variables,
    )

    plugin_name = install_path.name
    variables = build_plugin_variables(plugin_root=install_path)
    parts: list[str] = []
    sub_plugin_readme = install_path / "PRISMCLAW.md"
    if sub_plugin_readme.is_file():
        try:
            content = sub_plugin_readme.read_text(encoding="utf-8")
            if content.strip():
                content = _strip_html_comments(content)
                substituted = substitute_variables(content, variables)
                parts.append(
                    f"## 子插件级指令（{plugin_name}，优先级：高）\n\n"
                    f"以下指令为该子插件专属定义，优先级高于 marketplace 级指令。\n\n"
                    f"{substituted.strip()}"
                )
        except OSError as exc:
            logger.debug("Failed to read PRISMCLAW.md at %s: %s", sub_plugin_readme, exc)

    parent = install_path.parent
    marketplace_readme = parent / "PRISMCLAW.md"
    if marketplace_readme.is_file():
        try:
            content = marketplace_readme.read_text(encoding="utf-8")
            if content.strip():
                content = _strip_html_comments(content)
                substituted = substitute_variables(content, variables)
                mp_name = parent.name
                parts.append(
                    f"## Marketplace 级指令（{mp_name}，优先级：低）\n\n"
                    f"以下指令为 marketplace 通用约束，子插件级指令可覆盖之。\n\n"
                    f"{substituted.strip()}"
                )
        except OSError as exc:
            logger.debug("Failed to read marketplace PRISMCLAW.md at %s: %s", marketplace_readme, exc)

    if parts:
        return "\n\n---\n\n".join(parts)
    return None


def _strip_html_comments(text: str) -> str:
    """剥离 HTML comment 块 ``<!-- ... -->`` 并折叠空行。

    非贪婪 ``re.DOTALL``，跨行 comment 块一次匹配。comment 之间的空行
    用折叠正则 ``\n{3,} → \n\n`` 压平，避免模板头注释剥离后留下大块空白。
    """
    stripped = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    return re.sub(r"\n{3,}", "\n\n", stripped)


def _build_manifest_and_capability(
    plugin_id: PluginID,
    install_path: Path,
    plugin_options: dict[str, Any],
    *,
    agent_loader: PluginAgentLoader,
    data_base_dir: Path | None = None,
    manifest_override: PluginManifest | None = None,
    remap_rules: list[Any] | None = None,
    marketplace_data_dir: Path | None = None,
    skill_scanner: Any | None = None,
) -> tuple[
    PluginManifest, Any, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, str], str | None
]:
    """构建 manifest + capability_graph + 配套字段。

    Step 1-1 三轨统一后，不再返回中间 manifest_model wrapper；
    返回 (manifest, capability_graph, resolved_user_config,
           hooks_config, mcp_servers, output_styles, plugin_instructions)。

    全程操作 pydantic ``PluginManifest`` model 对象，不经过 raw dict。

    ``manifest_override``：方言无关化入口。canonical 策略传 None，由
    ``_resolve_canonical_manifest`` 从 ``install_path`` 读 ``plugin.json``；
    方言策略（如 ``.trae-plugin``）先把自身清单转换为 canonical 形态再传入，
    本函数之后的 skills/agents/commands/变量替换等处理对两者完全一致。

    ``remap_rules``：插件自身或所属 marketplace 声明的路径重映射规则，
    在变量替换前作用于正文。核心不内置任何插件特定路径——规则完全来自
    插件声明，无声明则为空（no-op）。
    ``marketplace_data_dir``：marketplace 级共享数据目录，承载
    ``${PRISMCLAW_MARKETPLACE_DATA}``；独立插件为 None（占位符保留字面）。
    """
    from prismclaw.engine.execution.capabilities.plugin_graph_builder import (
        build_plugin_capability_graph,
    )

    if manifest_override is not None:
        manifest = manifest_override
    else:
        manifest = _resolve_canonical_manifest(install_path)

    plugin_namespace = manifest.name or plugin_id.name

    # Step 1-11: dependencies 声明格式验证 (依赖是否已加载由 PluginLoader 批量检查)
    if manifest.dependencies:
        for dep in manifest.dependencies:
            if not isinstance(dep, str) or not dep.strip():
                raise PluginError(
                    f"插件 {plugin_namespace!r} 的依赖声明无效：{dep!r} "
                    "（应为非空字符串，如 'name' 或 'name@marketplace'）",
                    plugin_name=plugin_namespace,
                )
        logger.info(
            "plugin %r declares dependencies: %s (resolution deferred to PluginLoader)",
            plugin_namespace,
            manifest.dependencies,
        )

    # 路径重映射两层：引擎默认 + 插件声明。默认只覆盖最常见的裸写路径
    # ~/.prismclaw/ → ${PRISMCLAW_USER_HOME}/；插件声明可以加更具体的
    # from 前缀覆盖或扩展它（插件声明顺序决定优先级，更具体的 from 先跑）。
    from prismclaw.engine.plugins.ingest.path_remap import (
        DEFAULT_REMAP_RULES,
        parse_remap_rules,
    )

    # porting 是 PluginManifest 的显式字段，直接属性访问。
    own_porting = manifest.porting
    own_remap_cfg = own_porting.get("remap") if isinstance(own_porting, dict) else None
    effective_remap_rules = list(DEFAULT_REMAP_RULES) + parse_remap_rules(own_remap_cfg) + list(remap_rules or [])

    resolved_user_config = _resolve_user_config(manifest, plugin_options)
    sensitive_user_config_keys = _sensitive_user_config_keys(manifest)

    from prismclaw.engine.plugins.loading.data_dir import get_plugin_data_dir

    plugin_data_dir = get_plugin_data_dir(data_base_dir, plugin_namespace)

    # Hooks
    hooks_config: dict[str, Any] = {}
    hook_paths = [
        install_path / "hooks" / "hooks.json",
        *_resolve_relative_paths(install_path, manifest.hooks),
    ]
    # Marketplace-level hooks: parent directory's hooks.json applies to all sub-plugins
    marketplace_hooks = install_path.parent / "hooks" / "hooks.json"
    if marketplace_hooks.is_file():
        hook_paths.insert(0, marketplace_hooks)
    for hook_path in hook_paths:
        if not hook_path.exists() or not hook_path.is_file():
            continue
        try:
            hooks_config = _merge_hook_config(hooks_config, _load_json_file(hook_path))
        except Exception as exc:
            raise ValueError(f"Invalid hooks configuration at {hook_path}: {exc}") from exc

    # 加载期校验 hooks 中引用的安全脚本路径是否存在
    _validate_hook_command_paths(hooks_config, install_path)

    from prismclaw.engine.plugins.hooks.factory import normalize_hooks_config

    hooks_config = normalize_hooks_config(hooks_config)

    # Skills
    skill_paths = [
        install_path / "skills",
        *_resolve_relative_paths(install_path, manifest.skills),
    ]
    skills: list[Any] = []
    seen_skill_dirs: set[Path] = set()
    for skills_dir in skill_paths:
        if not skills_dir.exists() or not skills_dir.is_dir():
            continue
        skills_dir = skills_dir.resolve()
        if skills_dir in seen_skill_dirs:
            continue
        seen_skill_dirs.add(skills_dir)
        if skill_scanner is not None:
            skills.extend(
                skill_scanner(
                    skills_dir,
                    max_file_bytes=256_000,
                    max_candidates=500,
                    max_skills_loaded=100,
                )
            )

    # v5 渐进式披露：技能加载时只解析 frontmatter（body=""），
    # content pipeline 参数暂存到 pipeline_context，body 按需加载时
    # 由 SkillRuntime._apply_delayed_pipeline() 重新执行。
    # commands 仍需要 content pipeline + ensure 闭包。
    data_dir_created = False
    marketplace_data_dir_created = False

    def _ensure_plugin_data_dir() -> None:
        nonlocal data_dir_created
        if not data_dir_created:
            plugin_data_dir.mkdir(parents=True, exist_ok=True)
            data_dir_created = True

    def _ensure_marketplace_data_dir() -> None:
        nonlocal marketplace_data_dir_created
        if marketplace_data_dir is not None and not marketplace_data_dir_created:
            marketplace_data_dir.mkdir(parents=True, exist_ok=True)
            marketplace_data_dir_created = True

    # v5 渐进式披露：不执行 content pipeline，参数存入 pipeline_context
    for skill in skills:
        skill.pipeline_context = {
            "install_path": str(install_path),
            "plugin_data_dir": str(plugin_data_dir),
            "marketplace_data_dir": str(marketplace_data_dir) if marketplace_data_dir else None,
            "remap_rules": [
                {"source_prefix": r.source_prefix, "target_prefix": r.target_prefix}
                for r in effective_remap_rules
            ],
            "resolved_user_config": dict(resolved_user_config),
            "sensitive_user_config_keys": list(sensitive_user_config_keys),
        }

    # Agents
    agents: list[Any] = []
    agent_paths = [
        install_path / "agents",
        *_resolve_relative_paths(install_path, manifest.agents),
    ]
    seen_agent_paths: set[Path] = set()
    for agents_path in agent_paths:
        if not agents_path.exists():
            continue
        agents_path = agents_path.resolve()
        if agents_path in seen_agent_paths:
            continue
        seen_agent_paths.add(agents_path)
        if agents_path.is_dir():
            agents.extend(
                list(
                    agent_loader.scan_agents_dir(
                        workspace_path=install_path,
                        agents_path=agents_path,
                        source=f"plugin:{plugin_namespace}",
                    )
                )
            )
        elif agents_path.is_file() and agents_path.suffix == ".md":
            agents.append(
                agent_loader.load_agent_file(
                    agent_path=agents_path,
                    source=f"plugin:{plugin_namespace}",
                )
            )

    commands = _load_declared_commands(install_path, plugin_namespace, manifest)
    for command in commands:
        original_body = command.get("body")
        if not isinstance(original_body, str) or not original_body:
            continue
        processed = _apply_content_pipeline(
            original_body,
            install_path=install_path,
            plugin_data_dir=plugin_data_dir,
            marketplace_data_dir=marketplace_data_dir,
            skill_dir=None,
            effective_remap_rules=effective_remap_rules,
            resolved_user_config=resolved_user_config,
            sensitive_user_config_keys=sensitive_user_config_keys,
            ensure_plugin_data_dir=_ensure_plugin_data_dir,
            ensure_marketplace_data_dir=_ensure_marketplace_data_dir,
        )
        if processed is not None:
            command["body"] = processed
    output_styles = _load_output_styles(install_path, plugin_namespace)
    mcp_servers = _load_mcp_servers(
        install_path,
        manifest,
        plugin_data_dir=plugin_data_dir,
        marketplace_data_dir=marketplace_data_dir,
    )

    plugin_instructions = _load_plugin_instructions(install_path)

    for command in commands:
        command["output_styles"] = sorted(output_styles.keys())
        command["mcp_servers"] = sorted(f"{plugin_namespace}.{name}" for name in mcp_servers.keys())
        command["plugin_options"] = dict(resolved_user_config)

    capability_graph = build_plugin_capability_graph(
        plugin_name=plugin_namespace,
        namespace=plugin_id.source,
        root_path=install_path,
        manifest=manifest,
        hooks_config=hooks_config,
        commands=commands,
        output_styles=output_styles,
        mcp_servers=mcp_servers,
        skills=skills,
        agents=agents,
    )

    return (
        manifest,
        capability_graph,
        resolved_user_config,
        hooks_config,
        mcp_servers,
        output_styles,
        plugin_instructions,
    )


async def _load_declarative_plugin(
    plugin_id: PluginID,
    install_path: Path,
    *,
    enabled: bool,
    plugin_options: dict[str, Any],
    agent_loader: PluginAgentLoader,
    data_base_dir: Path | None,
    manifest_override: PluginManifest | None = None,
    porting_context: dict[str, Any] | None = None,
    dialect: str,
    skill_scanner: Any | None = None,
) -> tuple[LoadedPlugin | None, list[PluginError]]:
    """声明式插件加载核心（canonical 与方言策略共享）。

    把 ``_build_manifest_and_capability`` 的同步文件 I/O offload 到线程（让
    ``_load_group`` 的插件级 gather 获得真实并发、不阻塞事件循环），再用其
    结构化产物组装 ``LoadedPlugin``。canonical 策略传 ``manifest_override=None``
    （由 ``_resolve_canonical_manifest`` 读盘）；方言策略先把自身清单转换为
    canonical 形态再经 ``manifest_override`` 注入。``dialect`` 仅用于日志。

    ``porting_context``：marketplace 级 porting 上下文（``remap_rules`` +
    ``marketplace_name``），由发现引擎从市场级 plugin.json 通用解析。据此派生
    marketplace 共享数据目录（``${PRISMCLAW_MARKETPLACE_DATA}``），并把继承的 remap
    规则下传；插件自身 manifest 的 ``porting.remap`` 在 ``_build_manifest_and_capability``
    内合并（自身优先）。核心不内置任何插件特定路径。
    """
    from prismclaw.engine.plugins.loading.data_dir import (
        get_plugin_data_dir,
        resolve_data_base_dir,
    )

    inherited_remap_rules: list[Any] = []
    marketplace_data_dir: Path | None = None
    marketplace_name: str | None = None
    marketplace_description: str | None = None
    if isinstance(porting_context, dict):
        rules = porting_context.get("remap_rules")
        if isinstance(rules, list):
            inherited_remap_rules = list(rules)
        marketplace_name = porting_context.get("marketplace_name")
        marketplace_description = porting_context.get("marketplace_description")
        if isinstance(marketplace_name, str) and marketplace_name:
            # 共享数据目录以 marketplace 名为键，跨子插件一致（如 legal 的
            # 跨领域 matter 与 company-profile）。复用 data_dir 的 sanitize。
            marketplace_data_dir = get_plugin_data_dir(
                resolve_data_base_dir(data_base_dir), marketplace_name
            )

    errors: list[PluginError] = []
    try:
        (
            manifest,
            capability_graph,
            resolved_user_config,
            hooks_config,
            mcp_servers,
            output_styles,
            plugin_instructions,
        ) = await asyncio.to_thread(
            _build_manifest_and_capability,
            plugin_id,
            install_path,
            plugin_options,
            agent_loader=agent_loader,
            data_base_dir=data_base_dir,
            manifest_override=manifest_override,
            remap_rules=inherited_remap_rules,
            marketplace_data_dir=marketplace_data_dir,
            skill_scanner=skill_scanner,
        )
        version = manifest.version
        # 加载期把 hooks 配置转换为可执行 handler。
        from prismclaw.engine.plugins.hooks.factory import build_hook_handlers

        hook_namespace = manifest.name or plugin_id.name
        hook_data_dir = get_plugin_data_dir(data_base_dir, hook_namespace)
        hook_handlers = build_hook_handlers(
            hooks_config,
            plugin_root=str(install_path),
            plugin_data_dir=str(hook_data_dir),
            user_config=resolved_user_config,
        )
        loaded = LoadedPlugin(
            name=plugin_id.name,
            source=plugin_id.source,
            path=str(install_path),
            enabled=enabled,
            version=version,
            manifest=manifest,
            capability_graph=capability_graph,
            runtime_capabilities=(
                capability_graph.runtime_capabilities if capability_graph is not None else None
            ),
            resolved_user_config=resolved_user_config,
            hooks_config=hooks_config,
            hook_handlers=hook_handlers,
            mcp_servers=mcp_servers,
            output_styles=output_styles,
            root_path=install_path,
            install_path=str(install_path),
            plugin_instructions=plugin_instructions,
            # v5 渐进式披露：marketplace_name 从 porting_context 传递
            marketplace_name=marketplace_name if isinstance(marketplace_name, str) else None,
            # v5 渐进式披露：marketplace_description 从市场级 plugin.json 传递
            marketplace_description=marketplace_description if isinstance(marketplace_description, str) else None,
        )
        return loaded, errors
    except Exception as exc:
        logger.warning("Failed to load %s plugin %s: %s", dialect, plugin_id, exc)
        errors.append(
            PluginError(
                type="manifest_parse_error",
                source=plugin_id.source,
                plugin=plugin_id.name,
                message=str(exc),
            )
        )
        return None, errors
