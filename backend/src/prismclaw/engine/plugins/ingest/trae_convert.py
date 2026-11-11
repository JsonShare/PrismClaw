"""``.trae-plugin`` → canonical ``plugin.json`` converter (SDD P2-T2).

Translates a Trae-dialect plugin manifest into PrismClaw's canonical
(``.plugin/plugin.json``) form. Only fields PrismClaw consumes are
mapped; the Trae-specific ``interface`` block (marketplace display metadata:
displayName, brandColor, screenshots, …) is not consumed by the loader and is
preserved under ``_metadata`` for provenance rather than silently dropped.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = ["convert_trae_manifest", "write_canonical_manifest"]


def convert_trae_manifest(trae_manifest: dict[str, Any]) -> dict[str, Any]:
    """Convert a parsed Trae ``plugin.json`` into canonical form.

    Maps ``name`` / ``version`` / ``description`` / ``author`` / ``homepage`` /
    ``repository`` / ``license`` / ``keywords`` and normalizes ``skills`` to the
    canonical ``./skills/`` convention. The ``interface`` block is retained under
    ``_metadata.interface``; the PrismClaw-native ``porting`` declaration (remap
    rules) is carried through verbatim; other unrecognized top-level keys are
    dropped.
    """
    canonical: dict[str, Any] = {}

    name = trae_manifest.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("trae plugin.json missing required 'name'")
    canonical["name"] = name.strip()

    for key in ("version", "description", "homepage", "repository", "license"):
        value = trae_manifest.get(key)
        if isinstance(value, str) and value:
            canonical[key] = value

    author = trae_manifest.get("author")
    if isinstance(author, dict):
        author_name = author.get("name")
        if isinstance(author_name, str) and author_name:
            canonical["author"] = author_name
    elif isinstance(author, str) and author:
        canonical["author"] = author

    keywords = trae_manifest.get("keywords")
    if isinstance(keywords, list):
        cleaned = [k for k in keywords if isinstance(k, str) and k]
        if cleaned:
            canonical["keywords"] = cleaned

    # Skills: PrismClaw's declarative loader scans the conventional ``skills/``
    # directory by default, so an explicit field is only needed when the Trae
    # manifest points elsewhere. Normalize a ``./skills/``-style value away
    # (strip the relative ``./`` prefix and any trailing slash).
    skills = trae_manifest.get("skills")
    if isinstance(skills, str) and skills.strip():
        normalized = skills.strip().strip("./").strip("/")
        if normalized and normalized != "skills":
            canonical["skills"] = normalized

    interface = trae_manifest.get("interface")
    if isinstance(interface, dict) and interface:
        canonical["_metadata"] = {"interface": interface}

    # ``porting`` 是 PrismClaw 原生声明（路径重映射规则），与方言无关、本就为
    # canonical 形态，原样透传——方言插件同样可以自描述合规规则，加载器一视同仁。
    porting = trae_manifest.get("porting")
    if isinstance(porting, dict) and porting:
        canonical["porting"] = porting

    return canonical


def write_canonical_manifest(
    trae_manifest: dict[str, Any], target_dir: Path
) -> Path:
    """Convert ``trae_manifest`` and write it to ``target_dir/.plugin/plugin.json``.

    Creates the ``.plugin`` directory. Returns the written manifest path.
    """
    canonical = convert_trae_manifest(trae_manifest)
    plugin_dir_plugin = Path(target_dir) / ".plugin"
    plugin_dir_plugin.mkdir(parents=True, exist_ok=True)
    out_path = plugin_dir_plugin / "plugin.json"
    out_path.write_text(
        json.dumps(canonical, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out_path
