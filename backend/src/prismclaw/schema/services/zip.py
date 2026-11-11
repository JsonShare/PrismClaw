"""Zip extraction/verification callback protocols — injected by runtime layer."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class ZipExtractFn(Protocol):
    """Protocol for the zip extraction callback.

    Matches :func:`prismclaw.runtime.skills.installer._extract_zip` signature.
    """

    def __call__(
        self,
        zip_bytes: bytes,
        target_dir: Path,
        max_archive_bytes: int = 256_000_000,
        max_extracted_bytes: int = 512_000_000,
        max_entries: int = 50_000,
    ) -> None: ...


@runtime_checkable
class ZipVerifyFn(Protocol):
    """Protocol for the zip verification callback.

    Matches :func:`prismclaw.runtime.skills.installer._verify_integrity` signature.
    """

    def __call__(self, zip_bytes: bytes, expected_hash: str | None) -> None: ...
