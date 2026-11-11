"""Environment-driven configuration for the web-search-mcp service.

All knobs are prefixed with ``WSM_`` so the service stays self-contained and
shares no configuration files with the PrismClaw main app.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_csv(raw: str) -> list[str]:
    return [part.strip() for part in raw.replace(",", "\n").splitlines() if part.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WSM_",
        env_file=".env",
        env_file_encoding="utf-8",
        populate_by_name=True,
        extra="ignore",
    )

    # ── Auth (HTTP transport only; stdio ignores it) ──────────────────
    token: str = Field(
        default="",
        description="Shared static token required in X-WSM-Token for HTTP requests.",
    )

    # ── Search providers ──────────────────────────────────────────────
    tavily_api_keys: str = Field(
        default="",
        description="Comma/newline separated Tavily API keys (empty disables Tavily).",
    )
    anysearch_api_key: str = Field(
        default="",
        description="AnySearch API key (empty disables AnySearch). Free: 1000 req/day, 20 QPS.",
    )

    # ── URL policy ────────────────────────────────────────────────────
    allowed_schemes: str = Field(
        default="https",
        description="Comma separated scheme allowlist for result/fetch URLs.",
    )
    allowed_domains: str = Field(
        default="",
        description="Comma separated domain allowlist; empty allows all public domains.",
    )

    # ── Budgets / timeouts ────────────────────────────────────────────
    default_num_results: int = 6
    max_num_results: int = 20
    max_per_host: int = 2
    max_fetch_pages: int = 3
    page_max_chars: int = 8_000
    search_timeout_s: float = 12.0
    fetch_timeout_s: float = 15.0
    total_timeout_s: float = 45.0

    # ── Retrieval agent loop ──────────────────────────────────────────
    max_agent_rounds: int = Field(
        default=2,
        description="Max search rounds (1 = no follow-up reflection).",
    )
    max_query_variants: int = Field(
        default=2,
        description=(
            "Max derived query variants per round (original excluded from the count for round 1)."
        ),
    )
    min_sources_before_followup: int = Field(
        default=3,
        description=(
            "If fewer usable sources survive a round and rounds remain, derive follow-up queries."
        ),
    )
    report_excerpt_chars: int = Field(
        default=200,
        description="Per-source excerpt length in the synthesized markdown report.",
    )

    # ── Cache ─────────────────────────────────────────────────────────
    cache_ttl_seconds: float = 900.0
    cache_max_size: int = 256

    # ── Observability ─────────────────────────────────────────────────
    log_level: str = "INFO"

    @property
    def tavily_keys(self) -> list[str]:
        return _split_csv(self.tavily_api_keys)

    @property
    def scheme_list(self) -> list[str]:
        schemes = _split_csv(self.allowed_schemes)
        return schemes or ["https"]

    @property
    def domain_list(self) -> list[str]:
        return _split_csv(self.allowed_domains)
