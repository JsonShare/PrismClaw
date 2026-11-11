from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from prismclaw.schema.config.config import (
    BudgetPricingConfig,
    CompletionControlConfig,
    ContextConfig,
    RetryConfig,
    RunLoopTuningConfig,
    TimeoutConfig,
)


class AgentSettings(BaseSettings):
    """Agent 运行配置（agent 块，agent.yaml）。模型相关字段已移至 ModelsSettings。"""

    max_iterations: int = 70

    # 工具数组变更策略：append_only 顺序稳定+只追加（保护好前缀缓存）；
    # frozen 需预枚举全部能力；unbounded 放弃缓存红利。
    tool_mutation_policy: Literal["append_only", "frozen", "unbounded"] = "append_only"

    # 成本天花板（loop 防护第 3 层）：单任务 USD 上限 + 折算费率。
    # None = 不设上限；费率单位 USD / 百万 token。
    max_budget_usd: float | None = Field(default=None, ge=0)
    budget_pricing: BudgetPricingConfig = Field(default_factory=BudgetPricingConfig)

    # 上下文组装与压缩（收敛原 max_context_tokens / promptBudget /
    # compaction / tools.max_output_chars 四处配置）
    context: ContextConfig = Field(default_factory=ContextConfig)

    # 完成控制：结构化输出校验失败的修复额度上限。
    control: CompletionControlConfig = Field(default_factory=CompletionControlConfig)

    timeouts: TimeoutConfig = Field(default_factory=TimeoutConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    run_loop: RunLoopTuningConfig = Field(default_factory=RunLoopTuningConfig)

    model_config = SettingsConfigDict(
        env_prefix="PRISMCLAW_AGENT_", extra="ignore", populate_by_name=True
    )


class ServerSettings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000

    model_config = SettingsConfigDict(env_prefix="PRISMCLAW_SERVER_")
