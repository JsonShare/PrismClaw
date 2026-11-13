"""Shared discovery helpers used by both skill discovery and sub-agent discovery.

Extracted in change ``add-subagent-system`` (tasks.md §0) so the same scan +
frontmatter parsing logic powers both:

- ``backend/src/prismclaw/bootstrap/skills/discovery.py`` (skills, looking for ``SKILL.md`` per dir)
- ``backend/src/prismclaw/core/agent/subagent/registry.py`` (sub-agents, parsing ``<name>.md``
  files at the directory's top level — different layout, same primitives)

Behavior of the original module-private helpers is preserved verbatim; only the
shape is generalized:

- ``_extract_frontmatter`` is moved verbatim from ``skills/parser.py`` (still
  raises ``SkillParseError`` for backward compat with skill tests).
- ``_scan_directory`` is generalized from a ``list[SkillManifest]`` return into
  a generic ``list[T]`` with a caller-supplied ``parse`` callable, and the
  hard-coded ``SKILL.md`` filename becomes a ``manifest_filename`` parameter
  (still defaulting to ``SKILL.md`` so the skills call site does not change
  semantics). Sub-agent discovery uses a different layout (manifest at the
  top level, name from filename) and is therefore expected to use a thin
  wrapper rather than calling ``_scan_directory`` directly.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

_BLOCKED_NAMES = frozenset({
    "node_modules", ".git", "venv", "__pycache__", ".venv", "env",
    "uploads", "outputs", "tmp", ".envs", ".work",
    "skills", "sandbox",
})

# 目录扫描深度上限（单一来源）。PrismClaw 以 SKILL.md 叶子终止为发现主机制，
# 深度上限只是防止病态树的安全网。原值 3 会漏掉深层嵌套插件
# （如 marketplace/<plugin>/skills/<group>/<skill>/SKILL.md），放宽到 8。
# node_modules/.git 等仍由 _BLOCKED_NAMES 屏蔽，放宽深度不会扫入依赖/版本目录。
SKILL_SCAN_MAX_DEPTH = 8

T = TypeVar("T")


def _is_valid_entry_name(entry_name: str) -> bool:
    if entry_name.startswith(".") and entry_name not in {".claude"}:
        return False
    return entry_name not in _BLOCKED_NAMES


def _validate_path_containment(child_path: Path, root: Path) -> bool:
    try:
        resolved = child_path.resolve()
        root_resolved = root.resolve()
        return resolved.is_relative_to(root_resolved)
    except (OSError, ValueError):
        return False


def _extract_frontmatter(content: str) -> tuple[str, str]:
    # Raises SkillParseError (not ValueError) for backward compat with skill
    # callers that catch the typed exception. Lazy import avoids a circular
    # dependency: prismclaw.skills.__init__ imports prismclaw.skills.discovery, which
    # in turn imports from this helpers module — pulling SkillParseError at
    # module-init time creates a cycle when this module is loaded by
    # prismclaw.engine.subagent.parser before prismclaw.skills is initialized.
    from prismclaw.schema.shared.exceptions import SkillParseError

    if not content.startswith("---"):
        raise SkillParseError("No YAML frontmatter found (file must start with ---)")

    end_index = content.find("---", 3)
    if end_index == -1:
        raise SkillParseError("No closing --- for YAML frontmatter")

    frontmatter = content[3:end_index].strip()
    body = content[end_index + 3 :].strip()
    return frontmatter, body


def _scan_directory_v2(
    root: Path,
    parse: Callable[[Path, str], T],
    *,
    manifest_filename: str = "SKILL.md",
    max_file_bytes: int = 256_000,
    max_candidates: int = 500,
    max_loaded: int = 100,
    max_depth: int = SKILL_SCAN_MAX_DEPTH,
) -> list[T]:
    """Scan subdirectories of ``root`` recursively up to ``max_depth``.
    
    If it finds ``manifest_filename``, it stops descending into that directory,
    parses it, and returns it. The ``parse`` callable receives `(path, namespace)`.
    """
    results: list[T] = []
    scanned_count = 0
    
    def _walk(current_dir: Path, current_depth: int, current_ns: list[str]) -> None:
        nonlocal scanned_count
        if current_depth > max_depth or len(results) >= max_loaded:
            return
            
        try:
            entries = sorted(list(current_dir.iterdir()), key=lambda x: x.name)
        except OSError:
            logger.warning("Cannot list directory: %s", current_dir)
            return

        for path in entries:
            if not _is_valid_entry_name(path.name):
                continue
                
            scanned_count += 1
            if scanned_count > max_candidates:
                return

            if path.is_dir():
                # Check if it contains the manifest
                manifest_path = path / manifest_filename
                if manifest_path.exists():
                    try:
                        size = manifest_path.stat().st_size
                    except OSError:
                        continue
                        
                    if size > max_file_bytes:
                        continue
                        
                    if not _validate_path_containment(manifest_path, root):
                        continue
                        
                    ns = ".".join(current_ns + [path.name])
                    
                    from prismclaw.schema.shared.exceptions import SkillParseError
                    try:
                        manifest = parse(manifest_path, ns)
                    except SkillParseError as exc:
                        logger.warning("Failed to parse %s: %s", manifest_path, exc)
                        continue
                    
                    results.append(manifest)
                    if len(results) >= max_loaded:
                        return
                else:
                    # Recurse
                    _walk(path, current_depth + 1, current_ns + [path.name])

    _walk(root, 1, [])
    return results
