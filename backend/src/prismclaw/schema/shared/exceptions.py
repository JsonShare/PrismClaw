"""Shared exception types — zero prismclaw internal dependencies."""


class SkillParseError(Exception):
    file_path: str
    message: str

    def __init__(self, message: str, file_path: str = "") -> None:
        self.file_path = file_path
        self.message = message
        super().__init__(message)


class MCPServerDeadError(Exception):
    """Raised when an MCP tool call detects the backing server has died."""

    def __init__(self, server_name: str, reason: str) -> None:
        super().__init__(f"MCP server {server_name!r} is unavailable: {reason}")
        self.server_name = server_name
        self.reason = reason


class AgentAbortedError(Exception):
    """Raised when an agent run is aborted (user cancel / race condition)."""

    def __init__(self, kind: str = "aborted") -> None:
        super().__init__(kind)
        self.kind = kind


class LockAcquireError(Exception):
    """Raised when a distributed lock cannot be acquired."""

    def __init__(self, key: str) -> None:
        super().__init__(f"could not acquire lock: {key!r}")
        self.key = key


class LockLostError(Exception):
    """Raised when a previously held distributed lock is lost."""

    def __init__(self, key: str) -> None:
        super().__init__(f"lock lost: {key!r}")
        self.key = key


class AgentTimeoutError(Exception):
    """Raised when an agent operation exceeds its time limit."""

    def __init__(self, kind: str, limit_seconds: float) -> None:
        super().__init__(f"{kind} timeout after {limit_seconds}s")
        self.kind = kind
        self.limit_seconds = limit_seconds
