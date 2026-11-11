from __future__ import annotations

from enum import Enum

PROTOCOL_VERSION = 1


class ProtocolErrorCode(str, Enum):
    VERSION_MISMATCH = "version_mismatch"
    AUTH_FAILED = "auth_failed"
    SESSION_NOT_FOUND = "session_not_found"
    SESSION_BUSY = "session_busy"
    RATE_LIMITED = "rate_limited"
    INVALID_REQUEST = "invalid_request"
    INTERNAL_ERROR = "internal_error"


__all__ = ["PROTOCOL_VERSION", "ProtocolErrorCode"]
