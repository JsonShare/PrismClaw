from __future__ import annotations

from enum import StrEnum

PROTOCOL_VERSION = 1


class ProtocolErrorCode(StrEnum):
    VERSION_MISMATCH = "version_mismatch"
    AUTH_FAILED = "auth_failed"
    SESSION_NOT_FOUND = "session_not_found"
    SESSION_BUSY = "session_busy"
    RATE_LIMITED = "rate_limited"
    INVALID_REQUEST = "invalid_request"
    INTERNAL_ERROR = "internal_error"


__all__ = ["PROTOCOL_VERSION", "ProtocolErrorCode"]
