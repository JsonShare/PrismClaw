from __future__ import annotations


class ClawHubError(Exception):
    message: str
    status_code: int

    def __init__(self, message: str, status_code: int = 0) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class SkillInstallError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)
