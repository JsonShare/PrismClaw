from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from prismclaw.engine.subagent.definition.manifest import AgentManifest
from prismclaw.schema.shared import _extract_frontmatter


def parse_agent_md(path: str | Path) -> AgentManifest:
    resolved = Path(path).resolve()

    try:
        content = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read manifest file {resolved}: {exc}") from exc

    frontmatter_str, body = _extract_frontmatter(content)

    try:
        loaded = yaml.safe_load(frontmatter_str)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML frontmatter in {resolved}: {exc}") from exc

    if loaded is None:
        frontmatter: dict[str, Any] = {}
    elif not isinstance(loaded, dict):
        raise ValueError(f"frontmatter is not a YAML mapping in {resolved}")
    else:
        frontmatter = loaded

    manifest_name = frontmatter.get("name")
    expected_name = resolved.stem
    if manifest_name and manifest_name != expected_name:
        raise ValueError(
            f"manifest name {manifest_name!r} does not match filename "
            f"{expected_name!r} (path={resolved})"
        )

    return AgentManifest(**frontmatter, system_prompt=body)
