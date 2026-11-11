"""Skill editor — write-back editing of SKILL.md with whole-directory backups.

Public surface (consumed by the /skills web router and tests):

* :class:`SkillEditor` — resolves a skill manifest, applies origin + editable
  guards, snapshots the whole skill directory, writes the edited frontmatter /
  body back atomically, re-validates by re-parsing (rolling back on failure),
  and fires an injected cache-invalidation callback.
* :class:`BackupManager` — timestamped full-directory snapshots under
  ``<skill_dir>/.backups/`` plus a ``manifest.json`` version index, with LRU
  pruning and exact-restore (clean replace) semantics.

Design invariants (verified against the real scanner):
* ``.backups`` starts with ``.`` → skipped wholesale by
  ``schema.shared.discovery._is_valid_entry_name``; it can never be discovered
  as a skill, so no discovery change is required.
* Write-back round-trips the *original* frontmatter dict (via
  ``_extract_frontmatter`` + ``yaml.safe_load``) so unknown keys and runtime
  auto-written keys (e.g. ``name_zh``) survive untouched; only the fields the
  caller edits are merged in.
* Editing is opt-in and fail-closed: a skill is editable only when its
  ``SkillManifest.editable`` is True (frontmatter ``editable: true``).
* ``name`` cannot be changed via edit (would re-key the catalog).
* Writes are confined to the *managed capability roots*: the cloud workspace
  root plus the configured capability directories under both the workspace
  root and the project root (``asset_skills_dir`` / ``asset_plugins_dir``)
  — the same set the runtime skill discovery scans. A manifest pointing
  anywhere else is rejected, so a poisoned catalog entry cannot make the
  editor write outside managed directories.
* Origin policy mirrors the delete guard for *system-provided* origins only
  (bundled / workspace / builtin stay read-only). Plugin- and marketplace-
  provided skills are editable **only** when they opt in via
  ``editable: true``; the fail-closed default still locks every other skill.
  Caveat (surfaced to users): a plugin update/reinstall can overwrite an
  edited SKILL.md and silently drop both the edits and the opt-in flag.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from prismclaw.schema.agent.domains import SkillSource
from prismclaw.schema.shared import _extract_frontmatter

logger = logging.getLogger(__name__)

BACKUPS_DIRNAME = ".backups"
BACKUP_MANIFEST = "manifest.json"
BACKUP_KEEP_LIMIT = 20

# Origins that are system-provided and must never be hand-edited, mirroring the
# delete_skill guard in the web router (bundled/workspace/builtin). PLUGIN and
# MARKETPLACE are deliberately NOT here: external-content skills become
# hand-editable only through the explicit ``editable: true`` opt-in — the
# fail-closed default still locks every skill that does not opt in.
_READONLY_ORIGINS: frozenset[SkillSource] = frozenset(
    {
        SkillSource.BUNDLED,
        SkillSource.WORKSPACE,
        SkillSource.BUILTIN,
    }
)


def _relative_capability_dirs(settings: Any) -> list[str]:
    """Relative capability directory names from settings (skills + plugins).

    Tolerates partial settings objects (tests pass ``SimpleNamespace`` with
    ``plugins=None``); missing sections simply contribute no roots.
    """
    dirs: list[str] = []
    for section, field in (
        ("skills", "asset_skills_dir"),
        ("plugins", "asset_plugins_dir"),
    ):
        obj = getattr(settings, section, None)
        raw = getattr(obj, field, None) if obj is not None else None
        if isinstance(raw, str) and raw.strip():
            dirs.append(raw.strip())
    return dirs


# ---------------------------------------------------------------------------
# Errors (carry an HTTP status_code for the router to map directly)
# ---------------------------------------------------------------------------


class SkillEditError(Exception):
    """Base error for the skill editor; ``status_code`` maps to HTTP."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class EditForbidden(SkillEditError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=403)


class SkillNotFound(SkillEditError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=404)


class EditFailed(SkillEditError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=500)


# ---------------------------------------------------------------------------
# Result / record types
# ---------------------------------------------------------------------------


@dataclass
class BackupEntry:
    id: str
    ts: int
    reason: str
    files: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EditResult:
    ok: bool
    name: str
    backup_id: str
    description: str


@dataclass
class RestoreResult:
    ok: bool
    name: str
    backup_id: str
    pre_restore_backup_id: str


# ---------------------------------------------------------------------------
# Backup manager — whole-directory snapshots
# ---------------------------------------------------------------------------


class BackupManager:
    def __init__(self, skill_dir: Path) -> None:
        self.skill_dir = skill_dir
        self.backups_dir = skill_dir / BACKUPS_DIRNAME
        self.manifest_path = self.backups_dir / BACKUP_MANIFEST

    def snapshot(self, *, reason: str = "edit") -> BackupEntry:
        ts_ms = int(time.time() * 1000)
        bid = self._make_id(ts_ms)
        dest = self.backups_dir / bid
        while dest.exists():
            ts_ms += 1
            bid = self._make_id(ts_ms)
            dest = self.backups_dir / bid
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        # Copy everything EXCEPT the backups dir itself (avoid nesting).
        # A mid-copy failure (disk full / permission) must not leave a
        # partial snapshot dir behind: it has no manifest entry, so it
        # would never be pruned and would never be restorable.
        try:
            shutil.copytree(
                self.skill_dir,
                dest,
                ignore=shutil.ignore_patterns(BACKUPS_DIRNAME),
            )
            file_count = sum(1 for p in dest.rglob("*") if p.is_file())
        except Exception:
            shutil.rmtree(dest, ignore_errors=True)
            raise
        entry = BackupEntry(id=bid, ts=ts_ms, reason=reason, files=file_count)
        entries = self._read_manifest()
        entries.append(entry)
        self._write_manifest(entries)
        self._prune()
        return entry

    def list(self) -> list[BackupEntry]:
        return sorted(self._read_manifest(), key=lambda e: e.ts, reverse=True)

    def restore_into(self, backup_id: str) -> None:
        src = self.backups_dir / backup_id
        if not src.is_dir():
            raise SkillNotFound(f"backup {backup_id!r} not found")
        # Clean replace: drop current contents (except .backups) then copy the
        # snapshot in, so the directory matches the backup exactly.
        for child in self.skill_dir.iterdir():
            if child.name == BACKUPS_DIRNAME:
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        for child in src.iterdir():
            dest = self.skill_dir / child.name
            if child.is_dir():
                shutil.copytree(child, dest)
            else:
                shutil.copy2(child, dest)

    @staticmethod
    def _make_id(ts_ms: int) -> str:
        stamp = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).strftime(
            "%Y%m%dT%H%M%S"
        )
        return f"{stamp}_{ts_ms % 1000:03d}"

    def _read_manifest(self) -> list[BackupEntry]:
        if not self.manifest_path.is_file():
            return []
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        raw = data.get("backups") if isinstance(data, dict) else None
        out: list[BackupEntry] = []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    out.append(
                        BackupEntry(
                            id=item["id"],
                            ts=int(item.get("ts", 0) or 0),
                            reason=str(item.get("reason", "")),
                            files=int(item.get("files", 0) or 0),
                        )
                    )
        return out

    def _write_manifest(self, entries: list[BackupEntry]) -> None:
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "backups": [asdict(e) for e in entries]}
        self.manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _prune(self) -> None:
        entries = self._read_manifest()
        if len(entries) <= BACKUP_KEEP_LIMIT:
            return
        entries.sort(key=lambda e: e.ts)  # oldest first
        drop = entries[: len(entries) - BACKUP_KEEP_LIMIT]
        keep = entries[len(entries) - BACKUP_KEEP_LIMIT :]
        for e in drop:
            shutil.rmtree(self.backups_dir / e.id, ignore_errors=True)
        self._write_manifest(keep)


# ---------------------------------------------------------------------------
# Editor
# ---------------------------------------------------------------------------


def _origin_label(origin: Any) -> str:
    value = getattr(origin, "value", origin)
    return str(value) if value is not None else "unknown"


class SkillEditor:
    """Edit a single skill's SKILL.md with guards, backup and atomic write-back.

    Dependencies are injected as callables (mirrors the ``web_deps`` DI style):
    ``parse_fn`` validates the rewritten file, ``classify_origin_fn`` decides the
    read-only guard, ``invalidate_fn`` refreshes the runtime skill catalog.
    """

    def __init__(
        self,
        workspace_root: Path | str,
        settings: Any,
        *,
        parse_fn: Callable[[Path], Any],
        classify_origin_fn: Callable[..., Any],
        extension_manager: Any | None = None,
        invalidate_fn: Callable[[], None] | None = None,
        project_root: Path | str | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.settings = settings
        self.project_root = Path(project_root) if project_root is not None else None
        self._parse = parse_fn
        self._classify_origin = classify_origin_fn
        self._extension_manager = extension_manager
        self._invalidate = invalidate_fn
        self._allowed_roots = self._compute_allowed_roots()

    def _compute_allowed_roots(self) -> tuple[Path, ...]:
        """Directories the editor may write into.

        The cloud workspace root itself (which covers every workspace-scoped
        location, including the installed-skill root ``<workspace>/.work/skills``),
        plus the configured user-level capability directories
        (``asset_skills_dir`` / ``asset_plugins_dir``). This mirrors what the
        runtime skill discovery scans, so any manifest the capability catalog
        can produce is coverable.
        """
        roots: list[Path] = []

        def _add(candidate: Path) -> None:
            try:
                resolved = candidate.expanduser().resolve()
            except Exception:  # noqa: BLE001 - unresolvable root is skipped
                return
            if resolved not in roots:
                roots.append(resolved)

        _add(self.workspace_root)
        bases = [self.workspace_root]
        if self.project_root is not None:
            bases.append(self.project_root)
        for base in bases:
            for rel in _relative_capability_dirs(self.settings):
                expanded = Path(rel).expanduser()
                _add(expanded if expanded.is_absolute() else base / expanded)
        return tuple(roots)

    # ---- path resolution / guards -------------------------------------

    def _skill_dir(self, manifest: Any) -> Path:
        raw = getattr(manifest, "file_path", "") or ""
        if not raw:
            raise SkillNotFound("skill has no file path")
        try:
            mp = Path(raw).expanduser().resolve()
        except Exception as exc:  # noqa: BLE001 - surface as 404
            raise SkillNotFound(f"invalid skill path: {exc}") from exc
        if not mp.is_file():
            raise SkillNotFound(f"SKILL.md not found: {mp}")
        skill_dir = mp.parent
        if not any(
            skill_dir == root or root in skill_dir.parents
            for root in self._allowed_roots
        ):
            allowed = ", ".join(str(r) for r in self._allowed_roots)
            raise EditForbidden(
                f"skill is outside the managed skill roots (allowed: {allowed})"
            )
        return skill_dir

    def _guard(self, manifest: Any) -> Path:
        if not bool(getattr(manifest, "editable", False)):
            raise EditForbidden(
                f"skill {getattr(manifest, 'name', '?')!r} is not editable — "
                "set 'editable: true' in SKILL.md"
            )
        skill_dir = self._skill_dir(manifest)
        origin = self._classify_origin(
            manifest,
            self.workspace_root,
            self.settings,
            extension_manager=self._extension_manager,
        )
        if isinstance(origin, SkillSource) and origin in _READONLY_ORIGINS:
            raise EditForbidden(f"{_origin_label(origin)} skill is read-only")
        return skill_dir

    # ---- operations ----------------------------------------------------

    def edit(
        self,
        manifest: Any,
        *,
        description: str | None = None,
        body: str | None = None,
        fields: dict[str, Any] | None = None,
    ) -> EditResult:
        edit_fields = dict(fields or {})
        if description is None and body is None and not edit_fields:
            raise SkillEditError("nothing to edit")
        if "name" in edit_fields:
            raise SkillEditError("cannot change skill name via edit")

        skill_dir = self._guard(manifest)
        skill_file = skill_dir / "SKILL.md"
        backups = BackupManager(skill_dir)
        try:
            backup = backups.snapshot(reason="edit")
        except Exception as exc:  # noqa: BLE001 - unwrap OSError into 500
            raise EditFailed(
                f"backup failed, edit aborted before any write: {exc}"
            ) from exc
        try:
            self._write_back(
                skill_file, description=description, body=body, fields=edit_fields
            )
            self._parse(skill_file)
        except SkillEditError:
            backups.restore_into(backup.id)
            raise
        except Exception as exc:  # noqa: BLE001 - any failure → rollback + 500
            backups.restore_into(backup.id)
            raise EditFailed(
                f"edit failed, rolled back to {backup.id}: {exc}"
            ) from exc

        self._invalidate_safe()
        new_desc = (
            description
            if description is not None
            else (getattr(manifest, "description", "") or "")
        )
        return EditResult(
            ok=True, name=manifest.name, backup_id=backup.id, description=new_desc
        )

    def backups(self, manifest: Any) -> list[BackupEntry]:
        # Read-only listing: no editable/origin guard, just resolve the dir.
        skill_dir = self._skill_dir(manifest)
        return BackupManager(skill_dir).list()

    def restore(self, manifest: Any, backup_id: str) -> RestoreResult:
        skill_dir = self._guard(manifest)
        backups = BackupManager(skill_dir)
        try:
            pre = backups.snapshot(reason="pre-restore")
        except Exception as exc:  # noqa: BLE001 - unwrap OSError into 500
            raise EditFailed(
                f"backup failed, restore aborted before any write: {exc}"
            ) from exc
        try:
            backups.restore_into(backup_id)
            self._parse(skill_dir / "SKILL.md")
        except SkillEditError:
            backups.restore_into(pre.id)
            raise
        except Exception as exc:  # noqa: BLE001 - any failure → rollback + 500
            backups.restore_into(pre.id)
            raise EditFailed(
                f"restore failed, rolled back to {pre.id}: {exc}"
            ) from exc

        self._invalidate_safe()
        return RestoreResult(
            ok=True,
            name=manifest.name,
            backup_id=backup_id,
            pre_restore_backup_id=pre.id,
        )

    # ---- write-back ----------------------------------------------------

    def _write_back(
        self,
        skill_file: Path,
        *,
        description: str | None,
        body: str | None,
        fields: dict[str, Any],
    ) -> None:
        content = skill_file.read_text(encoding="utf-8")
        fm_str, old_body = _extract_frontmatter(content)
        data = yaml.safe_load(fm_str)
        if not isinstance(data, dict):
            raise SkillEditError("frontmatter is not a mapping")

        if description is not None:
            data["description"] = description
        for key, value in fields.items():
            data[key] = value
        new_body = old_body if body is None else body

        rendered = self._render(data, new_body)
        tmp = skill_file.with_name(skill_file.name + ".tmp")
        tmp.write_text(rendered, encoding="utf-8")
        os.replace(tmp, skill_file)

    @staticmethod
    def _render(data: dict[str, Any], body: str) -> str:
        fm = yaml.safe_dump(
            data, allow_unicode=True, sort_keys=False, default_flow_style=False
        ).strip()
        if body and not body.endswith("\n"):
            body = body + "\n"
        return f"---\n{fm}\n---\n{body}"

    def _invalidate_safe(self) -> None:
        if self._invalidate is None:
            return
        try:
            self._invalidate()
        except Exception:  # noqa: BLE001 - invalidation must never break edit
            logger.warning("skill editor: cache invalidation failed", exc_info=True)


__all__ = [
    "SkillEditor",
    "BackupManager",
    "BackupEntry",
    "EditResult",
    "RestoreResult",
    "SkillEditError",
    "EditForbidden",
    "SkillNotFound",
    "EditFailed",
    "BACKUPS_DIRNAME",
    "BACKUP_KEEP_LIMIT",
]
