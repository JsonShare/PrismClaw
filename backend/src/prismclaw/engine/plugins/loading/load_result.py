"""插件加载结果与结构化错误。

对标参考实现 ``types/plugin.ts`` 的 ``PluginLoadResult`` + ``PluginError``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

from prismclaw.schema.agent.domains import PluginErrorType

if TYPE_CHECKING:
    from prismclaw.schema.agent.capability import RuntimeCapabilities
    from prismclaw.engine.plugins.manifest import PluginManifest
    from prismclaw.schema.agent.capability import (
        PluginCapabilityGraph,
    )


@dataclass(slots=True)
class PluginError:
    """结构化错误。参考实现 ``PluginError``。

    子类化体系 + 统一 format_message 方法。基类 type 是 PluginErrorType 枚举
    （str 子类，与裸字符串 100% 等价），下游可 isinstance 判断进入结构化字段，
    或继续读取 message/detail 字符串。

    关于 type/source 的默认值：
    5 个子类（ManifestParseError 等）会用同名字段「覆盖基类声明并提供默认值」。
    Python dataclass 继承规则下，字段位置由基类中的首次出现决定，默认值由最后
    一次（子类）声明决定。若仅给子类 type 设默认、基类 type/source 无默认，
    则展开后 schema 会出现 ``type(有默认) → source(无默认)``，在 Python 3.12
    下触发 ``non-default argument follows default argument``。
    解决方案：基类 type/source 统一设默认值 ``""``，保证任何继承场景下
    「非默认字段都在默认字段之前」；业务语义上的必填由下游构造代码保障。
    """

    type: PluginErrorType | str = ""  # 空串兜底,实际使用时子类覆盖
    source: str = ""
    plugin: str | None = None
    message: str | None = None
    detail: dict[str, Any] | None = None

    def format_message(self) -> str:
        """按错误类型 / 子类结构化字段生成可读错误消息。

        子类覆盖此方法可插入 file_path、missing 列表等上下文。默认实现
        返回 message 字段或 fallback 到 type + source。
        """
        if self.message:
            return self.message
        plugin_part = f"[{self.plugin}] " if self.plugin else ""
        return f"{plugin_part}{self.type} (source={self.source})"


@dataclass(slots=True)
class ManifestParseError(PluginError):
    """SKILL.md / plugin.json / manifest frontmatter YAML 解析失败。"""

    file_path: str = ""
    line_no: int | None = None
    # 默认 type 与 source 覆盖基类无默认值的字段，需通过 __post_init__ 校验
    type: PluginErrorType = PluginErrorType.MANIFEST_PARSE  # type: ignore[assignment]

    def __post_init__(self) -> None:
        # 若外部传了不匹配的 type 自动纠正为规范值（兼容旧字符串）
        if self.type != PluginErrorType.MANIFEST_PARSE:
            self.type = PluginErrorType.MANIFEST_PARSE

    def format_message(self) -> str:
        if self.message:
            return self.message
        line = f":{self.line_no}" if self.line_no is not None else ""
        plugin = f"[{self.plugin}] " if self.plugin else ""
        return f"{plugin}Failed to parse manifest {self.file_path}{line} (source={self.source})"


@dataclass(slots=True)
class ManifestValidationError(PluginError):
    """manifest frontmatter 字段不通过 pydantic / 手工 schema 校验。

    invalid_fields 对齐方案 §2.11.1 定义,每项为 dict 含 ``loc``(路径列表)
    ``msg``(可读错误) ``type``(错误类别)三键,与 pydantic ValidationError.errors()
    输出结构直接兼容,可 ``.extend(pydantic_err.errors())`` 零转换接入。
    """

    file_path: str = ""
    invalid_fields: list[dict[str, Any]] = field(default_factory=list)
    type: PluginErrorType = PluginErrorType.MANIFEST_VALIDATE  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.type != PluginErrorType.MANIFEST_VALIDATE:
            self.type = PluginErrorType.MANIFEST_VALIDATE

    def format_message(self) -> str:
        if self.message:
            return self.message
        plugin = f"[{self.plugin}] " if self.plugin else ""
        if self.invalid_fields:
            parts: list[str] = []
            for err in self.invalid_fields:
                if not isinstance(err, dict):
                    continue
                loc = err.get("loc")
                msg = str(err.get("msg") or "(no message)")
                if isinstance(loc, (list, tuple)) and loc:
                    loc_str = ".".join(str(x) for x in loc)
                elif isinstance(loc, str) and loc:
                    loc_str = loc
                else:
                    loc_str = "(unknown field)"
                parts.append(f"{loc_str}: {msg}")
            field_msg = "; ".join(parts) if parts else "(invalid_fields malformed)"
        else:
            field_msg = "(no invalid_fields populated)"
        return (
            f"{plugin}Invalid manifest {self.file_path}: {field_msg} "
            f"(source={self.source})"
        )


@dataclass(slots=True)
class PluginDependencyError(PluginError):
    """依赖解析失败——缺 requirements、bins 不存在、路径不存在等。"""

    missing_requirements: list[str] = field(default_factory=list)
    invalid_paths: list[str] = field(default_factory=list)
    type: PluginErrorType = PluginErrorType.DEPENDENCY  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.type != PluginErrorType.DEPENDENCY:
            self.type = PluginErrorType.DEPENDENCY

    def format_message(self) -> str:
        if self.message:
            return self.message
        plugin = f"[{self.plugin}] " if self.plugin else ""
        parts: list[str] = []
        if self.missing_requirements:
            parts.append(f"缺少依赖：{', '.join(self.missing_requirements)}")
        if self.invalid_paths:
            parts.append(f"无效路径：{', '.join(self.invalid_paths)}")
        tail = " — ".join(parts) if parts else "（未填充缺失项或无效项）"
        return f"{plugin}依赖检查失败：{tail}（source={self.source}）"


@dataclass(slots=True)
class PluginActivationError(PluginError):
    """SkillActivator.evaluate() 条件评估异常(缺 context、条件表达式错误)。"""

    condition_name: str = ""
    condition_value: Any = None
    type: PluginErrorType = PluginErrorType.ACTIVATION  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.type != PluginErrorType.ACTIVATION:
            self.type = PluginErrorType.ACTIVATION

    def format_message(self) -> str:
        if self.message:
            return self.message
        plugin = f"[{self.plugin}] " if self.plugin else ""
        value = f"={self.condition_value!r}" if self.condition_value is not None else ""
        return (
            f"{plugin}无法评估激活条件 {self.condition_name}{value}"
            f"（source={self.source}）"
        )


@dataclass(slots=True)
class PluginActivationRuntimeError(PluginError):
    """技能激活后运行前的 hook / per-plugin 档案加载 / skill body 注入抛异常。"""

    skill_name: str = ""
    exc_type: str | None = None
    type: PluginErrorType = PluginErrorType.ACTIVATION_RUNTIME  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.type != PluginErrorType.ACTIVATION_RUNTIME:
            self.type = PluginErrorType.ACTIVATION_RUNTIME

    def format_message(self) -> str:
        if self.message:
            return self.message
        plugin = f"[{self.plugin}] " if self.plugin else ""
        exc = f" ({self.exc_type})" if self.exc_type else ""
        return (
            f"{plugin}激活技能 '{self.skill_name}' 时发生运行时错误{exc}"
            f"（source={self.source}）"
        )


@dataclass(slots=True)
class LoadedPlugin:
    """已加载插件描述。对标参考实现 ``LoadedPlugin``。

    直接持有结构化字段，ExtensionManager._build_plugin_descriptor 直接读取构建 PluginDescriptor。
    """

    name: str
    source: str
    path: str
    enabled: bool = True
    version: str | None = None
    manifest: "PluginManifest | None" = None
    # 结构化能力字段
    capability_graph: "PluginCapabilityGraph | None" = None
    runtime_capabilities: "RuntimeCapabilities | None" = None
    resolved_user_config: dict[str, Any] = field(default_factory=dict)
    hooks: list[Any] = field(default_factory=list)
    hooks_config: dict[str, Any] = field(default_factory=dict)
    # 加载期由 hooks 工厂从 hooks_config 构建的可执行 handler（event → handlers）。
    # 下游 extension_manager 直接注册，无需再判断 handler 类型。
    hook_handlers: dict[str, list[Any]] = field(default_factory=dict)
    mcp_servers: dict[str, Any] = field(default_factory=dict)
    output_styles: dict[str, str] = field(default_factory=dict)
    root_path: Path | None = None
    # Python 原生插件持有的模块对象（None 表示声明式插件）
    native_module: Any | None = None
    native_tools: list[Any] = field(default_factory=list)
    native_hooks: list[Any] = field(default_factory=list)
    # 安装路径
    install_path: str | None = None
    # v5 渐进式披露：所属 marketplace 名称（如 medical-healthcare）
    # 子插件通过 porting_context 传递 marketplace_name，非 marketplace 插件为 None。
    marketplace_name: str | None = None
    # v5 渐进式披露：所属 marketplace 描述（来自 marketplace.json description）
    # 比 manifest.description 更完整，系统提示词优先使用此字段。
    marketplace_description: str | None = None
    # 插件指令：PRISMCLAW.md 内容（子插件级 + marketplace 级继承）
    # 包含安全规则、输出规范、方法论、护栏等关键行为定义。
    plugin_instructions: str | None = None

    @property
    def plugin_id(self) -> str:
        return f"{self.name}@{self.source}"

    @property
    def is_native(self) -> bool:
        """是否为 Python 原生插件（main.py + plugin_sdk）。"""
        return self.native_module is not None


@dataclass(slots=True)
class PluginLoadResult:
    """三段式加载结果。参考实现 ``PluginLoadResult``。"""

    enabled: list[LoadedPlugin] = field(default_factory=list)
    disabled: list[LoadedPlugin] = field(default_factory=list)
    errors: list[PluginError] = field(default_factory=list)

    @property
    def all_plugins(self) -> list[LoadedPlugin]:
        return [*self.enabled, *self.disabled]

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


@dataclass(slots=True)
class PluginLoadBatch:
    """单源加载批次（builtin / marketplace / session）。"""

    plugins: list[LoadedPlugin] = field(default_factory=list)
    errors: list[PluginError] = field(default_factory=list)

    @classmethod
    def empty(cls) -> "PluginLoadBatch":
        return cls()
