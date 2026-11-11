from __future__ import annotations

from pydantic import BaseModel, Field


class EvidenceRef(BaseModel):
    """一条证据引用——从原始来源到陈述的可追溯链接。

    自动从 subagent 的 tool call 历史中提取，不依赖 LLM 手动填写。

    领域扩展通过 domain_extensions 存放，key 格式 ``{domain}.{field}``
    （如 ``medical.evidence_level``、``legal.source_type``）。
    各域模块通过 EvidenceInferencer 注册表填充，核心不包含域逻辑。
    """

    source_type: str = ""          # "file" | "web" | "tool_output" | "reasoning"
    source_path: str = ""          # 文件路径 / URL / 工具调用 ID
    source_location: str = ""      # 行号 / 段落 / 锚点
    quoted_text: str = ""          # 原始证据的精确引用（防止改写失真）
    confidence: str = "high"       # "high" | "medium" | "low" | "unverified"

    # 领域扩展——由各域 EvidenceInferencer 自动填充
    # key 格式: "{domain}.{field}"，值均为字符串
    domain_extensions: dict[str, str] = Field(default_factory=dict)

    # -- deprecated: 以下字段保留向后兼容，新代码应使用 domain_extensions --
    evidence_level: str = ""       # [deprecated] 循证等级，现由 domain_extensions["medical.evidence_level"] 承载
    recommendation_grade: str = "" # [deprecated] 推荐等级，现由 domain_extensions["medical.recommendation_grade"] 承载
    legal_source_type: str = ""    # [deprecated] 法规类型，现由 domain_extensions["legal.source_type"] 承载
    legal_effective_date: str = "" # [deprecated] 法规生效日期，现由 domain_extensions["legal.effective_date"] 承载
    legal_status: str = ""         # [deprecated] 效力状态，现由 domain_extensions["legal.status"] 承载


class SubagentContextSnapshot(BaseModel):
    task_goal: str
    done_so_far: list[str] = Field(default_factory=list)
    key_findings: list[str] = Field(default_factory=list)
    prior_evidence: list[EvidenceRef] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    expected_output: str | None = None
    system_prompt_section_names: list[str] = Field(default_factory=list)
    system_prompt_cache_break_sections: list[str] = Field(default_factory=list)
    active_skill_name: str | None = None
    invoked_skill_names: list[str] = Field(default_factory=list)


class SubagentResultEnvelope(BaseModel):
    summary: str = ""
    findings: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    recommended_next_actions: list[str] = Field(default_factory=list)
    confidence_assessment: str = ""  # "high" | "medium" | "low"
