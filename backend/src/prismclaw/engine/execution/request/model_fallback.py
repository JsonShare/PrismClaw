"""Model fallback chain for automatic model switching.

Provides 2-level model fallback when the primary model is overloaded:
- Primary model (e.g. claude-sonnet-4) fails with RATE_LIMIT/529/503
- Fallback to secondary model (e.g. claude-haiku)
- Clear thinking signatures from history on model switch
- Track fallback count, respect max_fallback_count limit

WP-4.1: Core fallback logic.
WP-4.2: Runner integration (see runner.py).

The :class:`ModelSpec` registry + :func:`get_model_spec` +
:func:`get_effective_context_window` are the source of truth for
budget-aware compaction.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from prismclaw.engine.foundation.metrics import emit
from prismclaw.schema.config.config import ProviderModelSpecs

logger = logging.getLogger(__name__)

# Thinking signature patterns to strip on model switch
_THINKING_PATTERNS = [
    re.compile(r'<thinking>.*?</thinking>', re.DOTALL),
    re.compile(r'<antml:thinking>.*?</antml:thinking>', re.DOTALL),
    re.compile(r'<thinking>\s*</thinking>'),  # empty thinking blocks
]

# Error codes that trigger fallback
FALLBACK_ERROR_CODES = frozenset({
    "rate_limit",
    "overloaded",
    "server_error",
})


@dataclass
class FallbackConfig:
    """Configuration for model fallback chain."""
    primary_model: str
    fallback_model: str | None = None
    max_fallback_count: int = 2


@dataclass
class ModelFallbackChain:
    """Tracks model fallback state and provides fallback decisions.

    Usage::

        chain = ModelFallbackChain(config=FallbackConfig(
            primary_model="claude-sonnet-4",
            fallback_model="claude-haiku",
        ))
        # On LLMError:
        if chain.should_fallback(error):
            effective_model = chain.get_fallback_model()
    """

    config: FallbackConfig
    _fallback_count: int = 0
    _current_model: str = ""
    _strip_attempted: bool = False

    def __post_init__(self) -> None:
        self._current_model = self.config.primary_model

    @property
    def current_model(self) -> str:
        """The model that should be used for the next call."""
        return self._current_model

    @property
    def fallback_count(self) -> int:
        return self._fallback_count

    @property
    def has_fallback_model(self) -> bool:
        return self.config.fallback_model is not None

    def should_fallback(self, error_type: str) -> bool:
        """Decide if we should fallback to another model.

        Returns True if:
        1. Error type is in FALLBACK_ERROR_CODES
        2. We haven't exceeded max_fallback_count
        3. A fallback model is configured
        """
        if error_type not in FALLBACK_ERROR_CODES:
            return False
        if self._fallback_count >= self.config.max_fallback_count:
            return False
        if self.config.fallback_model is None:
            return False
        return True

    def get_fallback_model(self) -> str:
        """Switch to the fallback model and return its name.

        Increments fallback_count. After max_fallback_count calls,
        subsequent calls still return the last fallback model.
        """
        self._fallback_count += 1
        self._strip_attempted = False  # Reset for new model
        self._current_model = self.config.fallback_model or self.config.primary_model
        logger.info(
            "model fallback: switching to %s (count=%d/%d)",
            self._current_model,
            self._fallback_count,
            self.config.max_fallback_count,
        )
        return self._current_model

    def strip_thinking_signatures(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove thinking signatures from message history.

        When switching models, thinking blocks from the previous model
        are not valid and must be cleared to avoid API errors.
        """
        if self._strip_attempted:
            return messages  # Already stripped this round

        self._strip_attempted = True
        result = []
        changed = False
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, str):
                new_content = content
                for pattern in _THINKING_PATTERNS:
                    new_content = pattern.sub("", new_content)
                if new_content != content:
                    changed = True
                    result.append({**msg, "content": new_content.strip()})
                else:
                    result.append(msg)
            elif isinstance(content, list):
                new_blocks = []
                for block in content:
                    if isinstance(block, dict):
                        block_type = block.get("type", "")
                        # thinking / redacted_thinking: 保留 block 但移除 signature
                        # 跨模型切换时新模型无法验证旧签名，必须清除
                        if block_type in ("thinking", "redacted_thinking"):
                            if "signature" in block:
                                changed = True
                                new_block = {k: v for k, v in block.items() if k != "signature"}
                                new_blocks.append(new_block)
                            else:
                                new_blocks.append(block)
                            continue
                        text = block.get("text", "")
                        if isinstance(text, str):
                            new_text = text
                            for pattern in _THINKING_PATTERNS:
                                new_text = pattern.sub("", new_text)
                            if new_text != text:
                                changed = True
                                new_blocks.append({**block, "text": new_text.strip()})
                            else:
                                new_blocks.append(block)
                        else:
                            new_blocks.append(block)
                    else:
                        new_blocks.append(block)
                if changed:
                    result.append({**msg, "content": new_blocks})
                else:
                    result.append(msg)
            else:
                result.append(msg)

        if changed:
            logger.info("stripped thinking signatures from %d messages for model switch", len(messages))
        return result

    def reset(self) -> None:
        """Reset to primary model (e.g. after successful response)."""
        self._current_model = self.config.primary_model
        self._fallback_count = 0
        self._strip_attempted = False

    @property
    def state_summary(self) -> dict[str, Any]:
        return {
            "primary_model": self.config.primary_model,
            "fallback_model": self.config.fallback_model,
            "current_model": self._current_model,
            "fallback_count": self._fallback_count,
            "max_fallback_count": self.config.max_fallback_count,
        }


# AutoCompact budget observability:
#   * ``budget.effective_window``   gauge, label ``model``
#   * ``budget.spec_miss``          counter (UnknownModelError per call)
_METRIC_BUDGET_EFFECTIVE = "budget.effective_window"
_METRIC_BUDGET_SPEC_MISS = "budget.spec_miss"


class UnknownModelError(KeyError):
    """Raised when a model id has no registered :class:`ModelSpec`.

    Do not silently fall back to a 128K default for unknown models.
    Callers that want graceful degradation should catch this and
    provide their own spec via :func:`register_model_spec`.
    """


@dataclass(frozen=True)
class ModelSpec:
    """Static per-model capability descriptor.

    In-process registry, sourced from
    ``AgentSettings.providers.<provider>.models.<model_id>``
    (each ``ModelEntry`` declares ``contextWindow`` and
    ``maxOutputTokens``).  Callers can also inject specs at runtime
    via :func:`register_model_spec` (used by tests).
    """

    model_id: str
    context_window: int
    max_output_tokens: int


# In-process registry.  Populated from ``AgentSettings.providers``
# (loaded from ``backend/configs/server.yaml``) and from runtime
# registrations via :func:`register_model_spec`.  No hard-coded
# model list here — the config file is the source of truth so the
# operator's ``default_model`` and provider list stays consistent
# with the budget math.
_SPEC_REGISTRY: dict[str, ModelSpec] = {}
_provider_specs: ProviderModelSpecs | None = None


def hydrate_spec_registry(specs: ProviderModelSpecs) -> None:
    """Hydrate _SPEC_REGISTRY from provider model specs (called by wiring layer at startup)."""
    global _provider_specs
    _provider_specs = specs
    for _provider_name, provider in specs.providers.items():
        for model_id, entry in provider.items():
            if entry.context_window is None or entry.max_output_tokens is None:
                continue
            try:
                _SPEC_REGISTRY[str(model_id)] = ModelSpec(
                    model_id=str(model_id),
                    context_window=int(entry.context_window),
                    max_output_tokens=int(entry.max_output_tokens),
                )
            except Exception:
                continue


def _sync_registry_from_settings() -> None:
    """Hydrate :data:`_SPEC_REGISTRY` from :data:`_provider_specs`.

    Called lazily on first spec lookup.  The primary hydration path
    is :func:`hydrate_spec_registry`, invoked by the wiring layer
    at startup via ``runner_assembly.build_kernel_runtime()``.
    """
    if _provider_specs is None:
        return


def _ensure_registry_loaded() -> None:
    """Lazily load the registry on first access.  Tests that have
    populated the registry via :func:`register_model_spec` are
    unaffected because they look up directly without going through
    :func:`_sync_registry_from_settings` after a force-reload.
    """
    if not _SPEC_REGISTRY:
        _sync_registry_from_settings()


def register_model_spec(spec: ModelSpec) -> None:
    """Register or override a :class:`ModelSpec` at runtime.

    Useful for tests and for callers that have resolved the
    model capabilities via an external source (e.g. ``litellm``).
    The runtime registry takes precedence over the config-sourced
    registry so tests can override the operator's choice without
    mutating the file.
    """
    _SPEC_REGISTRY[spec.model_id] = spec

def _normalize_model_id(model: str) -> str:
    """Strip common provider prefixes so ``anthropic/claude-sonnet-4-...``
    resolves to the bare ``claude-sonnet-4-...`` spec.
    """
    lowered = model.lower()
    for prefix in (
        "anthropic/",
        "bedrock/anthropic.",
        "vertex_ai/claude-",
        "openai/",
        "azure/",
    ):
        if lowered.startswith(prefix):
            return model[len(prefix):]
    return model


def get_model_spec(model: str) -> ModelSpec:
    """Look up the :class:`ModelSpec` for a model id.

    Raises :class:`UnknownModelError` if the model is not in the
    registry.  Do **not** silently fall back to a default spec;
    callers must register the model explicitly when they have
    authoritative capability data.
    """
    _ensure_registry_loaded()
    normalized = _normalize_model_id(model)
    if normalized in _SPEC_REGISTRY:
        return _SPEC_REGISTRY[normalized]
    # Also try the raw id (in case caller passes the canonical name).
    if model in _SPEC_REGISTRY:
        return _SPEC_REGISTRY[model]
    try:
        emit(
            _METRIC_BUDGET_SPEC_MISS,
            1.0,
            kind="counter",
            labels={"model": normalized},
        )
    except Exception:
        pass
    raise UnknownModelError(
        f"no ModelSpec registered for model {model!r}; "
        f"register one via prismclaw.engine.execution.request.model_fallback.register_model_spec"
    )


def get_effective_context_window(
    model: str,
    *,
    max_output_tokens_for_summary: int,
    fallback: int | None = None,
) -> int:
    """Return the effective context window for a model.

    Mirrors Claude's ``getEffectiveContextWindowSize(model)``::

        effective = context_window - min(max_output_tokens,
                                         MAX_OUTPUT_TOKENS_FOR_SUMMARY)

    ``max_output_tokens_for_summary`` is the operator-controlled upper
    bound (Claude uses ``MAX_OUTPUT_TOKENS_FOR_SUMMARY = 20_000``).

    When ``fallback`` is provided, return it instead of raising
    :class:`UnknownModelError` for unknown models.  This is used by
    the soft-budget pre-check, which must never crash the run loop
    on a missing spec.
    """
    try:
        spec = get_model_spec(model)
    except UnknownModelError:
        if fallback is None:
            raise
        try:
            emit(
                _METRIC_BUDGET_SPEC_MISS,
                1.0,
                kind="counter",
                labels={"model": _normalize_model_id(model)},
            )
        except Exception:
            pass
        return int(fallback)
    reserved_for_summary = min(spec.max_output_tokens, max_output_tokens_for_summary)
    effective = spec.context_window - reserved_for_summary
    try:
        emit(
            _METRIC_BUDGET_EFFECTIVE,
            float(effective),
            kind="gauge",
            labels={"model": spec.model_id},
        )
    except Exception:
        pass
    return effective


def resolve_model_max_output(model: str) -> int | None:
    """Resolve a model's advertised max output tokens.

    Source of truth is the operator's config registry
    (``providers.<p>.models.<m>.maxOutputTokens``) via
    :func:`get_model_spec`; ``litellm.get_model_info`` is only a
    secondary fallback for models not declared in configs (e.g.
    third-party ids). Returns ``None`` when neither source knows the
    model, letting callers apply their own ratio fallback.
    """
    try:
        return get_model_spec(model).max_output_tokens
    except UnknownModelError:
        pass
    try:
        from litellm import get_model_info

        info = get_model_info(model)
        value = info.get("max_output_tokens") or info.get("max_tokens")
        return int(value) if value else None
    except Exception:
        return None
