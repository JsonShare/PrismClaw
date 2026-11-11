"""LLM provider model type definitions — pure data, no env-var binding."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ModelModalities(BaseModel):
    input: set[str] = Field(default_factory=lambda: {"text"})
    output: set[str] = Field(default_factory=lambda: {"text"})

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class ModelEntry(BaseModel):
    modalities: ModelModalities = Field(default_factory=ModelModalities)
    # model capability hints.  Optional so existing
    # providers that only declare modalities continue to work.
    context_window: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Advertised context window in tokens. Used by "
            "model_fallback.get_effective_context_window to derive "
            "the budget-aware effective context window per model."
        ),
    )
    max_output_tokens: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Advertised max output tokens. Subtracted from "
            "context_window to derive the effective window."
        ),
    )

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class ProviderConfig(BaseModel):
    """Provider configuration — pure data, no env-var binding.

    The infra-layer ``ProviderSettings`` inherits from this and adds
    ``pydantic_settings.BaseSettings`` for env-var / JSON-file binding.
    Engine layer code should depend on this type, not the concrete
    ``ProviderSettings``.
    """

    api_key: str | None = Field(default=None)
    base_url: str | None = Field(default=None)
    models: dict[str, ModelEntry] = Field(default_factory=dict)
    prefixes: list[str] = Field(default_factory=list)
    llm_provider: str | None = Field(default=None)

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    @field_validator("base_url", mode="before")
    @classmethod
    def _sanitize_base_url(cls, value):
        if value is None:
            return None
        s = str(value)
        prev: str | None = None
        while prev != s:
            prev = s
            s = s.strip()
            if len(s) >= 2 and s[0] == s[-1] and s[0] in ("`", "'", '"'):
                s = s[1:-1]
                continue
            s = s.strip("`").strip()
        s = s.replace("`", "").strip()
        return s or None

    @field_validator("api_key", mode="before")
    @classmethod
    def _empty_api_key_to_none(cls, value):
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("models", mode="before")
    @classmethod
    def _reject_list_form(cls, value):
        if isinstance(value, list):
            raise ValueError(
                "ProviderConfig.models must be a dict {model_id: ModelEntry}, got list. "
                'Migration: change "models": [...] to '
                '"models": {model_id: {"modalities": {"input": [...], "output": [...]}}}. '
            )
        return value
