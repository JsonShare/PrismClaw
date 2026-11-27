"""Path remapping for ported experts — a generic, expert-declared mechanism.

Experts translated from another ecosystem (e.g. ``my-expert`` from the
reference implementation) may hard-code that ecosystem's stateful paths in
their prose, such as ``~/.prismclaw/experts/config/<root>/<area>/...``. Those paths do not
exist under PrismClaw until the user has run their cold-start interview, and
more importantly, the model routinely mis-expands ``~`` (drops the dot,
produces ``/home/user/prismclaw/...`` instead of ``/home/user/.prismclaw/...``).

Two layers of defence:

    1. **Engine default** (always applied, expert cannot disable): remaps
       ``~/.prismclaw/`` to ``${PRISMCLAW_USER_HOME}/`` before any expert-
       declared rules. This catches 100% of experts that prose-write the
       canonical PrismClaw user home without having ever declared a remap —
       which is every expert in the tree today.

    2. **Expert-declared rules** (in ``plugin.json`` ``porting.remap``): these
       run *after* the default and can override or augment it. A expert that
       wants to remap ``~/.prismclaw/experts/config/foo/`` to its expert data
       directory declares a rule with a more specific ``from`` prefix — order
       matters, so more specific prefixes must come first.

The loader applies the combined rules immediately before variable substitution,
so a rewritten ``${...}`` placeholder resolves to the expert's real persistent
locations in the same pass.

Design constraints:
    * Generic: this module knows nothing about any specific expert. Rules come
      from the expert's manifest; a expert with no ``porting.remap`` still
      benefits from the engine default.
    * Surgical: only a matched ``from`` prefix is replaced; the remainder of each
      path and all surrounding text are byte-for-byte preserved.
    * Deterministic: rules apply in declaration order, single pass — a rewritten
      ``${...}`` placeholder is never re-scanned.
"""

from __future__ import annotations

from dataclasses import dataclass

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
    ``${PRISMCLAW_PLUGIN_DATA}/`` that a later substitution pass resolves.
    """

    source_prefix: str
    target_prefix: str


# Engine-level default remap applied to *every* expert. This is not a expert
# declaration — the loader prepends it automatically. See module docstring for
# rationale.
#
# 只做一件事：把专家包正文里的 ``~/.prismclaw/`` 换成可移植占位符
# ``${PRISMCLAW_USER_HOME}/``，避免模型自行展开 ``~``（常被展开成
# ``/home/user/prismclaw`` 而非 ``/home/user/.prismclaw``）。静态能力目录
# 在正文中已直接写 ``~/.prismclaw/assets/...``，无需再单独写 assets 专用规则；
# 模型遗漏 ``assets`` 段的路径纠正属于路径解析器职责（见
# ``toolkit/file/workspace.py::canonicalize_fs_path``）。
DEFAULT_REMAP_RULES: tuple[PathRemapRule, ...] = (
    PathRemapRule("~/.prismclaw/", "${PRISMCLAW_USER_HOME}/"),
)


def apply_path_remap(content: str, rules: list[PathRemapRule]) -> str:
    """Rewrite known path prefixes in ``content`` per ``rules``.

    Rules are applied **most-specific-first**: longer ``source_prefix`` values
    run before shorter ones, so a expert-declared
    ``~/.prismclaw/experts/config/foo/`` overrides the engine default
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


def parse_remap_rules(config: object) -> list[PathRemapRule]:
    """Parse a ``porting.remap`` declaration into :class:`PathRemapRule` objects.

    Accepts the raw value of a manifest's ``porting.remap`` key: a list of
    ``{"from": <prefix>, "to": <prefix>}`` mappings. Invalid entries (non-dict,
    missing/empty/non-string ``from`` or ``to``) are skipped rather than raising,
    so a malformed declaration degrades to "fewer rules" instead of breaking
    expert load. Declaration order is preserved (it is significant).
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
