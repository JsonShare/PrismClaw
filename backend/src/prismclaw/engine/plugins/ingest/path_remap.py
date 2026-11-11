"""Path remapping for ported plugins — a generic, plugin-declared mechanism.

Plugins translated from another ecosystem (e.g. ``legal-chineselaw`` from the
reference implementation) may hard-code that ecosystem's stateful paths in
their prose, such as ``~/.prismclaw/plugins/config/<root>/<area>/...``. Those paths do not
exist under PrismClaw until the user has run their cold-start interview, and
more importantly, the model routinely mis-expands ``~`` (drops the dot,
produces ``/home/user/prismclaw/...`` instead of ``/home/user/.prismclaw/...``).

Two layers of defence:

    1. **Engine default** (always applied, plugin cannot disable): remaps
       ``~/.prismclaw/`` to ``${PRISMCLAW_USER_HOME}/`` before any plugin-
       declared rules. This catches 100% of plugins that prose-write the
       canonical PrismClaw user home without having ever declared a remap —
       which is every plugin in the tree today.

    2. **Plugin-declared rules** (in ``plugin.json`` ``porting.remap``): these
       run *after* the default and can override or augment it. A plugin that
       wants to remap ``~/.prismclaw/plugins/config/foo/`` to its plugin data
       directory declares a rule with a more specific ``from`` prefix — order
       matters, so more specific prefixes must come first.

The loader applies the combined rules immediately before variable substitution,
so a rewritten ``${...}`` placeholder resolves to the plugin's real persistent
locations in the same pass.

Design constraints:
    * Generic: this module knows nothing about any specific plugin. Rules come
      from the plugin's manifest; a plugin with no ``porting.remap`` still
      benefits from the engine default.
    * Surgical: only a matched ``from`` prefix is replaced; the remainder of each
      path and all surrounding text are byte-for-byte preserved.
    * Deterministic: rules apply in declaration order, single pass — a rewritten
      ``${...}`` placeholder is never re-scanned.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "PathRemapRule",
    "DEFAULT_REMAP_RULES",
    "apply_path_remap",
    "parse_remap_rules",
]


@dataclass(frozen=True)
class PathRemapRule:
    """A single prefix rewrite: ``source_prefix`` → ``target_prefix``.

    ``source_prefix`` is matched literally (not as a regex). ``target_prefix``
    is inserted verbatim and is typically a variable placeholder such as
    ``${PRISMCLAW_MARKETPLACE_DATA}/`` that a later substitution pass resolves.
    """

    source_prefix: str
    target_prefix: str


# Engine-level default remap applied to *every* plugin. This is not a plugin
# declaration — the loader prepends it automatically. See module docstring for
# rationale.
#
# T0 迁 assets/ 后，静态能力目录从 home 根裸位置收进 ~/.prismclaw/assets/。
# 追加三条 assets 专用长前缀规则，靠 apply_path_remap 的长度优先排序
# 自动先命中；通配规则覆盖 runtime / memory / workspaces / server.yaml /
# plugins-data 等**仍然留在 home 根**的运行时路径，不需改。
DEFAULT_REMAP_RULES: tuple[PathRemapRule, ...] = (
    PathRemapRule("~/.prismclaw/skills/", "${PRISMCLAW_USER_HOME}/assets/skills/"),
    PathRemapRule("~/.prismclaw/plugins/", "${PRISMCLAW_USER_HOME}/assets/plugins/"),
    PathRemapRule("~/.prismclaw/agents/", "${PRISMCLAW_USER_HOME}/assets/agents/"),
    PathRemapRule("~/.prismclaw/", "${PRISMCLAW_USER_HOME}/"),
)


def apply_path_remap(content: str, rules: list[PathRemapRule]) -> str:
    """Rewrite known path prefixes in ``content`` per ``rules``.

    Rules are applied **most-specific-first**: longer ``source_prefix`` values
    run before shorter ones, so a plugin-declared
    ``~/.prismclaw/plugins/config/foo/`` overrides the engine default
    ``~/.prismclaw/`` rather than being pre-empted by it. Within the same
    length, declaration order is preserved (stable sort).

    Single pass — a rewritten ``${...}`` placeholder is never re-scanned.
    This is a pure text transform — it does not read or write any files.
    """
    if not content or not rules:
        return content
    # Stable sort descending by source length: more specific prefixes first.
    ordered = sorted(rules, key=lambda r: len(r.source_prefix), reverse=True)
    result = content
    for rule in ordered:
        if rule.source_prefix and rule.source_prefix in result:
            result = result.replace(rule.source_prefix, rule.target_prefix)
    return result


def parse_remap_rules(config: Any) -> list[PathRemapRule]:
    """Parse a ``porting.remap`` declaration into :class:`PathRemapRule` objects.

    Accepts the raw value of a manifest's ``porting.remap`` key: a list of
    ``{"from": <prefix>, "to": <prefix>}`` mappings. Invalid entries (non-dict,
    missing/empty/non-string ``from`` or ``to``) are skipped rather than raising,
    so a malformed declaration degrades to "fewer rules" instead of breaking
    plugin load. Declaration order is preserved (it is significant).
    """
    if not isinstance(config, list):
        return []
    rules: list[PathRemapRule] = []
    for entry in config:
        if not isinstance(entry, dict):
            continue
        source = entry.get("from")
        target = entry.get("to")
        if not isinstance(source, str) or not source:
            continue
        if not isinstance(target, str):
            continue
        rules.append(PathRemapRule(source_prefix=source, target_prefix=target))
    return rules
