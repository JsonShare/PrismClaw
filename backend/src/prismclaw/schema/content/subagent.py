from __future__ import annotations

from pydantic import BaseModel, Field


class EvidenceRef(BaseModel):
    """一条证据引用——从原始来源到陈述的可追溯链接。

    自动从 subagent 的 tool call 历史中提取，不依赖 LLM 手动填写。

    领域扩展通过 domain_extensions 存放，key 格式 ``{domain}.{field}``
    （如 ``my_domain.evidence_level``）。
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
