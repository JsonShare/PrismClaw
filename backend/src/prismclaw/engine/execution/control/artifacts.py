from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ArtifactSignature = tuple[int, int]  # (mtime_ns, size)


@dataclass(frozen=True)
class Scope:
    """产物扫描范围。root 为绝对路径根，prefixes 为相对 root 的子路径前缀。"""

    kind: Literal["workspace", "session", "external"]
    root: str
    prefixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ArtifactFacts:
    exists: bool
    size: int


class ArtifactScanner:
    """产物事实的唯一来源：只做文件系统 stat / 枚举，不含业务判断。"""

    def __init__(self, workspace_root: str | Path | None) -> None:
        self._workspace_root = Path(workspace_root) if workspace_root is not None else None

    def resolve(self, path: str, *, root: str | None = None) -> Path:
        normalized = path.replace("\\", "/")
        candidate = Path(normalized)
        if candidate.is_absolute():
            return candidate
        base = Path(root) if root is not None else (self._workspace_root or Path.cwd())
        return base / normalized

    def stat_paths(self, paths: Iterable[str]) -> dict[str, ArtifactSignature | None]:
        """按调用方声明的路径原文为 key 记录 (mtime_ns, size)；不存在记 None。"""
        out: dict[str, ArtifactSignature | None] = {}
        for raw in paths:
            try:
                stat = self.resolve(raw).stat()
            except OSError:
                out[raw] = None
                continue
            out[raw] = (stat.st_mtime_ns, stat.st_size)
        return out

    def scan(self, scope: Scope) -> dict[str, ArtifactSignature]:
        """枚举 scope 内现存文件的签名，key 为相对 scope.root 的 posix 路径。"""
        root = Path(scope.root)
        prefixes = scope.prefixes or ("",)
        out: dict[str, ArtifactSignature] = {}
        for prefix in prefixes:
            start = root / prefix if prefix else root
            if not start.is_dir():
                continue
            for item in start.rglob("*"):
                if not item.is_file():
                    continue
                try:
                    stat = item.stat()
                except OSError:
                    continue
                rel = item.relative_to(root).as_posix()
                out[rel] = (stat.st_mtime_ns, stat.st_size)
        return out

    def inspect(self, path: str) -> ArtifactFacts:
        target = self.resolve(path)
        try:
            stat = target.stat()
        except OSError:
            return ArtifactFacts(exists=False, size=0)
        return ArtifactFacts(exists=True, size=stat.st_size)
