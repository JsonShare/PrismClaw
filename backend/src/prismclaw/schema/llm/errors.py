"""LLM error types — shared across layers.

Moved from engine/services/llm/errors.py to contracts so that
web/persistence layers can reference these without coupling to engine.
"""


class LLMErrorCode:
    CONTEXT_OVERFLOW = "context_overflow"
    RATE_LIMIT = "rate_limit"
    AUTH_ERROR = "auth_error"
    TIMEOUT = "timeout"
    CONNECTION_ERROR = "connection_error"
    PROVIDER_NOT_FOUND = "provider_not_found"
    VISION_NOT_SUPPORTED = "vision_not_support"
    UNKNOWN = "unknown"


class LLMError(Exception):
    def __init__(self, code: str, message: str, original: Exception | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.original = original


__all__ = ["LLMErrorCode", "LLMError"]
