"""Skill editor unit tests — guards, whole-dir backup, round-trip write-back,
rollback, restore, and scanner-safety of the .backups directory.

Uses the real parser (parse_skill_file) and real origin classifier
(classify_skill_origin) against a throwaway tmp workspace, so the parser
``editable`` normalization and the discovery skip of ``.backups`` are exercised
end-to-end rather than mocked.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from prismclaw.runtime.skills import editor as editor_mod
from prismclaw.runtime.skills.discovery import discover_skills
from prismclaw.runtime.skills.editor import (
    EditFailed,
    EditForbidden,
    SkillEditError,
    SkillEditor,
    SkillNotFound,
)
from prismclaw.runtime.skills.parser import parse_skill_file
from prismclaw.runtime.skills.origin import classify_skill_origin
from prismclaw.schema.agent.domains import SkillSource
from prismclaw.schema.shared.exceptions import SkillParseError


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


def _settings() -> SimpleNamespace:
    # classify_skill_origin reads settings.skills.asset_skills_dir and
    # settings.plugins (None → no plugin roots).
    return SimpleNamespace(
        skills=SimpleNamespace(asset_skills_dir=".work/skills"),
        plugins=None,
    )


def _write_skill(root: Path, name: str, *, editable: bool | None, extra_fm: str = "") -> Path:
    skill_dir = root / ".work" / "skills" / name
    (skill_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (skill_dir / "scripts" / "run.py").write_text("print('hi')\n", encoding="utf-8")
    editable_line = "" if editable is None else f"editable: {str(editable).lower()}\n"
    content = (
        "---\n"
        "name: " + name + "\n"
        "description: original description\n"
        "name_zh: 演示\n"
        "custom_unknown_key: keep-me\n"
        + editable_line
        + extra_fm
        + "---\n"
        "\n"
        "# Demo skill\n"
        "\n"
        "Original body line.\n"
    )
    md = skill_dir / "SKILL.md"
    md.write_text(content, encoding="utf-8")
    return md


def _make_editor(
    root: Path,
    *,
    parse_fn=parse_skill_file,
    classify=classify_skill_origin,
    settings=None,
    project_root=None,
):
    calls: list[str] = []

    def _invalidate() -> None:
        calls.append("invalidate")

    ed = SkillEditor(
        root,
        settings if settings is not None else _settings(),
        parse_fn=parse_fn,
        classify_origin_fn=classify,
        invalidate_fn=_invalidate,
        project_root=project_root,
    )
    return ed, calls


# ---------------------------------------------------------------------------
# editable flag semantics (default = disabled)
# ---------------------------------------------------------------------------


def test_default_is_not_editable(tmp_path):
    md = _write_skill(tmp_path, "demo", editable=None)
    manifest = parse_skill_file(md)
    assert manifest.editable is False
    ed, _ = _make_editor(tmp_path)
    with pytest.raises(EditForbidden) as ei:
        ed.edit(manifest, description="new")
    assert ei.value.status_code == 403
    # file untouched
    assert "original description" in md.read_text(encoding="utf-8")


def test_editable_false_is_not_editable(tmp_path):
    md = _write_skill(tmp_path, "demo", editable=False)
    manifest = parse_skill_file(md)
    assert manifest.editable is False
    ed, _ = _make_editor(tmp_path)
    with pytest.raises(EditForbidden):
        ed.edit(manifest, description="new")


def test_editable_true_allows_edit(tmp_path):
    md = _write_skill(tmp_path, "demo", editable=True)
    manifest = parse_skill_file(md)
    assert manifest.editable is True
    ed, calls = _make_editor(tmp_path)
    result = ed.edit(manifest, description="updated description")
    assert result.ok and result.name == "demo"
    text = md.read_text(encoding="utf-8")
    assert "updated description" in text
    assert "original description" not in text
    # unknown + auto-written frontmatter keys survive the round-trip
    assert "custom_unknown_key: keep-me" in text
    assert "name_zh: 演示" in text
    # cache invalidation fired
    assert calls == ["invalidate"]
    # a backup was created
    backups_dir = md.parent / ".backups"
    assert backups_dir.is_dir()
    assert (backups_dir / "manifest.json").is_file()
    listed = ed.backups(manifest)
    assert len(listed) == 1 and listed[0].reason == "edit"


def test_edit_body_and_fields(tmp_path):
    md = _write_skill(tmp_path, "demo", editable=True)
    manifest = parse_skill_file(md)
    ed, _ = _make_editor(tmp_path)
    ed.edit(manifest, body="Rewritten body.\nSecond line.\n", fields={"category": "demo-cat"})
    text = md.read_text(encoding="utf-8")
    assert "Rewritten body." in text
    assert "Original body line." not in text
    assert "category: demo-cat" in text
    # reparse succeeds and reflects new values
    reparsed = parse_skill_file(md)
    assert reparsed.body.startswith("Rewritten body.")
    assert reparsed.category == "demo-cat"


def test_name_change_rejected(tmp_path):
    md = _write_skill(tmp_path, "demo", editable=True)
    manifest = parse_skill_file(md)
    ed, _ = _make_editor(tmp_path)
    with pytest.raises(SkillEditError) as ei:
        ed.edit(manifest, fields={"name": "renamed"})
    assert ei.value.status_code == 400
    assert "name: demo" in md.read_text(encoding="utf-8")


def test_nothing_to_edit_rejected(tmp_path):
    md = _write_skill(tmp_path, "demo", editable=True)
    manifest = parse_skill_file(md)
    ed, _ = _make_editor(tmp_path)
    with pytest.raises(SkillEditError):
        ed.edit(manifest)


# ---------------------------------------------------------------------------
# origin / path guards
# ---------------------------------------------------------------------------


def test_readonly_origin_rejected(tmp_path):
    md = _write_skill(tmp_path, "demo", editable=True)
    manifest = parse_skill_file(md)
    ed, _ = _make_editor(tmp_path, classify=lambda *a, **k: SkillSource.BUNDLED)
    with pytest.raises(EditForbidden) as ei:
        ed.edit(manifest, description="x")
    assert ei.value.status_code == 403


def test_outside_workspace_rejected(tmp_path):
    outside = tmp_path / "outside"
    md = _write_skill(outside, "demo", editable=True)
    manifest = parse_skill_file(md)
    # editor rooted at a *different* dir → containment guard trips
    ed, _ = _make_editor(tmp_path / "elsewhere")
    with pytest.raises(EditForbidden):
        ed.edit(manifest, description="x")


def test_missing_file_raises_not_found(tmp_path):
    md = _write_skill(tmp_path, "demo", editable=True)
    manifest = parse_skill_file(md)
    md.unlink()
    ed, _ = _make_editor(tmp_path)
    with pytest.raises(SkillNotFound):
        ed.edit(manifest, description="x")


# ---------------------------------------------------------------------------
# rollback on write/validate failure
# ---------------------------------------------------------------------------


def test_rollback_on_parse_failure(tmp_path):
    md = _write_skill(tmp_path, "demo", editable=True)
    manifest = parse_skill_file(md)
    original = md.read_text(encoding="utf-8")

    def _boom(_path):
        raise SkillParseError("synthetic parse failure")

    ed, _ = _make_editor(tmp_path, parse_fn=_boom)
    with pytest.raises(EditFailed) as ei:
        ed.edit(manifest, description="should be rolled back")
    assert ei.value.status_code == 500
    # content restored to the pre-edit snapshot
    assert md.read_text(encoding="utf-8") == original


def test_backup_failure_aborts_edit_before_write(tmp_path, monkeypatch):
    # snapshot fails (read-only dir / disk full) → clean EditFailed(500),
    # the SKILL.md is never touched, no invalidation fires
    md = _write_skill(tmp_path, "demo", editable=True)
    manifest = parse_skill_file(md)
    original = md.read_text(encoding="utf-8")

    def _boom(*a, **k):
        raise OSError("synthetic disk full")

    monkeypatch.setattr(editor_mod.shutil, "copytree", _boom)
    ed, calls = _make_editor(tmp_path)
    with pytest.raises(EditFailed) as ei:
        ed.edit(manifest, description="x")
    assert ei.value.status_code == 500
    assert md.read_text(encoding="utf-8") == original
    assert calls == []
    # mkdir of .backups may exist, but no manifest was ever written
    assert not (md.parent / ".backups" / "manifest.json").exists()


def test_snapshot_cleans_partial_dest_on_failure(tmp_path, monkeypatch):
    # a copytree that dies mid-way must not leave an unmanaged orphan dir
    # (no manifest entry → never pruned, never restorable)
    md = _write_skill(tmp_path, "demo", editable=True)
    manifest = parse_skill_file(md)

    def _partial(src, dst, **k):
        Path(dst).mkdir(parents=True)
        (Path(dst) / "SKILL.md").write_text("partial", encoding="utf-8")
        raise OSError("synthetic disk full mid-copy")

    monkeypatch.setattr(editor_mod.shutil, "copytree", _partial)
    ed, _ = _make_editor(tmp_path)
    with pytest.raises(EditFailed):
        ed.edit(manifest, description="x")
    backups_dir = md.parent / ".backups"
    leftovers = (
        [p for p in backups_dir.iterdir() if p.is_dir()] if backups_dir.exists() else []
    )
    assert leftovers == []


# ---------------------------------------------------------------------------
# backup: whole-dir snapshot + restore + prune
# ---------------------------------------------------------------------------


def test_backup_snapshots_whole_dir(tmp_path):
    md = _write_skill(tmp_path, "demo", editable=True)
    manifest = parse_skill_file(md)
    ed, _ = _make_editor(tmp_path)
    result = ed.edit(manifest, description="v2")
    bid = result.backup_id
    snap = md.parent / ".backups" / bid
    assert (snap / "SKILL.md").is_file()
    assert (snap / "scripts" / "run.py").is_file()  # subdirectory included
    # the snapshot must not itself contain a nested .backups
    assert not (snap / ".backups").exists()
    entry = ed.backups(manifest)[0]
    assert entry.files >= 2  # SKILL.md + scripts/run.py


def test_restore_reverts(tmp_path):
    md = _write_skill(tmp_path, "demo", editable=True)
    manifest = parse_skill_file(md)
    ed, _ = _make_editor(tmp_path)
    ed.edit(manifest, description="v2")            # this backup captures "original"
    second = ed.edit(manifest, description="v3")   # this backup captures "v2"
    assert "v3" in md.read_text(encoding="utf-8")
    # each edit's backup is the PRE-edit state → restore the 2nd backup to reach v2
    res = ed.restore(manifest, second.backup_id)
    assert res.ok and res.backup_id == second.backup_id
    assert res.pre_restore_backup_id  # a pre-restore snapshot was taken
    text = md.read_text(encoding="utf-8")
    assert "v2" in text and "v3" not in text
    # restored content reparses cleanly
    assert parse_skill_file(md).description == "v2"


def test_restore_unknown_backup_raises(tmp_path):
    md = _write_skill(tmp_path, "demo", editable=True)
    manifest = parse_skill_file(md)
    ed, _ = _make_editor(tmp_path)
    with pytest.raises(SkillNotFound):
        ed.restore(manifest, "does-not-exist")


def test_backup_pruning(tmp_path, monkeypatch):
    monkeypatch.setattr(editor_mod, "BACKUP_KEEP_LIMIT", 3)
    md = _write_skill(tmp_path, "demo", editable=True)
    manifest = parse_skill_file(md)
    ed, _ = _make_editor(tmp_path)
    for i in range(5):
        ed.edit(manifest, description=f"v{i}")
    backups_dir = md.parent / ".backups"
    dirs = [d for d in backups_dir.iterdir() if d.is_dir()]
    assert len(dirs) == 3  # pruned to the limit
    assert len(ed.backups(manifest)) == 3


# ---------------------------------------------------------------------------
# scanner safety: .backups must never be discovered as a skill
# ---------------------------------------------------------------------------


def test_backups_dir_not_discovered(tmp_path):
    md = _write_skill(tmp_path, "demo", editable=True)
    manifest = parse_skill_file(md)
    ed, _ = _make_editor(tmp_path)
    ed.edit(manifest, description="v2")
    ed.edit(manifest, description="v3")
    skills_root = tmp_path / ".work" / "skills"
    found = discover_skills(project_locations=[skills_root])
    names = [m.name for m in found]
    assert names == ["demo"]  # no backup entries leak into the catalog


# ---------------------------------------------------------------------------
# managed roots: containment must cover the capability layout (project root),
# not just the per-user cloud workspace root
# ---------------------------------------------------------------------------


def _full_settings() -> SimpleNamespace:
    return SimpleNamespace(
        skills=SimpleNamespace(asset_skills_dir=".work/skills"),
        plugins=SimpleNamespace(asset_plugins_dir=".work/plugins"),
    )


def _write_skill_at(skill_dir: Path, name: str, *, editable: bool | None) -> Path:
    skill_dir.mkdir(parents=True, exist_ok=True)
    editable_line = "" if editable is None else f"editable: {str(editable).lower()}\n"
    content = (
        "---\n"
        "name: " + name + "\n"
        "description: original description\n"
        + editable_line
        + "---\n"
        "\n"
        "# Demo skill\n"
        "\n"
        "Original body line.\n"
    )
    md = skill_dir / "SKILL.md"
    md.write_text(content, encoding="utf-8")
    return md


def test_project_skill_editable_under_project_root(tmp_path):
    # cloud workspace root is elsewhere; the skill lives under the project root
    ws = tmp_path / "cloud_ws"
    ws.mkdir()
    md = _write_skill(tmp_path, "demo", editable=True)
    manifest = parse_skill_file(md)
    ed, calls = _make_editor(ws, settings=_full_settings(), project_root=tmp_path)
    result = ed.edit(manifest, description="v2")
    assert result.ok
    assert "v2" in md.read_text(encoding="utf-8")
    assert calls == ["invalidate"]


def test_plugin_skill_editable_when_opted_in(tmp_path):
    ws = tmp_path / "cloud_ws"
    ws.mkdir()
    skill_dir = tmp_path / ".work" / "plugins" / "demo-plugin" / "skills" / "pdemo"
    md = _write_skill_at(skill_dir, "pdemo", editable=True)
    manifest = parse_skill_file(md)
    settings = _full_settings()
    # the real classifier resolves this as PLUGIN (path under the plugin root)
    assert classify_skill_origin(manifest, tmp_path, settings) is SkillSource.PLUGIN
    ed, _ = _make_editor(ws, settings=settings, project_root=tmp_path)
    result = ed.edit(manifest, description="plugin edit")
    assert result.ok
    assert "plugin edit" in md.read_text(encoding="utf-8")
    # backups live inside the plugin skill dir, not the workspace
    assert (skill_dir / ".backups" / "manifest.json").is_file()


def test_plugin_skill_without_opt_in_still_locked(tmp_path):
    ws = tmp_path / "cloud_ws"
    ws.mkdir()
    skill_dir = tmp_path / ".work" / "plugins" / "demo-plugin" / "skills" / "pdemo"
    md = _write_skill_at(skill_dir, "pdemo", editable=None)
    manifest = parse_skill_file(md)
    ed, _ = _make_editor(ws, settings=_full_settings(), project_root=tmp_path)
    with pytest.raises(EditForbidden) as ei:
        ed.edit(manifest, description="x")
    assert ei.value.status_code == 403
    assert "original description" in md.read_text(encoding="utf-8")


def test_system_origin_blocked_even_when_opted_in(tmp_path):
    # bundled/workspace/builtin stay read-only regardless of the opt-in flag
    md = _write_skill(tmp_path, "demo", editable=True)
    manifest = parse_skill_file(md)
    ed, _ = _make_editor(
        tmp_path,
        settings=_full_settings(),
        project_root=tmp_path,
        classify=lambda *a, **k: SkillSource.BUNDLED,
    )
    with pytest.raises(EditForbidden) as ei:
        ed.edit(manifest, description="x")
    assert ei.value.status_code == 403


def test_outside_all_roots_rejected(tmp_path):
    ws = tmp_path / "cloud_ws"
    proj = tmp_path / "proj"
    ws.mkdir()
    proj.mkdir()
    skill_dir = tmp_path / "elsewhere" / ".prismclaw" / "skills" / "demo"
    md = _write_skill_at(skill_dir, "demo", editable=True)
    manifest = parse_skill_file(md)
    ed, _ = _make_editor(ws, settings=_full_settings(), project_root=proj)
    with pytest.raises(EditForbidden) as ei:
        ed.edit(manifest, description="x")
    assert ei.value.status_code == 403
    assert "outside the managed skill roots" in str(ei.value)
