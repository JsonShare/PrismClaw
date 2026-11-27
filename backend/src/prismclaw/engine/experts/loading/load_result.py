"""专家包加载结果与结构化错误。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prismclaw.schema.agent.domains import PackageErrorType

if TYPE_CHECKING:
    from prismclaw.engine.experts.manifest import ExpertManifest


@dataclass(slots=True)
class PackageError:
    """结构化专家包错误；具体子类固定 ``type`` 并提供上下文化消息。"""

    type: PackageErrorType | str = ""
    source: str = ""
    expert: str | None = None
    message: str | None = None
    detail: dict[str, Any] | None = None

    def format_message(self) -> str:
        if self.message:
            return self.message
        expert_part = f"[{self.expert}] " if self.expert else ""
        return f"{expert_part}{self.type} (source={self.source})"


@dataclass(slots=True)
class ManifestParseError(PackageError):
    """SKILL.md / plugin.json / manifest frontmatter YAML 解析失败。"""

    file_path: str = ""
    line_no: int | None = None
    type: PackageErrorType = field(default=PackageErrorType.MANIFEST_PARSE, init=False)

    def format_message(self) -> str:
        if self.message:
            return self.message
        line = f":{self.line_no}" if self.line_no is not None else ""
        expert = f"[{self.expert}] " if self.expert else ""
        return f"{expert}Failed to parse manifest {self.file_path}{line} (source={self.source})"


@dataclass(slots=True)
class ManifestValidationError(PackageError):
    """manifest frontmatter 字段不通过 pydantic / 手工 schema 校验。

    invalid_fields 对齐方案 §2.11.1 定义,每项为 dict 含 ``loc``(路径列表)
    ``msg``(可读错误) ``type``(错误类别)三键,与 pydantic ValidationError.errors()
    输出结构直接兼容,可 ``.extend(pydantic_err.errors())`` 零转换接入。
    """

    file_path: str = ""
    invalid_fields: list[dict[str, Any]] = field(default_factory=list)
    type: PackageErrorType = field(default=PackageErrorType.MANIFEST_VALIDATE, init=False)

    def format_message(self) -> str:
        if self.message:
            return self.message
        expert = f"[{self.expert}] " if self.expert else ""
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
        return f"{expert}Invalid manifest {self.file_path}: {field_msg} (source={self.source})"


@dataclass(slots=True)
class PackageDependencyError(PackageError):
    """依赖解析失败——缺 requirements、bins 不存在、路径不存在等。"""

    missing_requirements: list[str] = field(default_factory=list)
    invalid_paths: list[str] = field(default_factory=list)
    type: PackageErrorType = field(default=PackageErrorType.DEPENDENCY, init=False)

    def format_message(self) -> str:
        if self.message:
            return self.message
        expert = f"[{self.expert}] " if self.expert else ""
        parts: list[str] = []
        if self.missing_requirements:
            parts.append(f"缺少依赖：{', '.join(self.missing_requirements)}")
        if self.invalid_paths:
            parts.append(f"无效路径：{', '.join(self.invalid_paths)}")
        tail = " — ".join(parts) if parts else "（未填充缺失项或无效项）"
        return f"{expert}依赖检查失败：{tail}（source={self.source}）"


@dataclass(slots=True)
class PackageActivationError(PackageError):
    """SkillActivator.evaluate() 条件评估异常(缺 context、条件表达式错误)。"""

    condition_name: str = ""
    condition_value: Any = None
    type: PackageErrorType = field(default=PackageErrorType.ACTIVATION, init=False)

    def format_message(self) -> str:
        if self.message:
            return self.message
        expert = f"[{self.expert}] " if self.expert else ""
        value = f"={self.condition_value!r}" if self.condition_value is not None else ""
        return f"{expert}无法评估激活条件 {self.condition_name}{value}（source={self.source}）"


@dataclass(slots=True)
class PackageActivationRuntimeError(PackageError):
    """技能激活后运行前的 hook / per-expert 档案加载 / skill body 注入抛异常。"""

    skill_name: str = ""
    exc_type: str | None = None
    type: PackageErrorType = field(default=PackageErrorType.ACTIVATION_RUNTIME, init=False)

    def format_message(self) -> str:
        if self.message:
            return self.message
        expert = f"[{self.expert}] " if self.expert else ""
        exc = f" ({self.exc_type})" if self.exc_type else ""
        return f"{expert}激活技能 '{self.skill_name}' 时发生运行时错误{exc}（source={self.source}）"


@dataclass(slots=True)
class ExpertPackage:
    """已加载专家包描述。

    直接持有结构化字段，ExtensionManager._build_expert_descriptor 直接读取构建 ExpertDescriptor。
    """

    name: str
    source: str
    path: str
    enabled: bool = True
    version: str | None = None
    manifest: ExpertManifest | None = None
    # 结构化能力字段（声明式专家包加载产物）
    skills: list[Any] = field(default_factory=list)
    # 继承的全局基础技能（extra_skill_dirs）。仅用于运行时能力装配，
    # 不计入本包技能清单，也不参与 selected_skill_ids 等选择层 ID 生成。
    inherited_skills: list[Any] = field(default_factory=list)
    agents: list[Any] = field(default_factory=list)
    commands: list[Any] = field(default_factory=list)
    output_styles: dict[str, str] = field(default_factory=dict)
    root_path: Path | None = None
    # 平铺包名，来源为包目录名 / manifest.name。
    expert_name: str | None = None
    # 专家包指令：PLUGIN.md 内容（当前包级）
    # 包含安全规则、输出规范、方法论、护栏等关键行为定义。

    @property
    def expert_id(self) -> str:
        return f"{self.name}@{self.source}"


@dataclass(slots=True)
class PackageLoadResult:
    """三段式加载结果。"""

    enabled: list[ExpertPackage] = field(default_factory=list)
    disabled: list[ExpertPackage] = field(default_factory=list)
    errors: list[PackageError] = field(default_factory=list)

    @property
    def all_experts(self) -> list[ExpertPackage]:
        return [*self.enabled, *self.disabled]

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)
