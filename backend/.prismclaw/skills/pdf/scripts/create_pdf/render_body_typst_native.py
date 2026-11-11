#!/usr/bin/env python3
"""
render_body_typst_native.py — Pure Typst PDF renderer (unified)

All visual elements rendered natively through Typst:
  - Math: native Typst math (vector output, full LaTeX environment support)
  - Charts: Typst cetz canvas (bar, line, pie — no matplotlib)
  - Flowcharts: Typst fletcher diagrams (no Node.js / g6)
  - Code: Typst native raw blocks with syntax highlighting
  - Tables: Typst native tables with accent styling

Only dependency: pip install typst

Usage:
    python3 render_body_typst_native.py --tokens tokens.json --content content.json --out body.pdf

Exit codes: 0 success, 1 bad args/missing file, 2 missing dep, 3 render error
"""

import argparse
import colorsys
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import importlib.util


# ── Dependency bootstrap ───────────────────────────────────────────────────────

def _pip_install(*packages):
    import platform
    missing = [p for p in packages
               if importlib.util.find_spec(p.split("[")[0]) is None]
    if not missing:
        return
    cmd = [sys.executable, "-m", "pip", "install",
           "-i", "https://pypi.tuna.tsinghua.edu.cn/simple", "-q"]
    if platform.system() == "Linux":
        cmd.append("--break-system-packages")
    subprocess.check_call(cmd + missing)


def ensure_deps():
    _pip_install("typst")


ensure_deps()

import typst as typst_compiler


_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SKILL_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
_LAYOUTS_DIR = os.path.join(_SKILL_ROOT, "assets", "templates", "layouts")


# ══════════════════════════════════════════════════════════════════════════════
# Color helpers
# ══════════════════════════════════════════════════════════════════════════════

def _hex_to_rgb(h: str):
    h = h.lstrip("#")
    return int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255


def _generate_chart_colors(accent_hex: str, n: int) -> list:
    """Generate n colors from accent by rotating hue."""
    r, g, b = _hex_to_rgb(accent_hex)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    colors = []
    for i in range(max(n, 1)):
        h2 = (h + i * 0.13) % 1.0
        s2 = max(0.35, s - i * 0.08)
        v2 = min(0.92, v + i * 0.04)
        r2, g2, b2 = colorsys.hsv_to_rgb(h2, s2, v2)
        colors.append(f"#{int(r2*255):02X}{int(g2*255):02X}{int(b2*255):02X}")
    return colors


def _nice_ticks(max_val: float, n_ticks: int = 5) -> list:
    """Generate nice round tick values for chart axes."""
    if max_val <= 0:
        return [0]
    raw_step = max_val / n_ticks
    mag = 10 ** math.floor(math.log10(raw_step))
    for step in [1, 2, 2.5, 5, 10]:
        nice = step * mag
        if nice >= raw_step:
            break
    ticks = []
    v = 0
    while v <= max_val * 1.05:
        ticks.append(v)
        v += nice
    return ticks


# ══════════════════════════════════════════════════════════════════════════════
# Typst text utilities
# ══════════════════════════════════════════════════════════════════════════════

_TYPST_SPECIAL = re.compile(r'([#\$\*_@<>\\{}])')


def _insert_word_breaks(text: str, max_token: int = 10) -> str:
    """Insert zero-width spaces into long unbreakable ASCII/Latin tokens to allow line wrapping.

    Only targets runs of ASCII/Latin characters (letters, digits, punctuation).
    CJK text is naturally breakable by Typst and is left untouched.
    Breaks at: camelCase boundaries, dots, underscores, hyphens, slashes, colons.
    If after natural breaks the segments are still too long, force-break every max_token chars.
    """
    zwsp = "\u200B"
    def _break_token(m):
        token = m.group(0)
        if len(token) <= max_token:
            return token
        # Insert ZWSP at natural break points
        result = re.sub(r'(?<=[a-z])(?=[A-Z])', zwsp, token)          # camelCase
        result = re.sub(r'(?<=[A-Za-z0-9])([._/:\-])', zwsp + r'\1', result)  # before . _ / : -
        result = re.sub(r'([._/:\-])(?=[A-Za-z0-9])', r'\1' + zwsp, result)   # after . _ / : -
        # Force-break any remaining long segments
        parts = result.split(zwsp)
        final_parts = []
        for p in parts:
            while len(p) > max_token:
                final_parts.append(p[:max_token])
                p = p[max_token:]
            final_parts.append(p)
        return zwsp.join(final_parts)
    # Only match long runs of non-whitespace ASCII characters (exclude CJK ranges)
    return re.sub(r'[\x21-\x7e]{' + str(max_token) + r',}', _break_token, text)


def _escape_typst(text: str) -> str:
    """Escape Typst special characters in plain text."""
    return _TYPST_SPECIAL.sub(r'\\\1', text)


def _safe_asset_name(source_path: str, index: int) -> str:
    base = os.path.basename(source_path)
    stem, ext = os.path.splitext(base)
    ext = ext.lower() if ext else ".png"
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._")
    if not safe_stem:
        safe_stem = "image"
    digest = hashlib.sha1(os.path.abspath(source_path).encode("utf-8")).hexdigest()[:8]
    return f"fig_{index:03d}_{safe_stem}_{digest}{ext}"


def _path_relative(path: str, start: str) -> str:
    try:
        rel = os.path.relpath(path, start)
    except ValueError:
        return path.replace(os.sep, "/")
    return rel.replace(os.sep, "/")


def _escape_typst_string(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _resolve_source_path(raw_path: str, project_root: str) -> str:
    if not raw_path:
        return ""
    if os.path.isabs(raw_path):
        return raw_path

    project_candidate = os.path.abspath(os.path.join(project_root, raw_path))
    if os.path.exists(project_candidate):
        return project_candidate

    cwd_candidate = os.path.abspath(raw_path)
    if os.path.exists(cwd_candidate):
        return cwd_candidate

    return project_candidate


def _snapshot_images(content: list, project_root: str, typ_dir: str) -> dict:
    assets_dir = os.path.abspath(os.path.join(project_root, "assets", "images"))
    os.makedirs(assets_dir, exist_ok=True)

    snapshots = {}
    image_index = 0
    for item in content:
        if not isinstance(item, dict) or item.get("type") not in ("image", "figure"):
            continue
        raw_path = str(item.get("path", item.get("src", "")))
        source_path = _resolve_source_path(raw_path, project_root)
        if not source_path or not os.path.exists(source_path):
            continue
        source_path = os.path.abspath(source_path)
        if source_path in snapshots:
            continue
        # Image already inside assets/images — reuse it in place. Re-snapshotting
        # would create a redundant fig_*-prefixed duplicate of a file that is
        # already a valid project asset.
        if os.path.normcase(os.path.dirname(source_path)) == os.path.normcase(assets_dir):
            snapshots[source_path] = _path_relative(source_path, typ_dir)
            continue
        image_index += 1
        asset_name = _safe_asset_name(source_path, image_index)
        asset_path = os.path.join(assets_dir, asset_name)
        if os.path.normcase(os.path.abspath(source_path)) != os.path.normcase(os.path.abspath(asset_path)):
            shutil.copy2(source_path, asset_path)
        snapshots[source_path] = _path_relative(asset_path, typ_dir)
    return snapshots


def _html_to_typst(text: str) -> str:
    """Convert content.json text (with <b>, <i>, <font> markup) to Typst.

    Inline math wrapped in $...$ is preserved and converted from LaTeX to
    Typst math syntax.  Everything outside math spans is escaped normally.
    Bare LaTeX math patterns (e.g. C_{total}, x^{2}) are auto-detected and
    wrapped in $...$ before processing.
    """
    # 0. Auto-detect bare LaTeX math patterns NOT already inside $...$
    #    and wrap them so they render as inline math instead of garbled text.
    #    Matches: C_{total}, x^{2}, A_{i}^{2}, etc.
    #    IMPORTANT: only apply to text OUTSIDE existing $...$ spans to avoid
    #    breaking formulas that are already properly delimited.
    _BARE_MATH_RE = re.compile(
        r'([A-Za-z]+(?:[_^]\{[^}]*\})+)'
    )
    _DOLLAR_SPLIT = re.compile(r'(\$[^$]+\$)')
    _parts0 = _DOLLAR_SPLIT.split(text)
    for _k in range(len(_parts0)):
        if not (_parts0[_k].startswith('$') and _parts0[_k].endswith('$')):
            _parts0[_k] = _BARE_MATH_RE.sub(r'$\1$', _parts0[_k])
    text = ''.join(_parts0)

    # 1. Split on inline math $...$, protect math spans from escaping.
    #    We use a regex that matches $...$ but not escaped \$.
    math_pattern = re.compile(r'(?<!\\)\$(.+?)(?<!\\)\$')
    segments = math_pattern.split(text)
    # segments alternates: [plain, math_content, plain, math_content, ...]
    rebuilt = []
    for idx, seg in enumerate(segments):
        if idx % 2 == 1:
            # This is math content — convert LaTeX to Typst math
            converted = _latex_to_typst_math(seg)
            rebuilt.append(f'${converted}$')
        else:
            # Plain text — escape normally, then process HTML tags
            rebuilt.append(seg)
    text = ''.join(rebuilt)

    # 2. Now split on HTML tags and escape plain-text portions only.
    #    We must not escape the $...$ math spans we just inserted.
    parts = re.split(r'(\$[^$]+\$|</?(?:b|i|code|font)[^>]*>)', text)
    result = []
    for part in parts:
        if part.startswith('<') or part.startswith('$'):
            result.append(part)
        else:
            result.append(_escape_typst(part))
    text = ''.join(result)

    text = re.sub(r'<b>(.*?)</b>', r'*\1*', text, flags=re.DOTALL)
    text = re.sub(r'<i>(.*?)</i>', r'_\1_', text, flags=re.DOTALL)

    def _font_repl(m):
        color = m.group(1)
        if not color.startswith('#'):
            color = '#' + color
        return f'#text(fill: rgb("{color}"))[{m.group(2)}]'
    text = re.sub(
        r'<font\s+color=["\']?([^"\'>\s]+)["\']?\s*>(.*?)</font>',
        _font_repl, text, flags=re.DOTALL,
    )
    text = re.sub(r'<code>(.*?)</code>', r'#raw("\1")', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', '', text)
    return text


# ══════════════════════════════════════════════════════════════════════════════
# LaTeX math → Typst math converter
# ══════════════════════════════════════════════════════════════════════════════

def _find_brace_group(text: str, pos: int):
    """Extract balanced {...} starting at pos. Returns (content, end_pos)."""
    if pos >= len(text) or text[pos] != '{':
        return None, pos
    depth = 1
    i = pos + 1
    while i < len(text):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return text[pos + 1:i], i + 1
        i += 1
    return None, pos


_SIMPLE_MATH = [
    ('\\qquad', ' wide '), ('\\quad', ' quad '), ('\\,', ' '),
    ('\\;', ' '), ('\\!', ''),
    ('\\left(', '('), ('\\right)', ')'),
    ('\\left[', '['), ('\\right]', ']'),
    ('\\left\\{', '{'), ('\\right\\}', '}'),
    ('\\left|', '|'), ('\\right|', '|'),
    ('\\left.', ''), ('\\right.', ''),
    ('\\partial', 'partial'), ('\\nabla', 'nabla'),
    ('\\infty', 'infinity'), ('\\integral', 'integral'),
    ('\\int', 'integral'), ('\\sum', 'sum'), ('\\prod', 'product'),
    ('\\lim', 'lim'), ('\\log', 'log'), ('\\ln', 'ln'),
    ('\\sin', 'sin'), ('\\cos', 'cos'), ('\\tan', 'tan'),
    ('\\exp', 'exp'), ('\\min', 'min'), ('\\max', 'max'),
    ('\\arg', 'arg'),
    ('\\approx', 'approx'), ('\\equiv', 'equiv'),
    ('\\leq', '<='), ('\\geq', '>='), ('\\neq', '!='),
    ('\\ll', '<<'), ('\\gg', '>>'), ('\\in', 'in'),
    ('\\notin', 'in.not'), ('\\subset', 'subset'), ('\\supset', 'supset'),
    ('\\subseteq', 'subset.eq'), ('\\supseteq', 'supset.eq'),
    ('\\cup', 'union'), ('\\cap', 'sect'),
    ('\\forall', 'forall'), ('\\exists', 'exists'),
    ('\\Rightarrow', 'arrow.r.double'), ('\\Leftarrow', 'arrow.l.double'),
    ('\\Leftrightarrow', 'arrow.l.r.double'),
    ('\\rightarrow', 'arrow.r'), ('\\leftarrow', 'arrow.l'),
    ('\\to', 'arrow.r'), ('\\mapsto', 'arrow.r.bar'),
    ('\\cdot', 'dot'), ('\\times', 'times'), ('\\div', 'div'),
    ('\\pm', 'plus.minus'), ('\\mp', 'minus.plus'),
    ('\\ldots', 'dots'), ('\\cdots', 'dots.c'),
    ('\\vdots', 'dots.v'), ('\\ddots', 'dots.down'),
    ('\\alpha', 'alpha'), ('\\beta', 'beta'), ('\\gamma', 'gamma'),
    ('\\delta', 'delta'), ('\\epsilon', 'epsilon'),
    ('\\varepsilon', 'epsilon.alt'),
    ('\\zeta', 'zeta'), ('\\eta', 'eta'), ('\\theta', 'theta'),
    ('\\vartheta', 'theta.alt'),
    ('\\iota', 'iota'), ('\\kappa', 'kappa'), ('\\lambda', 'lambda'),
    ('\\mu', 'mu'), ('\\nu', 'nu'), ('\\xi', 'xi'),
    ('\\pi', 'pi'), ('\\rho', 'rho'), ('\\sigma', 'sigma'),
    ('\\tau', 'tau'), ('\\upsilon', 'upsilon'), ('\\phi', 'phi'),
    ('\\varphi', 'phi.alt'),
    ('\\chi', 'chi'), ('\\psi', 'psi'), ('\\omega', 'omega'),
    ('\\Gamma', 'Gamma'), ('\\Delta', 'Delta'), ('\\Theta', 'Theta'),
    ('\\Lambda', 'Lambda'), ('\\Xi', 'Xi'), ('\\Pi', 'Pi'),
    ('\\Sigma', 'Sigma'), ('\\Phi', 'Phi'), ('\\Psi', 'Psi'),
    ('\\Omega', 'Omega'),
    # Arrows & bracket symbols
    ('\\uparrow', 'arrow.t'), ('\\downarrow', 'arrow.b'),
    ('\\Uparrow', 'arrow.t.double'), ('\\Downarrow', 'arrow.b.double'),
    ('\\updownarrow', 'arrow.t.b'), ('\\longrightarrow', 'arrow.r.long'),
    ('\\longleftarrow', 'arrow.l.long'),
    ('\\langle', 'angle.l'), ('\\rangle', 'angle.r'),
    ('\\lceil', 'ceil.l'), ('\\rceil', 'ceil.r'),
    ('\\lfloor', 'floor.l'), ('\\rfloor', 'floor.r'),
    # Additional operators & symbols
    ('\\dagger', 'dagger'), ('\\ddagger', 'dagger.double'),
    ('\\star', 'star'), ('\\circ', 'circle.small'),
    ('\\bullet', 'bullet'), ('\\oplus', 'plus.circle'),
    ('\\otimes', 'times.circle'), ('\\oslash', 'diameter'),
    ('\\propto', 'prop'), ('\\sim', 'tilde'), ('\\simeq', 'tilde.eq'),
    ('\\cong', 'tilde.equiv'), ('\\perp', 'perp'), ('\\parallel', 'parallel'),
    ('\\neg', 'not'), ('\\wedge', 'and'), ('\\vee', 'or'),
    ('\\hbar', 'planck.reduce'),
    ('\\ell', 'ell'), ('\\Re', 'Re'), ('\\Im', 'Im'),
    ('\\det', 'det'), ('\\dim', 'dim'), ('\\ker', 'ker'),
    ('\\deg', 'deg'), ('\\sup', 'sup'), ('\\inf', 'inf'),
    ('\\sinh', 'sinh'), ('\\cosh', 'cosh'), ('\\tanh', 'tanh'),
]


def _latex_to_typst_math(expr: str) -> str:
    """Best-effort LaTeX math → Typst math conversion."""

    _TYPST_KEYWORDS = frozenset({
        'frac', 'sqrt', 'root', 'sum', 'product', 'integral', 'lim',
        'log', 'ln', 'sin', 'cos', 'tan', 'exp', 'min', 'max', 'arg',
        'infinity', 'partial', 'nabla', 'forall', 'exists',
        'alpha', 'beta', 'gamma', 'delta', 'epsilon', 'zeta', 'eta',
        'theta', 'iota', 'kappa', 'lambda', 'mu', 'nu', 'xi', 'pi',
        'rho', 'sigma', 'tau', 'upsilon', 'phi', 'chi', 'psi', 'omega',
        'Gamma', 'Delta', 'Theta', 'Lambda', 'Xi', 'Pi', 'Sigma',
        'Phi', 'Psi', 'Omega',
        'approx', 'equiv', 'times', 'div', 'dot', 'quad', 'wide',
        'bold', 'italic', 'upright', 'cal', 'hat', 'overline', 'arrow',
        'tilde', 'underline', 'union', 'sect', 'subset', 'supset',
        'dots', 'in', 'not',
        # Bracket & arrow symbols
        'angle', 'ceil', 'floor', 'dagger', 'star', 'bullet',
        'prop', 'perp', 'parallel', 'planck', 'reduce',
        'ell', 'det', 'dim', 'ker', 'deg', 'sup', 'inf',
        'sinh', 'cosh', 'tanh',
        # Matrix & layout
        'mat', 'vec', 'delim', 'cases',
        # Compound dotted names (base parts)
        'plus', 'minus', 'circle', 'double', 'small', 'long',
        'diameter', 'Re', 'Im',
    })

    def _split_letters(text: str) -> str:
        parts = re.split(r'("(?:[^"\\]|\\.)*")', text)
        out = []
        for part in parts:
            if part.startswith('"') and part.endswith('"'):
                out.append(part)
                continue
            tokens = re.findall(r'[a-zA-Z]+(?:\.[a-zA-Z]+)?|[^a-zA-Z]+', part)
            for tok in tokens:
                base = tok.split('.')[0] if '.' in tok else tok
                if base.isalpha() and len(base) > 1 and base not in _TYPST_KEYWORDS:
                    out.append(' '.join(base))
                    if '.' in tok:
                        out.append('.' + tok.split('.', 1)[1])
                else:
                    out.append(tok)
        return ''.join(out)

    def _process(text: str) -> str:
        result = []
        i = 0

        def _needs_space(new_token: str) -> bool:
            if not result or not new_token:
                return False
            prev = result[-1]
            if not prev:
                return False
            return prev[-1].isalpha() and new_token[0].isalpha()

        def _emit(token: str):
            if _needs_space(token):
                result.append(' ')
            result.append(token)

        while i < len(text):
            # ── Matrix environments: \begin{pmatrix}...\end{pmatrix} etc. ──
            _MAT_ENV_RE = re.compile(
                r'\\begin\{(pmatrix|bmatrix|Bmatrix|vmatrix|Vmatrix|matrix|cases)\}')
            m_env = _MAT_ENV_RE.match(text, i)
            if m_env:
                env_name = m_env.group(1)
                end_tag = f'\\end{{{env_name}}}'
                end_pos = text.find(end_tag, m_env.end())
                if end_pos != -1:
                    body = text[m_env.end():end_pos]
                    # Parse rows (separated by \\) and cols (separated by &)
                    rows = [r.strip() for r in re.split(r'\\\\', body)]

                    if env_name == 'cases':
                        # Typst cases(): each row is "value & condition"
                        # Output: cases(val1 & cond1, val2 & cond2)
                        case_parts = []
                        for row in rows:
                            if not row:
                                continue
                            case_parts.append(_process(row.strip()))
                        _emit(f'cases({", ".join(case_parts)})')
                    else:
                        converted_rows = []
                        for row in rows:
                            if not row:
                                continue
                            cols = [_process(c.strip()) for c in row.split('&')]
                            converted_rows.append(', '.join(cols))
                        mat_body = '; '.join(converted_rows)
                        # Typst delimiters
                        delim_map = {
                            'pmatrix': 'delim: "("',
                            'bmatrix': 'delim: "["',
                            'Bmatrix': 'delim: "{"',
                            'vmatrix': 'delim: "|"',
                            'Vmatrix': 'delim: "‖"',
                            'matrix':  '',
                        }
                        delim = delim_map.get(env_name, '')
                        if delim:
                            _emit(f'mat({delim}, {mat_body})')
                        else:
                            _emit(f'mat({mat_body})')
                    i = end_pos + len(end_tag)
                    continue

            if text[i:].startswith('\\frac'):
                i += 5
                while i < len(text) and text[i] == ' ':
                    i += 1
                num, i = _find_brace_group(text, i)
                while i < len(text) and text[i] == ' ':
                    i += 1
                den, i = _find_brace_group(text, i)
                if num is not None and den is not None:
                    _emit(f'frac({_process(num)}, {_process(den)})')
                else:
                    _emit('frac')
                continue

            _ONE_ARG = [
                ('\\sqrt', 'sqrt'), ('\\text', None),
                ('\\mathrm', 'upright'), ('\\mathcal', 'cal'),
                ('\\mathbf', 'bold'), ('\\mathit', 'italic'),
                ('\\hat', 'hat'), ('\\bar', 'overline'), ('\\vec', 'arrow'),
                ('\\dot', 'dot'), ('\\ddot', 'dot.double'),
                ('\\tilde', 'tilde'), ('\\overline', 'overline'),
                ('\\underline', 'underline'),
            ]
            matched_one = False
            for latex_cmd, typst_fn in _ONE_ARG:
                if text[i:].startswith(latex_cmd):
                    end_cmd = i + len(latex_cmd)
                    if end_cmd < len(text) and text[end_cmd].isalpha():
                        continue
                    i = end_cmd
                    while i < len(text) and text[i] == ' ':
                        i += 1
                    arg, i = _find_brace_group(text, i)
                    if arg is not None:
                        inner = _process(arg)
                        if typst_fn is None:
                            _emit(f'"{arg}"')
                        else:
                            _emit(f'{typst_fn}({inner})')
                    matched_one = True
                    break
            if matched_one:
                continue

            if text[i:].startswith('\\|'):
                _emit('||')
                i += 2
                continue

            matched_simple = False
            for latex_cmd, typst_cmd in _SIMPLE_MATH:
                if text[i:].startswith(latex_cmd):
                    end_cmd = i + len(latex_cmd)
                    if (latex_cmd[-1].isalpha() and end_cmd < len(text)
                            and text[end_cmd].isalpha()):
                        continue
                    _emit(typst_cmd)
                    i = end_cmd
                    matched_simple = True
                    break
            if matched_simple:
                continue

            if text[i] in ('_', '^') and i + 1 < len(text) and text[i + 1] == '{':
                op = text[i]
                arg, end = _find_brace_group(text, i + 1)
                if arg is not None:
                    # If the subscript/superscript content is a pure text label
                    # (e.g. "total", "ticket", "max"), wrap it as a Typst string
                    # to keep it as a single upright word, not split into
                    # individual italic variables.
                    if (arg.isalpha() and len(arg) > 1
                            and arg not in _TYPST_KEYWORDS
                            and not any(c in arg for c in '\\{}^_')):
                        result.append(f'{op}("{arg}")')
                    else:
                        result.append(f'{op}({_process(arg)})')
                    i = end
                    continue

            if text[i] == '\\' and i + 1 < len(text) and text[i + 1].isalpha():
                j = i + 1
                while j < len(text) and text[j].isalpha():
                    j += 1
                cmd_name = text[i + 1:j]
                _emit(cmd_name)
                i = j
                continue

            result.append(text[i])
            i += 1

        return ''.join(result)

    raw = _process(expr)
    result = _split_letters(raw)

    # ── Safety net: balance unclosed delimiters ──────────────────────
    # LLMs sometimes produce LaTeX with missing closing braces.
    # After conversion, each opening '(' / '[' must have a matching
    # closer — otherwise Typst raises "unclosed delimiter".
    # IMPORTANT: skip characters inside quoted strings "..." so that
    # e.g. mat(delim: "(", ...) doesn't miscount the quoted paren.
    for open_ch, close_ch in [('(', ')'), ('[', ']'), ('{', '}')]:
        depth = 0
        in_quote = False
        for c in result:
            if c == '"':
                in_quote = not in_quote
            elif not in_quote:
                if c == open_ch:
                    depth += 1
                elif c == close_ch:
                    depth -= 1
        if depth > 0:
            result += close_ch * depth
        elif depth < 0:
            result = open_ch * (-depth) + result

    return result


# ══════════════════════════════════════════════════════════════════════════════
# Native Typst chart generators (cetz)
# ══════════════════════════════════════════════════════════════════════════════

# ── Bar chart ─────────────────────────────────────────────────────────────────

def _chart_bar(item: dict, accent: str, columns: int = 1) -> str:
    """Generate Typst CETZ code for a bar chart with auto-scaled Y-axis."""
    labels   = item.get("labels", [])
    datasets = item.get("datasets", [])
    title    = item.get("title", "")
    y_label  = item.get("y_label", "")
    n_groups = len(labels)
    n_series = max(len(datasets), 1)
    if n_groups == 0:
        return ""

    all_vals = [v for ds in datasets for v in ds.get("values", [])]
    if not all_vals:
        return ""
    data_max = max(all_vals)
    data_min = min(all_vals)

    # --- Generate nice ticks covering full data range ---
    # Bar charts MUST include 0 in the y-axis range so bars have a baseline.
    # Extend the data range to include 0 before computing ticks.
    effective_min = min(data_min, 0)
    effective_max = max(data_max, 0)
    if effective_max <= 0:
        effective_max = 0
    if effective_min >= 0:
        effective_min = 0

    if effective_min >= 0:
        # All positive (or zero): ticks from 0 upward
        if effective_max <= 0:
            effective_max = 1
        ticks = _nice_ticks(effective_max)
        y_max = ticks[-1] if ticks else max(effective_max, 1)
        y_min = 0.0
        # Auto-scale if any real data exceeds computed y_max
        if data_max > y_max:
            y_max = data_max * 1.1
    else:
        # Has negative values: need ticks below zero too
        span = effective_max - effective_min
        if span <= 0:
            span = 1
        raw_step = span / 5
        mag = 10 ** math.floor(math.log10(max(raw_step, 1e-12)))
        nice_step = mag
        for step in [1, 2, 2.5, 5, 10]:
            candidate = step * mag
            if candidate >= raw_step:
                nice_step = candidate
                break
        y_min = math.floor(effective_min / nice_step) * nice_step
        y_max = math.ceil(effective_max / nice_step) * nice_step
        # Ensure 0 is included
        if y_min > 0:
            y_min = 0
        if y_max < 0:
            y_max = 0
        if y_max <= y_min:
            y_max = y_min + nice_step
        ticks = []
        v = y_min
        while v <= y_max + nice_step * 0.01:
            ticks.append(round(v, 10))
            v += nice_step

    y_range = y_max - y_min
    if y_range <= 0:
        y_range = 1

    # Helper: map data value to canvas y coordinate
    def _val_to_y(val):
        return (val - y_min) / y_range * ch

    colors = _generate_chart_colors(accent, n_series)

    # Chart dimensions — scale down for multi-column layouts
    if columns >= 3:
        cw, ch = 4.5, 3.0
    elif columns == 2:
        cw, ch = 6.5, 4.0
    else:
        cw, ch = 10.0, 6.0
    group_w = cw / n_groups
    bar_w = group_w * 0.65 / n_series
    gap = group_w * 0.175

    # Zero baseline in canvas coordinates
    zero_y = _val_to_y(0)

    lines = []
    lines.append("#align(center)[")
    lines.append("#cetz.canvas(length: 1cm, {")
    lines.append("  import cetz.draw: *")

    # Title
    if title:
        lines.append(
            f'  content(({cw/2:.2f}, {ch + 0.6:.2f}), '
            f'text(size: 10pt, weight: "bold", fill: dark)[{_html_to_typst(title)}])'
        )

    # Y-axis grid & ticks
    for tv in ticks:
        y = _val_to_y(tv)
        lines.append(f"  line((0, {y:.3f}), ({cw}, {y:.3f}), stroke: 0.3pt + luma(220))")
        lines.append(
            f'  content((-0.3, {y:.3f}), text(size: 7pt, fill: luma(130))[{tv:g}], anchor: "east")'
        )

    # Zero line (stronger, if range spans negative values)
    if y_min < 0 < y_max:
        lines.append(f"  line((0, {zero_y:.3f}), ({cw}, {zero_y:.3f}), stroke: 0.8pt + luma(140))")

    # Axes
    lines.append(f"  line((0, 0), ({cw}, 0), stroke: 0.5pt + luma(160))")
    lines.append(f"  line((0, 0), (0, {ch}), stroke: 0.5pt + luma(160))")

    # Y label
    if y_label:
        lines.append(
            f'  content((-1.5, {ch/2:.2f}), text(size: 8pt, fill: luma(100))'
            f'[{_html_to_typst(y_label)}], angle: 90deg)'
        )

    # Bars
    for gi, label in enumerate(labels):
        for si, ds in enumerate(datasets):
            vals = ds.get("values", [])
            val = vals[gi] if gi < len(vals) else 0
            x = gi * group_w + gap + si * bar_w
            h = _val_to_y(val)
            color = colors[si % len(colors)]
            # Bar grows from zero baseline; negative bars go downward
            lines.append(
                f'  rect(({x:.3f}, {zero_y:.3f}), ({x + bar_w:.3f}, {h:.3f}), fill: rgb("{color}"), stroke: none)'
            )

        # X labels
        cx = gi * group_w + group_w / 2
        lines.append(
            f'  content(({cx:.3f}, -0.4), text(size: 8pt, fill: luma(100))'
            f'[{_html_to_typst(str(label))}])'
        )

    # Legend
    if n_series > 1:
        for si, ds in enumerate(datasets):
            lbl = ds.get("label", f"Series {si+1}")
            color = colors[si % len(colors)]
            ly = ch - 0.3 - si * 0.55
            lines.append(
                f'  rect(({cw + 0.20:.2f}, {ly - 0.13:.2f}), ({cw + 0.45:.2f}, {ly + 0.12:.2f}), fill: rgb("{color}"), stroke: none)'
            )
            lines.append(
                f'  content(({cw + 0.60:.2f}, {ly:.2f}), text(size: 7pt, fill: luma(80))[{_html_to_typst(lbl)}], anchor: "west")'
            )

    lines.append("})")
    lines.append("]")
    return "\n".join(lines)


# ── Line chart ────────────────────────────────────────────────────────────────

def _chart_line(item: dict, accent: str, columns: int = 1) -> str:
    """Generate Typst CETZ code for a line chart with auto-scaled Y-axis."""
    labels   = item.get("labels", [])
    datasets = item.get("datasets", [])
    title    = item.get("title", "")
    y_label  = item.get("y_label", "")
    n_groups = len(labels)
    n_series = max(len(datasets), 1)
    if n_groups == 0:
        return ""

    # Flatten all values to find range
    all_vals = [v for ds in datasets for v in ds.get("values", [])]
    if not all_vals:
        return ""
    data_max = max(all_vals)
    data_min = min(all_vals)

    # --- Generate nice ticks covering full data range (including negatives) ---
    if data_min >= 0:
        # All positive: ticks from 0 upward
        ticks_pos = _nice_ticks(data_max)
        y_max = ticks_pos[-1] if ticks_pos else max(data_max, 1)
        y_min = 0.0
        ticks = ticks_pos
    else:
        # Has negative values: need ticks below zero too
        span = data_max - data_min
        if span <= 0:
            span = 1
        raw_step = span / 5
        mag = 10 ** math.floor(math.log10(max(raw_step, 1e-12)))
        nice_step = mag
        for step in [1, 2, 2.5, 5, 10]:
            candidate = step * mag
            if candidate >= raw_step:
                nice_step = candidate
                break
        # Round min down and max up to nice step
        y_min = math.floor(data_min / nice_step) * nice_step
        y_max = math.ceil(data_max / nice_step) * nice_step
        if y_max <= y_min:
            y_max = y_min + nice_step
        ticks = []
        v = y_min
        while v <= y_max + nice_step * 0.01:
            ticks.append(round(v, 10))
            v += nice_step

    y_range = y_max - y_min
    if y_range <= 0:
        y_range = 1

    colors = _generate_chart_colors(accent, n_series)

    # Chart dimensions — scale down for multi-column layouts
    if columns >= 3:
        cw, ch = 4.5, 3.0
    elif columns == 2:
        cw, ch = 6.5, 4.0
    else:
        cw, ch = 10.0, 6.0

    # Helper: map data value to canvas y coordinate
    def _val_to_y(val):
        return (val - y_min) / y_range * ch

    lines = []
    lines.append("#align(center)[")
    lines.append("#cetz.canvas(length: 1cm, {")
    lines.append("  import cetz.draw: *")

    # Title
    if title:
        lines.append(
            f'  content(({cw/2:.2f}, {ch + 0.6:.2f}), '
            f'text(size: 10pt, weight: "bold", fill: dark)'
            f'[{_html_to_typst(title)}])'
        )

    # Y-axis grid & ticks
    for tv in ticks:
        y = _val_to_y(tv)
        lines.append(f"  line((0, {y:.3f}), ({cw}, {y:.3f}), stroke: 0.3pt + luma(220))")
        lines.append(
            f'  content((-0.3, {y:.3f}), text(size: 7pt, fill: luma(130))[{tv:g}], anchor: "east")'
        )

    # Zero line (if range spans negative values, draw a stronger zero line)
    if y_min < 0 < y_max:
        zero_y = _val_to_y(0)
        lines.append(f"  line((0, {zero_y:.3f}), ({cw}, {zero_y:.3f}), stroke: 0.8pt + luma(140))")

    # Axes
    lines.append(f"  line((0, 0), ({cw}, 0), stroke: 0.5pt + luma(160))")
    lines.append(f"  line((0, 0), (0, {ch}), stroke: 0.5pt + luma(160))")

    # Y-axis label
    if y_label:
        lines.append(
            f'  content((-1.5, {ch/2:.2f}), '
            f'text(size: 8pt, fill: luma(100))[{_html_to_typst(y_label)}], angle: 90deg)'
        )

    # Lines + markers
    for si, ds in enumerate(datasets):
        vals = ds.get("values", [])
        color = colors[si % len(colors)]
        points = []
        for gi in range(min(n_groups, len(vals))):
            x = gi / max(n_groups - 1, 1) * cw
            y = _val_to_y(vals[gi])
            points.append((x, y))

        if len(points) >= 2:
            pts_str = ", ".join(f"({p[0]:.3f}, {p[1]:.3f})" for p in points)
            lines.append(f'  line({pts_str}, stroke: 1.8pt + rgb("{color}"))')

        for x, y in points:
            lines.append(f'  circle(({x:.3f}, {y:.3f}), radius: 0.08, fill: rgb("{color}"), stroke: none)')

    # X labels
    for gi, label in enumerate(labels):
        x = gi / max(n_groups - 1, 1) * cw
        lines.append(
            f'  content(({x:.3f}, -0.4), text(size: 8pt, fill: luma(100))[{_html_to_typst(str(label))}])'
        )

    # Legend (right side)
    if n_series > 1:
        for si, ds in enumerate(datasets):
            lbl = ds.get("label", f"Series {si+1}")
            color = colors[si % len(colors)]
            ly = ch - 0.3 - si * 0.55
            lines.append(f'  line(({cw + 0.50:.2f}, {ly:.2f}), ({cw + 0.80:.2f}, {ly:.2f}), stroke: 1.8pt + rgb("{color}"))')
            lines.append(f'  content(({cw + 0.95:.2f}, {ly:.2f}), text(size: 7pt, fill: luma(80))[{_html_to_typst(lbl)}], anchor: "west")')

    lines.append("})")
    lines.append("]")
    return "\n".join(lines)

# ── Pie chart ─────────────────────────────────────────────────────────────────

def _chart_pie(item: dict, accent: str, columns: int = 1) -> str:
    """Generate Typst cetz code for a pie chart."""
    labels   = item.get("labels", [])
    datasets = item.get("datasets", [])
    title    = item.get("title", "")

    vals = datasets[0].get("values", []) if datasets else []
    n = len(vals)
    if n == 0:
        return ""
    total = sum(vals) or 1
    colors = _generate_chart_colors(accent, n)

    # Pie dimensions — scale down for multi-column layouts
    if columns >= 3:
        r, cx, cy = 1.4, 1.8, 1.8
    elif columns == 2:
        r, cx, cy = 2.0, 2.5, 2.5
    else:
        r, cx, cy = 3.2, 4.0, 4.0

    lines = []
    lines.append("#align(center)[")
    lines.append("#cetz.canvas(length: 1cm, {")
    lines.append("  import cetz.draw: *")

    if title:
        lines.append(f'  content(({cx:.2f}, {cy + r + 1.0:.2f}), '
                     f'text(size: 10pt, weight: "bold", fill: dark)'
                     f'[{_html_to_typst(title)}])')

    # Pie slices
    angle = 0.0
    for i in range(n):
        frac = vals[i] / total
        delta = frac * 360.0
        color = colors[i % len(colors)]
        lines.append(f'  arc(({cx}, {cy}), start: {angle:.2f}deg, '
                     f'delta: {delta:.2f}deg, radius: {r}, '
                     f'anchor: "origin", mode: "PIE", '
                     f'fill: rgb("{color}"), stroke: 0.8pt + white)')

        # Percentage label at midpoint of slice
        mid_angle = angle + delta / 2
        mid_rad = math.radians(mid_angle)
        lx = cx + (r * 0.62) * math.cos(mid_rad)
        ly = cy + (r * 0.62) * math.sin(mid_rad)
        pct = f"{frac * 100:.1f}%"
        lines.append(f'  content(({lx:.3f}, {ly:.3f}), '
                     f'text(size: 7pt, fill: white, weight: "bold")[{pct}])')

        angle += delta

    # Legend (right side)
    lx_start = cx + r + 0.8
    for i in range(n):
        lbl = labels[i] if i < len(labels) else f"Slice {i+1}"
        color = colors[i % len(colors)]
        ly = cy + r - 0.6 - i * 0.55
        lines.append(f'  rect(({lx_start:.2f}, {ly - 0.13:.2f}), '
                     f'({lx_start + 0.25:.2f}, {ly + 0.12:.2f}), '
                     f'fill: rgb("{color}"), stroke: none)')
        lines.append(f'  content(({lx_start + 0.40:.2f}, {ly:.2f}), '
                     f'text(size: 7pt, fill: luma(80))'
                     f'[{_html_to_typst(lbl)}], anchor: "west")')

    lines.append("})")
    lines.append("]")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# Native Typst flowchart generator (fletcher)
# ══════════════════════════════════════════════════════════════════════════════

_SHAPE_MAP = {
    "oval":          "fletcher.shapes.pill",
    "rect":          "rect",
    "diamond":       "fletcher.shapes.diamond",
    "parallelogram": "fletcher.shapes.hexagon",
}


def _auto_layout(nodes: list, edges: list) -> dict:
    """Assign (col, row) grid positions to flowchart nodes via BFS."""
    from collections import defaultdict, deque

    children = defaultdict(list)
    parents = defaultdict(list)
    for e in edges:
        children[e["from"]].append(e["to"])
        parents[e["to"]].append(e["from"])

    node_ids = [n["id"] for n in nodes]
    id_set = set(node_ids)

    # Find roots (no parents, or only the first node)
    roots = [nid for nid in node_ids if not parents[nid]]
    if not roots:
        roots = [node_ids[0]]

    # BFS for row assignment (depth from root)
    row = {}
    visited = set()
    queue = deque()
    for r in roots:
        visited.add(r)
        queue.append((r, 0))

    while queue:
        nid, depth = queue.popleft()
        if nid not in id_set:
            continue
        row[nid] = depth
        for child in children[nid]:
            if child not in visited and child in id_set:
                visited.add(child)
                queue.append((child, depth + 1))

    # Assign unvisited nodes
    for nid in node_ids:
        if nid not in row:
            row[nid] = max(row.values(), default=0) + 1

    # Assign columns within each row
    rows_grouped = defaultdict(list)
    for nid in node_ids:
        rows_grouped[row[nid]].append(nid)

    col = {}
    for r, nids in rows_grouped.items():
        for i, nid in enumerate(nids):
            col[nid] = i - (len(nids) - 1) / 2.0

    return {nid: (col.get(nid, 0), row.get(nid, 0)) for nid in node_ids}


def _flowchart_typst(item: dict, tokens: dict) -> str:
    """Generate Typst fletcher diagram code for a flowchart."""
    nodes = item.get("nodes", [])
    edges = item.get("edges", [])
    if not nodes:
        return ""

    accent    = tokens["accent"]
    accent_lt = tokens["accent_lt"]
    dark      = tokens["dark"]

    positions = _auto_layout(nodes, edges)
    node_map = {n["id"]: n for n in nodes}

    lines = []
    lines.append("#align(center)[")
    lines.append("#fletcher.diagram(")
    lines.append("  node-stroke: 0.8pt + luma(80),")
    lines.append(f'  node-fill: rgb("{accent_lt}"),')
    lines.append("  spacing: (18mm, 14mm),")
    lines.append(f'  edge-stroke: 0.8pt + rgb("{dark}"),')

    # Nodes
    for n in nodes:
        nid = n["id"]
        label = _html_to_typst(n.get("label", nid))
        shape_name = n.get("shape", "rect")
        shape_typst = _SHAPE_MAP.get(shape_name, "rect")
        col, row = positions.get(nid, (0, 0))
        # Convert float col to int if whole number
        col_s = str(int(col)) if col == int(col) else str(col)
        row_s = str(int(row)) if row == int(row) else str(row)
        lines.append(f'  fletcher.node(({col_s}, {row_s}), '
                     f'[{label}], '
                     f'shape: {shape_typst}),')

    # Edges
    for e in edges:
        f_id = e["from"]
        t_id = e["to"]
        f_pos = positions.get(f_id, (0, 0))
        t_pos = positions.get(t_id, (0, 0))
        fc = str(int(f_pos[0])) if f_pos[0] == int(f_pos[0]) else str(f_pos[0])
        fr = str(int(f_pos[1])) if f_pos[1] == int(f_pos[1]) else str(f_pos[1])
        tc = str(int(t_pos[0])) if t_pos[0] == int(t_pos[0]) else str(t_pos[0])
        tr = str(int(t_pos[1])) if t_pos[1] == int(t_pos[1]) else str(t_pos[1])
        edge_label = e.get("label", "")
        if edge_label:
            lines.append(f'  fletcher.edge(({fc}, {fr}), ({tc}, {tr}), '
                         f'"->", label: [{_html_to_typst(edge_label)}]),')
        else:
            lines.append(f'  fletcher.edge(({fc}, {fr}), ({tc}, {tr}), '
                         f'"->"),')

    lines.append(")")
    lines.append("]")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# Typst document builder
# ══════════════════════════════════════════════════════════════════════════════

def _build_preamble(tokens: dict) -> str:
    """Generate Typst preamble from external template or inline fallback."""
    # Try loading external layout template
    layout = tokens.get("layout", "default")
    template_path = os.path.join(_LAYOUTS_DIR, layout, "preamble.typ")

    # Compute template variables
    ml = tokens["margin_left"] / 72 * 2.54
    mr = tokens["margin_right"] / 72 * 2.54
    mt = tokens["margin_top"] / 72 * 2.54
    mb = tokens["margin_bottom"] / 72 * 2.54

    accent    = tokens["accent"]
    accent_lt = tokens["accent_lt"]
    dark      = tokens["dark"]
    body_text = tokens["body_text"]
    muted     = tokens["muted"]

    size_h1   = tokens["size_h1"]
    size_h2   = tokens["size_h2"]
    size_h3   = tokens["size_h3"]
    size_body = tokens["size_body"]
    size_cap  = tokens["size_caption"]
    size_meta = tokens["size_meta"]
    line_gap  = tokens["line_gap"]
    para_gap  = tokens["para_gap"]
    section_gap = tokens["section_gap"]

    title  = _escape_typst(tokens.get("title", ""))
    author = _escape_typst(tokens.get("author", ""))
    date   = _escape_typst(tokens.get("date", ""))

    heading_font = tokens.get("heading_font_typst", '"Source Han Serif CN"')
    body_font    = tokens.get("body_font_typst", heading_font)
    doc_lang     = tokens.get("lang", "zh")

    # Template variable mapping
    variables = {
        "accent": accent,
        "accent_lt": accent_lt,
        "dark": dark,
        "body_text": body_text,
        "muted": muted,
        "columns": str(tokens.get("columns", 1)),
        "margin_left_cm": f"{ml:.2f}",
        "margin_right_cm": f"{mr:.2f}",
        "margin_top_cm": f"{mt:.2f}",
        "margin_bottom_cm": f"{mb:.2f}",
        "size_h1": str(size_h1),
        "size_h2": str(size_h2),
        "size_h3": str(size_h3),
        "size_body": str(size_body),
        "size_h5": str(size_body - 0.5),
        "size_h6": str(size_body - 0.5),
        "size_caption": str(size_cap),
        "size_meta": str(size_meta),
        "size_callout": str(size_body + 0.5),
        "para_gap": str(para_gap),
        "section_gap": str(section_gap),
        "leading": f"{line_gap / size_body:.2f}",
        "title": title,
        "author": author,
        "date": date,
        "heading_font": heading_font,
        "body_font": body_font,
        "lang": doc_lang,
        "show_cover": "true" if tokens.get("show_cover", True) else "false",
        "show_header": "true" if tokens.get("show_header", True) else "false",
    }

    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            template = f.read()
        # Replace {{variable}} placeholders
        for key, val in variables.items():
            template = template.replace("{{" + key + "}}", val)
        return template
    else:
        # Fallback: inline preamble (original code)
        return _build_preamble_inline(tokens)


def _build_preamble_inline(tokens: dict) -> str:
    """Inline fallback preamble (used when external template not found)."""
    ml = tokens["margin_left"] / 72 * 2.54
    mr = tokens["margin_right"] / 72 * 2.54
    mt = tokens["margin_top"] / 72 * 2.54
    mb = tokens["margin_bottom"] / 72 * 2.54

    accent    = tokens["accent"]
    accent_lt = tokens["accent_lt"]
    dark      = tokens["dark"]
    body_text = tokens["body_text"]
    muted     = tokens["muted"]

    size_h1   = tokens["size_h1"]
    size_h2   = tokens["size_h2"]
    size_h3   = tokens["size_h3"]
    size_body = tokens["size_body"]
    size_cap  = tokens["size_caption"]
    size_meta = tokens["size_meta"]
    line_gap  = tokens["line_gap"]
    para_gap  = tokens["para_gap"]
    section_gap = tokens["section_gap"]

    title  = _escape_typst(tokens.get("title", ""))
    author = _escape_typst(tokens.get("author", ""))
    date   = _escape_typst(tokens.get("date", ""))

    # Font family names — read from tokens (set by palette.py)
    heading_font = tokens.get("heading_font_typst", '"Source Han Serif CN"')
    body_font    = tokens.get("body_font_typst", heading_font)
    doc_lang     = tokens.get("lang", "zh")

    return f'''// ── Package imports ───────────────────────────────────────
#import "@preview/cetz:0.3.4" as cetz
#import "@preview/fletcher:0.5.7" as fletcher

// ── Color definitions ────────────────────────────────────
#let accent = rgb("{accent}")
#let accent-lt = rgb("{accent_lt}")
#let dark = rgb("{dark}")
#let body-text = rgb("{body_text}")
#let muted-color = rgb("{muted}")

// ── Page setup ───────────────────────────────────────────
#set page(
  paper: "a4",
  columns: {tokens.get("columns", 1)},
  margin: (left: {ml:.2f}cm, right: {mr:.2f}cm, top: {mt:.2f}cm, bottom: {mb:.2f}cm),
  header: context {{
    set text(size: {size_meta}pt, fill: muted-color)
    upper("{title}")
    h(1fr)
    "{date}"
    v(4pt)
    line(length: 100%, stroke: 1.5pt + accent)
  }},
  footer: context {{
    line(length: 100%, stroke: 0.5pt + luma(200))
    v(2pt)
    set text(size: {size_meta}pt, fill: muted-color)
    "{author}"
    h(1fr)
    counter(page).display()
  }},
)

// ── Text defaults ────────────────────────────────────────
#set text(
  font: ({body_font},),
  size: {size_body}pt,
  fill: body-text,
  lang: "{doc_lang}",
)
#let heading-font = ({heading_font},)
#set par(
  justify: true,
  leading: {line_gap / size_body:.2f}em,
  spacing: {para_gap}pt,
)

// ── Heading styles ───────────────────────────────────────
#show heading: set block(above: 0pt, below: 0pt)

#show heading.where(level: 1): it => {{
  v(0pt)
  text(font: heading-font, size: {size_h1}pt, weight: "bold", fill: dark)[#it.body]
  v(24pt)
  line(length: 100%, stroke: 1.2pt + accent)
  v(12pt)
}}

#show heading.where(level: 2): it => {{
  v(18pt)
  text(font: heading-font, size: {size_h2}pt, weight: "bold", fill: dark)[#it.body]
  v(9pt)
}}

#show heading.where(level: 3): it => {{
  v(14pt)
  text(font: heading-font, size: {size_h3}pt, weight: "bold", fill: dark)[#it.body]
  v(7pt)
}}

#show heading.where(level: 4): it => {{
  v(12pt)
  text(font: heading-font, size: {size_body}pt, weight: "bold", fill: dark)[#it.body]
  v(6pt)
}}

#show heading.where(level: 5): it => {{
  v(10pt)
  text(font: heading-font, size: {size_body - 0.5}pt, weight: "bold", fill: dark)[#it.body]
  v(5pt)
}}

#show heading.where(level: 6): it => {{
  v(8pt)
  text(font: heading-font, size: {size_body - 0.5}pt, fill: muted-color)[#it.body]
  v(4pt)
}}

// ── Helper functions ─────────────────────────────────────
#let callout-box(body) = {{
  v(8pt)
  block(
    width: 100%,
    fill: accent-lt,
    stroke: (left: 4pt + accent),
    radius: 3pt,
    inset: (left: 16pt, right: 10pt, top: 10pt, bottom: 10pt),
    text(weight: "bold", size: {size_body + 0.5}pt, fill: dark)[#body]
  )
  v(0pt)
}}

#let fit-math(body) = {{
  layout(size => {{
    let m = measure(body)
    if m.width > size.width {{
      let ratio = size.width / m.width
      align(center, scale(x: ratio * 100%, y: ratio * 100%, origin: center + horizon, body))
    }} else {{
      body
    }}
  }})
}}

#let caption-text(body) = {{
  set text(size: {size_cap}pt, fill: muted-color)
  align(center)[#body]
  v(6pt)
}}

#let accent-divider() = {{
  v(14pt)
  line(length: 100%, stroke: 1.2pt + accent)
  v(14pt)
}}

#let block-gap(body) = {{
  v(8pt)
  body
  v(8pt)
}}

#let figure-block(body) = {{
  v(10pt)
  body
  v(10pt)
}}

#let code-block(code, lang: none) = {{
  v(6pt)
  block(
    width: 100%,
    fill: luma(248),
    stroke: (left: 3pt + accent),
    radius: 2pt,
    inset: (x: 12pt, y: 8pt),
    {{
      if lang != none {{
        text(size: 7pt, fill: muted-color, weight: "bold")[#upper(lang)]
        v(4pt)
      }}
      text(size: 8.5pt)[#code]
    }}
  )
  v(6pt)
}}

'''


# ══════════════════════════════════════════════════════════════════════════════
# Block → Typst converters
# ══════════════════════════════════════════════════════════════════════════════

class TypstContext:
    """Mutable state passed to all block converters."""

    def __init__(self, tokens: dict, project_root: str, image_snapshots: dict):
        self.tokens = tokens
        self.project_root = project_root
        self.image_snapshots = image_snapshots
        self.figure_n = 0
        self.numbered_n = 0
        self.in_bullet = False
        self.in_numbered = False


def _close_lists(ctx: TypstContext) -> str:
    ctx.in_bullet = False
    ctx.in_numbered = False
    return ""


def _block_h(item: dict, ctx: TypstContext, level: int) -> str:
    _close_lists(ctx)
    text = _html_to_typst(item.get("text", ""))
    marker = "=" * level
    return f"\n{marker} {text}\n"


def _block_body(item: dict, ctx: TypstContext) -> str:
    _close_lists(ctx)
    text = _html_to_typst(item.get("text", ""))
    return f"\n#h(2em){text}\n"


def _block_bullet(item: dict, ctx: TypstContext) -> str:
    ctx.in_numbered = False
    ctx.in_bullet = True
    text = _html_to_typst(item.get("text", ""))
    return f"- {text}\n"


def _block_numbered(item: dict, ctx: TypstContext) -> str:
    ctx.in_bullet = False
    if not ctx.in_numbered:
        ctx.in_numbered = True
        ctx.numbered_n = 0
    ctx.numbered_n += 1
    text = _html_to_typst(item.get("text", ""))
    return f"+ {text}\n"


def _block_callout(item: dict, ctx: TypstContext) -> str:
    _close_lists(ctx)
    text = _html_to_typst(item.get("text", ""))
    return f"\n#callout-box[{text}]\n"


def _block_table(item: dict, ctx: TypstContext) -> str:
    _close_lists(ctx)
    headers = item.get("headers", [])
    rows    = item.get("rows", [])
    caption = item.get("caption", "")
    ncols = len(headers)
    if ncols == 0:
        return ""

    col_widths = item.get("col_widths")
    if col_widths:
        cols = ", ".join(f"{w}fr" for w in col_widths)
    else:
        # Use 1fr columns so each column gets equal share of table width, preventing overflow
        cols = ", ".join(["1fr"] * ncols)

    # Styling (fill, stroke, inset) is defined in preamble.typ via #set table / #show table.cell
    inner = f"#block(width: 100%, breakable: true)[\n"
    inner += f"#table(\n"
    inner += f"  columns: ({cols}),\n"
    inner += f"  table.header(\n"
    for h in headers:
        inner += (f'    [{_html_to_typst(_insert_word_breaks(str(h)))}],\n')
    inner += f"  ),\n"

    for row in rows:
        for cell in row:
            inner += f"  [{_html_to_typst(_insert_word_breaks(str(cell)))}],\n"

    if caption:
        # Embed caption as a full-width footer row so it always stays with the table
        inner += (
            f"  table.cell(colspan: {ncols}, fill: none, stroke: none, "
            f"inset: (x: 0pt, top: 6pt, bottom: 2pt))"
            f"[#caption-text[{_html_to_typst(caption)}]],\n"
        )
    inner += ")\n"
    inner += "]\n"  # close #block

    # No outer block(breakable: false) — table is breakable across pages;
    # caption stays attached because it is the last table row.
    typ = "\n#block-gap[\n"
    typ += inner
    typ += "]\n"
    return typ


def _block_image(item: dict, ctx: TypstContext) -> str:
    _close_lists(ctx)
    raw_path = str(item.get("path", item.get("src", "")))
    source_path = _resolve_source_path(raw_path, ctx.project_root)

    if not source_path or not os.path.exists(source_path):
        return f'\n#text(fill: muted-color)[\\[Image not found: {_escape_typst(raw_path)}\\]]\n'

    source_path = os.path.abspath(source_path)
    image_path = ctx.image_snapshots.get(source_path)
    if not image_path:
        return f'\n#text(fill: muted-color)[\\[Image not staged: {_escape_typst(raw_path)}\\]]\n'

    inner = "#align(center)[\n"
    inner += f'  #image("{_escape_typst_string(image_path)}", width: 100%)\n'
    inner += "]\n"

    caption = item.get("caption", "")
    if caption:
        inner += f"#caption-text[{_html_to_typst(caption)}]\n"

    typ = "\n#figure-block[\n"
    typ += inner
    typ += "]\n"
    return typ


def _block_figure(item: dict, ctx: TypstContext) -> str:
    ctx.figure_n += 1
    raw_cap = item.get("caption", "")
    cap = f"Figure {ctx.figure_n}: {raw_cap}" if raw_cap else f"Figure {ctx.figure_n}"
    return _block_image({**item, "caption": cap}, ctx)


def _block_code(item: dict, ctx: TypstContext) -> str:
    _close_lists(ctx)
    code_text = item.get("text", "")
    language  = item.get("language", "")

    # Wrap long lines to prevent overflow (approx 80 chars fits A4 with margins)
    max_line = 80
    wrapped_lines = []
    for line in code_text.split("\n"):
        while len(line) > max_line:
            # Try to break at last space before limit
            brk = line.rfind(" ", 0, max_line)
            if brk <= 0:
                brk = max_line
            wrapped_lines.append(line[:brk])
            line = line[brk:].lstrip(" ") if brk == line.rfind(" ", 0, max_line) else line[brk:]
        wrapped_lines.append(line)
    code_text = "\n".join(wrapped_lines)

    ticks = "```"
    while ticks in code_text:
        ticks += "`"

    lang_spec = language if language and language != "text" else ""
    raw_block = f"{ticks}{lang_spec}\n{code_text}\n{ticks}"

    # Styling is defined in preamble.typ via #let code-block(code, lang)
    lang_arg = f', lang: "{_escape_typst(language)}"' if language and language != "text" else ""
    typ = f"\n#code-block({raw_block}{lang_arg})\n"
    return typ


def _auto_break_long_math(typst_expr: str, threshold: int = 200) -> str:
    """Insert Typst math line breaks (\\\\) at natural points for long exprs.

    Break priority:
        1.  Top-level  `` = ``  (align with  ``&=``)
        2.  Top-level  `` + ``  /  `` - ``

    "Top-level" means nesting depth == 0  (not inside ``()``, ``{}``,
    ``[]``, or quoted strings).  The algorithm is recursive: after one
    split, each segment is checked again so very long formulas can get
    multiple breaks.
    """
    if len(typst_expr) <= threshold:
        return typst_expr

    # ── collect candidate break positions ──────────────────────────
    candidates = []          # (position, priority, token_str)
    depth = 0
    in_str = False
    i = 0
    while i < len(typst_expr):
        c = typst_expr[i]
        if c == '"':
            in_str = not in_str
        elif not in_str:
            if c in '({[':
                depth += 1
            elif c in ')}]':
                depth = max(0, depth - 1)
            elif depth == 0:
                if typst_expr[i:i + 3] == ' = ':
                    candidates.append((i, 1, ' = '))
                elif typst_expr[i:i + 3] == ' + ':
                    candidates.append((i, 2, ' + '))
                elif typst_expr[i:i + 3] == ' - ' and i > 0:
                    candidates.append((i, 2, ' - '))
                elif typst_expr[i] in (',', '*'): 
                    candidates.append((i, 3, typst_expr[i]))
        i += 1

    if not candidates:
        return typst_expr

    # ── pick the best single break point ───────────────────────────
    # Prefer highest priority (lowest number), then closest to middle.
    mid = len(typst_expr) // 2
    candidates.sort(key=lambda x: (x[1], abs(x[0] - mid)))
    pos, prio, tok = candidates[0]

    lhs = typst_expr[:pos]
    op = tok.strip()                       # '=' / '+' / '-' / ',' / '*'
    rhs = typst_expr[pos + len(tok):]

    # Recursively break the rhs if still too long
    rhs_broken = _auto_break_long_math(rhs, threshold)

    # Use plain line break (no &) so each line is independently centered
    joined = f"{lhs} \\\n{op} {rhs_broken}"

    return joined


def _block_math(item: dict, ctx: TypstContext) -> str:
    """Native Typst math — vector output, full LaTeX support."""
    _close_lists(ctx)
    expr    = item.get("text", "").strip()
    label   = item.get("label", "").strip()
    caption = item.get("caption", "")

    typst_expr = _latex_to_typst_math(expr)
    typst_expr = _auto_break_long_math(typst_expr)

    typ = "\n#v(4pt)\n"

    if label:
        typ += "#grid(\n"
        typ += "  columns: (1fr, auto),\n"
        typ += "  align: (center, right + horizon),\n"
        typ += f"  fit-math[$ {typst_expr} $],\n"
        typ += f'  text(size: 9pt, fill: muted-color)[{_escape_typst(label)}],\n'
        typ += ")\n"
    else:
        typ += f"#fit-math[$ {typst_expr} $]\n"

    if caption:
        typ += f"#caption-text[{_html_to_typst(caption)}]\n"

    typ += "#v(4pt)\n"
    return typ


def _block_chart(item: dict, ctx: TypstContext) -> str:
    """Native Typst chart via cetz canvas."""
    _close_lists(ctx)
    chart_type = item.get("chart_type", "bar")
    accent = ctx.tokens["accent"]
    columns = int(ctx.tokens.get("columns", 1))

    if chart_type == "line":
        chart_code = _chart_line(item, accent, columns)
    elif chart_type == "pie":
        chart_code = _chart_pie(item, accent, columns)
    else:
        chart_code = _chart_bar(item, accent, columns)

    if not chart_code:
        return '\n#text(fill: muted-color)[\\[Chart: no data\\]]\n'

    raw_cap = item.get("caption", "")
    use_fig = item.get("figure", True)
    inner = chart_code + "\n"
    if raw_cap or use_fig:
        ctx.figure_n += 1
        prefix = f"Figure {ctx.figure_n}: " if use_fig else ""
        inner += f"#caption-text[{_escape_typst(prefix)}{_html_to_typst(raw_cap)}]\n"

    typ = "\n#figure-block[\n"
    typ += inner
    typ += "]\n"
    return typ


def _block_flowchart(item: dict, ctx: TypstContext) -> str:
    """Native Typst flowchart via fletcher diagram."""
    _close_lists(ctx)
    fc_code = _flowchart_typst(item, ctx.tokens)

    if not fc_code:
        return '\n#text(fill: muted-color)[\\[Flowchart: no nodes\\]]\n'

    raw_cap = item.get("caption", "")
    use_fig = item.get("figure", True)
    inner = fc_code + "\n"
    if raw_cap or use_fig:
        ctx.figure_n += 1
        prefix = f"Figure {ctx.figure_n}: " if use_fig else ""
        inner += f"#caption-text[{_escape_typst(prefix)}{_html_to_typst(raw_cap)}]\n"

    typ = "\n#figure-block[\n"
    typ += inner
    typ += "]\n"
    return typ


def _block_bibliography(item: dict, ctx: TypstContext) -> str:
    _close_lists(ctx)
    heading = item.get("title", "References")
    typ = ""

    if heading:
        text = _html_to_typst(heading)
        typ += f"\n= {text}\n"

    for ref in item.get("items", []):
        ref_id = _escape_typst(str(ref.get("id", "")))
        ref_text = _escape_typst(ref.get("text", ""))
        typ += (f"#par(hanging-indent: 28pt)[\n"
                f'  #text(size: 8.5pt, weight: "bold", fill: dark)'
                f"[\\[{ref_id}\\]] "
                f"#text(size: 9pt)[{ref_text}]\n"
                f"]\n")

    return typ


def _block_divider(item: dict, ctx: TypstContext) -> str:
    _close_lists(ctx)
    return "\n#accent-divider()\n"


def _block_caption(item: dict, ctx: TypstContext) -> str:
    _close_lists(ctx)
    text = _html_to_typst(item.get("text", ""))
    return f"\n#caption-text[{text}]\n"


def _block_pagebreak(item: dict, ctx: TypstContext) -> str:
    _close_lists(ctx)
    return "\n#pagebreak()\n"


def _block_spacer(item: dict, ctx: TypstContext) -> str:
    pt = item.get("pt", 12)
    return f"\n#v({pt}pt)\n"


# ── Block dispatch ────────────────────────────────────────────────────────────

_RESETS_NUMBERED = frozenset({
    "h1", "h2", "h3", "h4", "h5", "h6", "body", "bullet", "callout", "table",
    "image", "figure", "code", "math", "chart", "flowchart",
    "bibliography", "divider", "caption", "pagebreak", "spacer",
})


# ══════════════════════════════════════════════════════════════════════════════
# Main build pipeline
# ══════════════════════════════════════════════════════════════════════════════

def build(tokens: dict, content: list, out_path: str, project_root: str) -> dict:
    project_root = os.path.abspath(project_root)
    build_dir = os.path.join(project_root, "build")
    exports_dir = os.path.join(project_root, "exports")
    os.makedirs(build_dir, exist_ok=True)
    os.makedirs(exports_dir, exist_ok=True)

    requested_out = os.path.abspath(out_path)
    try:
        common = os.path.commonpath([project_root, requested_out])
    except ValueError:
        common = ""
    output_inside_project = os.path.normcase(common) == os.path.normcase(project_root)
    compile_out = requested_out if output_inside_project else os.path.join(exports_dir, os.path.basename(requested_out))

    image_snapshots = _snapshot_images(content, project_root, build_dir)
    ctx = TypstContext(tokens, project_root, image_snapshots)

    typ = _build_preamble(tokens)
    typ += "// ── Document content ──────────────────────────────────────\n\n"

    for item in content:
        kind = item.get("type", "body")

        if kind in _RESETS_NUMBERED and kind != "numbered":
            ctx.in_numbered = False
            ctx.numbered_n = 0

        if kind in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(kind[1])
            typ += _block_h(item, ctx, level)
        elif kind == "body":
            typ += _block_body(item, ctx)
        elif kind == "bullet":
            typ += _block_bullet(item, ctx)
        elif kind == "numbered":
            typ += _block_numbered(item, ctx)
        elif kind == "callout":
            typ += _block_callout(item, ctx)
        elif kind == "table":
            typ += _block_table(item, ctx)
        elif kind == "image":
            typ += _block_image(item, ctx)
        elif kind == "figure":
            typ += _block_figure(item, ctx)
        elif kind == "code":
            typ += _block_code(item, ctx)
        elif kind == "math":
            typ += _block_math(item, ctx)
        elif kind == "chart":
            typ += _block_chart(item, ctx)
        elif kind == "flowchart":
            typ += _block_flowchart(item, ctx)
        elif kind == "bibliography":
            typ += _block_bibliography(item, ctx)
        elif kind == "divider":
            typ += _block_divider(item, ctx)
        elif kind == "caption":
            typ += _block_caption(item, ctx)
        elif kind == "pagebreak":
            typ += _block_pagebreak(item, ctx)
        elif kind == "spacer":
            typ += _block_spacer(item, ctx)

    typ_path = os.path.join(build_dir, "body.typ")
    with open(typ_path, "w", encoding="utf-8") as f:
        f.write(typ)

    os.makedirs(os.path.dirname(compile_out), exist_ok=True)
    font_dirs = [d for d in tokens.get("font_dirs", []) if os.path.isdir(d)]

    # Always include the bundled SourceHanSerifCN font directory next to
    # this script as fallback.  tokens.json stores absolute paths from
    # when palette.py ran, which may differ from the render environment.
    # Only add the CJK font directory actually used — do NOT add unused
    # font dirs (e.g. fangsong) as Typst may pick them as fallback.
    _bundled_cjk = os.path.join(_SCRIPT_DIR, "fonts", "SourceHanSerifCN")
    if os.path.isdir(_bundled_cjk) and os.path.normcase(_bundled_cjk) not in [os.path.normcase(d) for d in font_dirs]:
        font_dirs.append(_bundled_cjk)

    try:
        typst_compiler.compile(
            typ_path,
            output=compile_out,
            root=project_root,
            font_paths=font_dirs,
        )
    except Exception as e:
        return {
            "status": "error",
            "error": f"Typst compilation failed: {e}",
            "typ_path": typ_path,
        }

    if not os.path.exists(compile_out):
        return {"status": "error", "error": "Typst produced no output PDF"}

    if not output_inside_project:
        os.makedirs(os.path.dirname(requested_out), exist_ok=True)
        shutil.copy2(compile_out, requested_out)

    size = os.path.getsize(requested_out)
    return {
        "status": "ok",
        "out": requested_out,
        "project_out": compile_out,
        "typ_path": typ_path,
        "assets": len(image_snapshots),
        "size_kb": size // 1024,
    }


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Render body PDF from tokens.json + content.json via pure Typst"
    )
    parser.add_argument("--tokens",  default="tokens.json")
    parser.add_argument("--content", default="content.json")
    parser.add_argument("--out",     default="body.pdf")
    args = parser.parse_args()

    for fpath in (args.tokens, args.content):
        if not os.path.exists(fpath):
            print(
                json.dumps({"status": "error",
                            "error": f"File not found: {fpath}"}),
                file=sys.stderr,
            )
            sys.exit(1)

    with open(args.tokens,  encoding="utf-8") as f:
        tokens = json.load(f)
    with open(args.content, encoding="utf-8") as f:
        content = json.load(f)

    try:
        project_root = os.path.dirname(os.path.abspath(args.content))
        result = build(tokens, content, args.out, project_root)
        if result["status"] == "error":
            print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
            sys.exit(3)
        print(json.dumps(result))
    except Exception as e:
        import traceback
        print(
            json.dumps({
                "status": "error",
                "error": str(e),
                "trace": traceback.format_exc(),
            }),
            file=sys.stderr,
        )
        sys.exit(3)


if __name__ == "__main__":
    main()
