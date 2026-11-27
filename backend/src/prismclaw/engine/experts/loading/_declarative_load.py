"""声明式专家包加载核心实现。

从 load_strategy.py 拆分而来，包含声明式专家包的命令加载、
输出样式、content pipeline、MCP 服务器、指令加载，
以及 manifest+capability 构建和声明式专家包加载主函数。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from prismclaw.engine.experts import ExpertError, ExpertErrorCode
from prismclaw.engine.experts.loading._manifest_parse import (
    _coerce_string_list,
    _resolve_canonical_manifest,
    _resolve_relative_paths,
)
from prismclaw.engine.experts.loading.agent_loader import ExpertAgentLoader
from prismclaw.engine.experts.loading.load_result import ExpertPackage, PackageError
from prismclaw.engine.experts.manifest import ExpertManifest
from prismclaw.engine.experts.registry.identity import EXPERT_SOURCE, ExpertID
from prismclaw.schema.agent.capability import ExpertCommandCapability
from prismclaw.schema.agent.domains import resolve_invocation_policy
from prismclaw.schema.services.skills import SkillManifest

logger = logging.getLogger(__name__)

# 技能目录扫描器：接收目录与上限参数，返回仅解析 frontmatter 的技能清单。
SkillScanner = Callable[..., list[SkillManifest]]


def _reject_marketplace_data_placeholder(install_path: Path) -> None:
    placeholder = "${PRISMCLAW_MARKETPLACE_DATA}"
    for path in install_path.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".json", ".md", ".yaml", ".yml"}:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if placeholder in content:
            raise ExpertError(
                ExpertErrorCode.MANIFEST_INVALID,
                f"Unsupported {placeholder} in {path}",
                {"expert_path": str(install_path), "file_path": str(path)},
            )


def _load_declared_commands(
    path: Path, expert_namespace: str, manifest: ExpertManifest
) -> list[dict[str, Any]]:
    """加载命令定义。

    v4：合并两个来源（manifest.commands + commands/ 目录）：
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
                else f"/{expert_namespace}:{cmd_name}"
            )
            if namespaced_name in seen_names:
                continue
            seen_names.add(namespaced_name)
            commands.append(
                {
                    "type": "expert_command",
                    "name": namespaced_name,
                    "description": str(cmd_spec.description or ""),
                    "body": str(cmd_spec.content or ""),
                    "source": f"expert:{expert_namespace}",
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
            invocation_policy = resolve_invocation_policy(data).value
            commands.append(
                {
                    "type": "expert_command",
                    "name": name,
                    "description": data.get("description", ""),
                    "body": body_text,
                    "source": f"expert:{expert_namespace}",
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


def _normalize_commands(commands: list[Any]) -> list[ExpertCommandCapability]:
    """把装配阶段原始 command dicts 归一为 ExpertCommandCapability。"""
    result: list[ExpertCommandCapability] = []
    for command in commands:
        if not isinstance(command, dict):
            continue
        result.append(
            ExpertCommandCapability(
                name=str(command.get("name", "")),
                description=str(command.get("description", "")),
                body=str(command.get("body", "")),
                metadata=dict(command),
            )
        )
    return result


def _load_output_styles(path: Path, expert_namespace: str) -> dict[str, str]:
    """加载 output-styles/*.md 为 {namespace.style_name: body} 映射。"""
    styles: dict[str, str] = {}
    styles_dir = path / "output-styles"
    if not styles_dir.is_dir():
        return styles
    for entry in sorted(styles_dir.iterdir(), key=lambda p: p.name):
        if not entry.is_file() or entry.suffix != ".md":
            continue
        try:
            styles[f"{expert_namespace}.{entry.stem}"] = entry.read_text(encoding="utf-8")
        except OSError as exc:
            logger.debug("Failed to load output style %s: %s", entry, exc)
    return styles


def _apply_content_pipeline(
    body: str,
    *,
    install_path: Path,
    expert_data_dir: Path,
    skill_dir: Path | None,
    effective_remap_rules: list[Any],
    ensure_expert_data_dir: Callable[[], None],
) -> str | None:
    """公共 body 替换管道：path_remap → 变量替换。

    返回处理后的 body，或 None 表示无需处理（既无 remap 也无变量占位符）。
    """
    from prismclaw.engine.experts.ingest.path_remap import (
        apply_path_remap,
    )
    from prismclaw.engine.experts.manifest.substitution import (
        PRISMCLAW_PLUGIN_DATA,
        build_expert_variables,
        substitute_variables,
    )

    data_dir_placeholder = "${" + PRISMCLAW_PLUGIN_DATA + "}"

    remapped = apply_path_remap(body, effective_remap_rules)
    if remapped == body and "${" not in body:
        return None
    if data_dir_placeholder in remapped:
        ensure_expert_data_dir()
    return substitute_variables(
        remapped,
        build_expert_variables(
            expert_root=install_path,
            expert_data_dir=expert_data_dir,
            skill_dir=skill_dir,
        ),
    )


def _build_manifest_and_capability(
    expert_id: ExpertID,
    install_path: Path,
    *,
    agent_loader: ExpertAgentLoader,
    data_base_dir: Path | None = None,
    skill_scanner: SkillScanner | None = None,
) -> tuple[
    ExpertManifest,
    list[SkillManifest],
    list[Any],
    list[ExpertCommandCapability],
    dict[str, str],
]:
    """构建 manifest + 结构化能力字段 + 配套字段。

    返回 (manifest, skills, agents, commands, output_styles)。

    全程操作 pydantic ``ExpertManifest`` model 对象，不经过 raw dict。
    路径重映射仅使用当前包 ``plugin.json.porting.remap`` 声明。
    """
    manifest = _resolve_canonical_manifest(install_path, source=expert_id.source)

    expert_namespace = manifest.name or expert_id.name

    # Step 1-11: dependencies 声明格式验证 (依赖是否已加载由 ExpertLoader 批量检查)
    if manifest.dependencies:
        for dep in manifest.dependencies:
            if not isinstance(dep, str) or not dep.strip():
                raise ValueError(
                    f"专家包 {expert_namespace!r} 的依赖声明无效：{dep!r} "
                    "（应为非空字符串，如 'name' 或 'name@marketplace'）"
                )
        logger.info(
            "expert %r declares dependencies: %s (resolution deferred to ExpertLoader)",
            expert_namespace,
            manifest.dependencies,
        )

    # 路径重映射两层：引擎默认 + 专家包声明。默认只覆盖最常见的裸写路径
    # ~/.prismclaw/ → ${PRISMCLAW_USER_HOME}/；专家包声明可以加更具体的
    # from 前缀覆盖或扩展它（专家包声明顺序决定优先级，更具体的 from 先跑）。
    from prismclaw.engine.experts.ingest.path_remap import (
        DEFAULT_REMAP_RULES,
        parse_remap_rules,
    )

    # porting 是 ExpertManifest 的显式字段，直接属性访问。
    own_porting = manifest.porting
    own_remap_cfg = own_porting.get("remap") if isinstance(own_porting, dict) else None
    effective_remap_rules = list(DEFAULT_REMAP_RULES) + parse_remap_rules(own_remap_cfg)

    from prismclaw.engine.experts.loading.data_dir import get_expert_data_dir

    expert_data_dir = get_expert_data_dir(data_base_dir, expert_namespace)

    # Skills
    skill_paths = [
        install_path / "skills",
        *_resolve_relative_paths(install_path, manifest.skills),
    ]
    skills: list[SkillManifest] = []
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

    def _ensure_expert_data_dir() -> None:
        nonlocal data_dir_created
        if not data_dir_created:
            expert_data_dir.mkdir(parents=True, exist_ok=True)
            data_dir_created = True

    # v5 渐进式披露：不执行 content pipeline，参数存入 pipeline_context
    for skill in skills:
        skill.pipeline_context = {
            "install_path": str(install_path),
            "expert_data_dir": str(expert_data_dir),
            "remap_rules": [
                {"source_prefix": r.source_prefix, "target_prefix": r.target_prefix}
                for r in effective_remap_rules
            ],
        }

    # Agents
    agents: list[Any] = []
    agent_paths = [install_path / "agents"]
    if expert_id.source != EXPERT_SOURCE:
        agent_paths.extend(_resolve_relative_paths(install_path, manifest.agents))
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
                        source=f"expert:{expert_namespace}",
                    )
                )
            )
        elif agents_path.is_file() and agents_path.suffix == ".md":
            agents.append(
                agent_loader.load_agent_file(
                    agent_path=agents_path,
                    source=f"expert:{expert_namespace}",
                )
            )

    commands = _load_declared_commands(install_path, expert_namespace, manifest)
    for command in commands:
        original_body = command.get("body")
        if not isinstance(original_body, str) or not original_body:
            continue
        processed = _apply_content_pipeline(
            original_body,
            install_path=install_path,
            expert_data_dir=expert_data_dir,
            skill_dir=None,
            effective_remap_rules=effective_remap_rules,
            ensure_expert_data_dir=_ensure_expert_data_dir,
        )
        if processed is not None:
            command["body"] = processed
    output_styles = _load_output_styles(install_path, expert_namespace)

    for command in commands:
        command["output_styles"] = sorted(output_styles.keys())

    return (
        manifest,
        skills,
        agents,
        _normalize_commands(commands),
        output_styles,
    )


async def _load_declarative_expert(
    expert_id: ExpertID,
    install_path: Path,
    *,
    enabled: bool,
    agent_loader: ExpertAgentLoader,
    data_base_dir: Path | None,
    dialect: str,
    skill_scanner: SkillScanner | None = None,
) -> tuple[ExpertPackage | None, list[PackageError]]:
    """加载一个平铺声明式包，不继承任何外层套件配置。"""
    errors: list[PackageError] = []
    try:
        await asyncio.to_thread(_reject_marketplace_data_placeholder, install_path)
        (
            manifest,
            skills,
            agents,
            commands,
            output_styles,
        ) = await asyncio.to_thread(
            _build_manifest_and_capability,
            expert_id,
            install_path,
            agent_loader=agent_loader,
            data_base_dir=data_base_dir,
            skill_scanner=skill_scanner,
        )
        version = manifest.version
        expert_namespace = manifest.name or expert_id.name
        loaded = ExpertPackage(
            name=expert_id.name,
            source=expert_id.source,
            path=str(install_path),
            enabled=enabled,
            version=version,
            manifest=manifest,
            skills=skills,
            agents=agents,
            commands=commands,
            output_styles=output_styles,
            root_path=install_path,
            expert_name=expert_namespace,
        )
        return loaded, errors
    except ExpertError as exc:
        logger.warning("Failed to load %s expert %s: %s", dialect, expert_id, exc)
        errors.append(
            PackageError(
                type=exc.code.value,
                source=expert_id.source,
                expert=expert_id.name,
                message=str(exc),
                detail=exc.detail,
            )
        )
        return None, errors
    except Exception as exc:
        logger.warning("Failed to load %s expert %s: %s", dialect, expert_id, exc)
        errors.append(
            PackageError(
                type="manifest_parse_error",
                source=expert_id.source,
                expert=expert_id.name,
                message=str(exc),
            )
        )
        return None, errors
