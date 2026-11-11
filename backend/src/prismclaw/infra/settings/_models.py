from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from prismclaw.schema.llm.provider_types import ProviderConfig


class EmbeddingSettings(BaseSettings):
    model: str = ""
    api_key: str = Field("")
    base_url: str = Field("")
    llm_provider: str | None = Field(default=None)
    dimensions: int = 4096

    model_config = SettingsConfigDict(
        env_prefix="PRISMCLAW_EMBEDDING_",
        populate_by_name=True,
    )


class VisionSettings(BaseSettings):
    """Vision model configuration for read_image tool (OCR + image captioning)."""

    model: str = Field("Qwen3-VL-30B-A3B-Instruct")
    api_key: str = Field("")
    base_url: str = Field("http://211.157.155.210:8006/v1")
    llm_provider: str | None = Field(default=None)
    timeout_seconds: int = Field(90, gt=0)
    # 单张内联图片 token 估算 + 单轮内联上限（ratio A_eff 扣除项）。
    tokens_per_image: int = Field(1024, ge=0)
    max_images_inline: int = Field(4, ge=0)

    model_config = SettingsConfigDict(
        env_prefix="PRISMCLAW_VISION_",
        populate_by_name=True,
    )


class SpeechSettings(BaseSettings):
    """Speech-to-text (ASR) configuration for voice input in the chat box.

    只保留流式（WebSocket）形态：Qwen3-ASR 的流式推理仅存在于 qwen-asr
    Python 包内，vLLM 的 HTTP 服务不提供增量接口，因此由独立进程承载，
    见 scripts/qwen3_asr_stream_server.py。
    """

    language: str = Field("zh")
    # 流式 ASR 旁路服务地址，例如 ws://127.0.0.1:8021/ws/asr；留空即关闭语音输入
    stream_url: str = Field("")
    # 旁路服务连接超时（秒）：首次建模/建连可能较慢
    stream_connect_timeout: int = Field(10, gt=0)

    model_config = SettingsConfigDict(
        env_prefix="PRISMCLAW_SPEECH_",
        populate_by_name=True,
    )


class ProviderSettings(BaseSettings, ProviderConfig):
    """Provider configuration with env-var / JSON-file binding.

    Inherits data fields and validators from :class:`ProviderConfig`.
    Engine layer code should depend on ``ProviderConfig``, not this type.
    """

    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")


class ModelsSettings(BaseSettings):
    """模型注册与默认路由配置（models 块，models.yaml）。

    从原 AgentSettings 拆出：模型相关配置独立成组，与 agent 运行配置分离。
    """

    default_model: str = ""
    providers: dict[str, ProviderSettings] = Field(default_factory=dict)
    default_provider: str | None = None
    unknown_prefix_policy: Literal["fail", "default"] = "fail"

    model_config = SettingsConfigDict(
        env_prefix="PRISMCLAW_MODELS_", extra="ignore", populate_by_name=True
    )
