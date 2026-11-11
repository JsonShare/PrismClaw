#!/usr/bin/env python3
"""
palette.py — Infer design tokens from document metadata.

Usage:
    python3 palette.py --title "AI Trends 2025" --type report --out tokens.json
    python3 palette.py --title "John Doe Resume" --type resume --out tokens.json
    python3 palette.py --meta meta.json --out tokens.json

Outputs tokens.json consumed by render_body_typst_native.py.
CJK fonts (SourceHanSerifCN / ZhuqueFangsong) are bundled in scripts/fonts/.
Exit codes: 0 success, 1 bad args, 3 write error
"""

import argparse
import json
import os
import re
import sys

# ── Skill root path ───────────────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SKILL_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
_PALETTES_DIR = os.path.join(_SKILL_ROOT, "assets", "templates", "palettes")

# ── Palette library ────────────────────────────────────────────────────────────
# Built-in palettes (fallback if external JSON files don't exist)
_BUILTIN_PALETTES = {
    "report": {
        "accent": "#3B6D8A",
        "accent_lt": "#E6EFF5",
        "page_bg": "#FAFAF8",
        "dark": "#1A1E24",
        "body_text": "#2C2C30",
        "muted": "#7A7A84",
        "mood": "authoritative",
    },
    "proposal": {
        "accent": "#4E6070",
        "accent_lt": "#EAECEE",
        "page_bg": "#FAFAF7",
        "dark": "#18191E",
        "body_text": "#28282E",
        "muted": "#7A7870",
        "mood": "confident",
    },
    "resume": {
        "accent": "#1C3557",
        "accent_lt": "#E8EEF5",
        "page_bg": "#FFFFFF",
        "dark": "#111111",
        "body_text": "#222222",
        "muted": "#888888",
        "mood": "clean",
    },
    "portfolio": {
        "accent": "#6A7A88",
        "accent_lt": "#EAECEE",
        "page_bg": "#F8F8F8",
        "dark": "#18191E",
        "body_text": "#28282E",
        "muted": "#8A8A96",
        "mood": "expressive",
    },
    "academic": {
        "accent": "#2A436A",
        "accent_lt": "#E6EBF4",
        "page_bg": "#F5F4F0",
        "dark": "#1A1A28",
        "body_text": "#1E1E2A",
        "muted": "#686877",
        "mood": "scholarly",
    },
    "general": {
        "accent": "#4A6070",
        "accent_lt": "#E6EAEC",
        "page_bg": "#F8F6F2",
        "dark": "#1A1A1A",
        "body_text": "#2C2C2C",
        "muted": "#888888",
        "mood": "neutral",
    },
    "minimal": {
        "accent": "#4A4A4A",
        "accent_lt": "#EBEBEA",
        "page_bg": "#F7F6F4",
        "dark": "#111111",
        "body_text": "#222222",
        "muted": "#999999",
        "mood": "restrained",
    },
    "stripe": {
        "accent": "#4A5568",
        "accent_lt": "#EAECEE",
        "page_bg": "#F8F8F7",
        "dark": "#0E1117",
        "body_text": "#262630",
        "muted": "#888898",
        "mood": "bold",
    },
    "diagonal": {
        "accent": "#3D5A72",
        "accent_lt": "#E4EBF0",
        "page_bg": "#F8FAFC",
        "dark": "#0F1A2A",
        "body_text": "#1E2C3A",
        "muted": "#7A8A96",
        "mood": "dynamic",
    },
    "frame": {
        "accent": "#5C4A38",
        "accent_lt": "#EAE5DE",
        "page_bg": "#F5F2EC",
        "dark": "#2A1E14",
        "body_text": "#2C2018",
        "muted": "#9A8A78",
        "mood": "classical",
    },
    "editorial": {
        "accent": "#7A2B36",
        "accent_lt": "#EEE4E5",
        "page_bg": "#FFFFFF",
        "dark": "#0A0A0A",
        "body_text": "#1A1A1A",
        "muted": "#777777",
        "mood": "editorial",
    },
    "magazine": {
        "accent": "#1C3557",
        "accent_lt": "#E4EBF3",
        "page_bg": "#F0EEE9",
        "dark": "#0D1A2B",
        "body_text": "#2A2A2A",
        "muted": "#888888",
        "mood": "magazine",
    },
    "darkroom": {
        "accent": "#3D5A7A",
        "accent_lt": "#E2EBF2",
        "page_bg": "#F7F7F5",
        "dark": "#0A1018",
        "body_text": "#2C2C2C",
        "muted": "#8A9AB0",
        "mood": "darkroom",
    },
    "terminal": {
        "accent": "#3D7A5C",
        "accent_lt": "#E2EEE8",
        "page_bg": "#F8F8F6",
        "dark": "#010409",
        "body_text": "#2C2C2C",
        "muted": "#5A7A6A",
        "mood": "terminal",
    },
    "poster": {
        "accent": "#0A0A0A",
        "accent_lt": "#EBEBEA",
        "page_bg": "#FFFFFF",
        "dark": "#0A0A0A",
        "body_text": "#1A1A1A",
        "muted": "#888888",
        "mood": "poster",
    },
}


def _load_external_palettes():
    """Load palette JSON files from assets/templates/palettes/."""
    if not os.path.isdir(_PALETTES_DIR):
        return {}
    external = {}
    for fname in os.listdir(_PALETTES_DIR):
        if fname.endswith(".json") and not fname.startswith("_"):
            name = fname[:-5]
            try:
                with open(os.path.join(_PALETTES_DIR, fname), encoding="utf-8") as f:
                    external[name] = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass  # skip invalid files
    return external


# Merge: external files override built-in (external is source of truth)
PALETTES = {**_BUILTIN_PALETTES, **_load_external_palettes()}

# ── Font pairs — legacy names (kept for backward compat, not used by Typst) ──
FONT_PAIRS = {
    "authoritative": {"display_rl": "Times-Bold", "body_rl": "Helvetica", "body_b_rl": "Helvetica-Bold"},
    "confident":     {"display_rl": "Times-Bold", "body_rl": "Helvetica", "body_b_rl": "Helvetica-Bold"},
    "clean":         {"display_rl": "Times-Bold", "body_rl": "Helvetica", "body_b_rl": "Helvetica-Bold"},
    "expressive":    {"display_rl": "Times-Bold", "body_rl": "Helvetica", "body_b_rl": "Helvetica-Bold"},
    "scholarly":     {"display_rl": "Times-Bold", "body_rl": "Helvetica", "body_b_rl": "Helvetica-Bold"},
    "neutral":       {"display_rl": "Times-Bold", "body_rl": "Helvetica", "body_b_rl": "Helvetica-Bold"},
    "restrained":    {"display_rl": "Times-Bold", "body_rl": "Helvetica", "body_b_rl": "Helvetica-Bold"},
    "bold":          {"display_rl": "Times-Bold", "body_rl": "Helvetica", "body_b_rl": "Helvetica-Bold"},
    "dynamic":       {"display_rl": "Times-Bold", "body_rl": "Helvetica", "body_b_rl": "Helvetica-Bold"},
    "classical":     {"display_rl": "Times-Bold", "body_rl": "Helvetica", "body_b_rl": "Helvetica-Bold"},
    "editorial":     {"display_rl": "Times-Bold", "body_rl": "Helvetica", "body_b_rl": "Helvetica-Bold"},
    "magazine":      {"display_rl": "Times-Bold", "body_rl": "Helvetica", "body_b_rl": "Helvetica-Bold"},
    "darkroom":      {"display_rl": "Times-Bold", "body_rl": "Helvetica", "body_b_rl": "Helvetica-Bold"},
    "terminal":      {"display_rl": "Courier-Bold", "body_rl": "Courier", "body_b_rl": "Courier-Bold"},
    "poster":        {"display_rl": "Times-Bold", "body_rl": "Courier", "body_b_rl": "Courier-Bold"},
}

SYSTEM_FALLBACK = {
    "display_rl": "Times-Bold",
    "body_rl": "Helvetica",
    "body_b_rl": "Helvetica-Bold",
}

# ── CJK body fonts (local TTF in scripts/fonts/) ─────────────────────────────
# SourceHanSerifCN and ZhuqueFangsong are shipped locally with TrueType outlines.
# Typst registers them by font directory at render time (see render_body_typst_native.py).
_FONTS_ROOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
# _FONTS_ROOT_DIR = "/usr/share/fonts/truetype/dumate/"
_CJK_FONTS_DIR = os.path.join(_FONTS_ROOT_DIR, "SourceHanSerifCN")
_FANGSONG_DIR = os.path.join(_FONTS_ROOT_DIR, "fangsong")

_CJK_FONT_FILES = {
    "SourceHanSerifCN-Regular": "SourceHanSerifCN-Regular.ttf",
    "SourceHanSerifCN-Bold": "SourceHanSerifCN-Bold.ttf",
}

_FANGSONG_FONT_FILES = {
    "ZhuqueFangsong": "ZhuqueFangsong-Regular.ttf",
}

# Supported CJK font families and their metadata
CJK_FONT_FAMILIES = {
    "serif": {
        "dir": _CJK_FONTS_DIR,
        "files": _CJK_FONT_FILES,
        "body": "SourceHanSerifCN-Regular",
        "bold": "SourceHanSerifCN-Bold",
        "display": "SourceHanSerifCN-Bold",
        "typst_family": '"Source Han Serif CN"',
        "typst_fallback": '"Noto Serif CJK SC", "Songti SC", serif',
        "css_family": '"Source Han Serif CN", "Noto Serif CJK SC", "Songti SC", serif',
    },
    "fangsong": {
        "dir": _FANGSONG_DIR,
        "files": _FANGSONG_FONT_FILES,
        "body": "ZhuqueFangsong",
        "bold": "ZhuqueFangsong",       # 仿宋无粗体，复用 Regular
        "display": "ZhuqueFangsong",
        "typst_family": '"Zhuque Fangsong (technical preview)"',
        "typst_fallback": '"FangSong", "Zhuque Fangsong (technical preview)", serif',
        "css_family": '"Zhuque Fangsong (technical preview)", "FangSong", serif',
    },
}


def _resolve_font_paths(font_family: str = "serif") -> dict:
    """Return font_paths dict for the given CJK font family.

    Parameters
    ----------
    font_family : str
        One of the keys in ``CJK_FONT_FAMILIES`` (default ``"serif"``).

    Returns
    -------
    dict
        ``{"FontName": {"path": "...", "index": 0}, ...}``
        Only includes entries for files that actually exist on disk.
    """
    family = CJK_FONT_FAMILIES.get(font_family, CJK_FONT_FAMILIES["serif"])
    font_dir = family["dir"]
    paths: dict = {}
    for name, filename in family["files"].items():
        fpath = os.path.join(font_dir, filename)
        if os.path.isfile(fpath):
            paths[name] = {"path": fpath, "index": 0}
        else:
            print(f"[palette] WARNING: CJK font not found: {fpath}", file=sys.stderr)
    return paths


# ── Colour helpers ──────────────────────────────────────────────────────────────
def _hex_to_rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _lighten(hex_color: str, factor: float = 0.09) -> str:
    """Blend hex_color toward white (factor = accent weight, 0=white, 1=full color)."""
    r, g, b = _hex_to_rgb(hex_color)
    return "#{:02X}{:02X}{:02X}".format(
        round(r * factor + 255 * (1 - factor)),
        round(g * factor + 255 * (1 - factor)),
        round(b * factor + 255 * (1 - factor)),
    )


def _decode_escaped_unicode(value: str) -> str:
    if not isinstance(value, str) or not re.search(r"\\u[0-9a-fA-F]{4}", value):
        return value
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return value


# ── Token assembly ─────────────────────────────────────────────────────────────
def build_tokens(
        title: str,
        doc_type: str,
        author: str = "",
        date: str = "",
        accent_override: str = "",
        font_family: str = "serif",
        lang: str = "zh",
) -> dict:
    palette = PALETTES.get(doc_type, PALETTES["general"]).copy()
    mood = palette["mood"]
    font_pair = FONT_PAIRS.get(mood, SYSTEM_FALLBACK)

    if accent_override:
        palette["accent"] = accent_override
        palette["accent_lt"] = _lighten(accent_override, 0.09)

    # ── Font selection by language ───────────────────────────────────────────
    if lang == "en":
        # English: use system fonts (Helvetica / Times New Roman)
        display_rl = font_pair["display_rl"]
        body_rl = font_pair["body_rl"]
        body_b_rl = font_pair["body_b_rl"]
        font_paths: dict = {}
        font_dirs: list[str] = []
        heading_font_typst = '"Times New Roman"'
        body_font_typst = '"Helvetica"'
        css_family = '"Helvetica", Arial, sans-serif'
        code_css = '"Courier New", Courier, monospace'
    else:
        # Chinese: use local bundled CJK TTF files
        family_info = CJK_FONT_FAMILIES.get(font_family, CJK_FONT_FAMILIES["serif"])
        cjk_paths = _resolve_font_paths(font_family)
        if cjk_paths:
            display_rl = family_info["display"]
            body_rl = family_info["body"]
            body_b_rl = family_info["bold"]
            font_paths = cjk_paths
        else:
            display_rl = font_pair["display_rl"]
            body_rl = font_pair["body_rl"]
            body_b_rl = font_pair["body_b_rl"]
            font_paths = {}

        has_cjk = bool(cjk_paths)
        heading_font_typst = family_info["typst_family"] if has_cjk else family_info["typst_fallback"]
        body_font_typst = family_info["typst_family"] if has_cjk else family_info["typst_fallback"]
        font_dirs = [d for d in [family_info["dir"]] if os.path.isdir(d)]
        css_family = family_info["css_family"]
        code_css = '"Courier New", Courier, monospace'

    tokens = {
        # Identity
        "title": title,
        "author": author,
        "date": date,
        "doc_type": doc_type,

        # Palette
        "accent": palette["accent"],
        "accent_lt": palette["accent_lt"],
        "page_bg": palette["page_bg"],
        "dark": palette["dark"],
        "body_text": palette["body_text"],
        "muted": palette["muted"],
        "mood": mood,

        # Typography — legacy keys (kept for backward compat)
        "font_display_rl": display_rl,
        "font_body_rl": body_rl,
        "font_body_b_rl": body_b_rl,

        # Specialised font roles (legacy)
        "font_code_rl": body_rl,
        "font_label_rl": "Helvetica",

        # CSS font-family names (legacy)
        "font_body_css": css_family,
        "font_code_css": code_css,

        # Legacy keys (kept for backward compat)
        "font_heading": display_rl,
        "font_body_b": body_b_rl,

        # TTF registration paths — only populated for fonts that were located
        "font_paths": font_paths,

        # Typst font family names (used by render_body_typst_native.py)
        "heading_font_typst": heading_font_typst,
        "body_font_typst": body_font_typst,

        # Font directories for Typst compiler
        "font_dirs": font_dirs,

        # Document language (used by renderer for typographic rules)
        "lang": lang,

        # Type scale (pt)
        "size_display": 54,
        "size_h1": 22,
        "size_h2": 15,
        "size_h3": 11.5,
        "size_body": 10.5,
        "size_caption": 8.5,
        "size_meta": 8,

        # Layout (pt, 1cm ≈ 28.35pt)
        "margin_left": 79,  # 2.8cm
        "margin_right": 79,
        "margin_top": 79,
        "margin_bottom": 71,  # 2.5cm
        "section_gap": 26,
        "para_gap": 16,
        "line_gap": 12,
    }
    return tokens


# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Generate design tokens from document metadata")
    parser.add_argument("--title", default="Untitled Document")
    parser.add_argument("--type", default="general",
                        help="Document type: " + ", ".join(PALETTES.keys()))
    parser.add_argument("--author", default="")
    parser.add_argument("--date", default="")
    parser.add_argument("--meta", help="JSON file with title/type/author/date keys")
    parser.add_argument("--accent", default="",
                        help="Override accent colour (hex, e.g. #2D6A8F). "
                             "accent_lt is auto-derived by lightening toward white.")
    parser.add_argument("--font", default="fangsong",
                        choices=list(CJK_FONT_FAMILIES.keys()),
                        help="CJK font family: " + ", ".join(CJK_FONT_FAMILIES.keys()))
    parser.add_argument("--lang", default="zh", choices=["zh", "en"],
                        help="Document language: zh=CJK fonts, en=Latin fonts (Helvetica/Times)")
    parser.add_argument("--columns", type=int, default=1,
                        choices=[1, 2],
                        help="Page column layout: 1 (single, default) or 2 (dual)")
    parser.add_argument("--layout", default="default",
                        help="Page layout template name (from assets/templates/layouts/)")
    parser.add_argument("--no-cover", action="store_true",
                        help="Omit the cover page")
    parser.add_argument("--no-header", action="store_true",
                        help="Omit the running page header and footer")
    parser.add_argument("--list-types", action="store_true",
                        help="List all available document types and exit")
    parser.add_argument("--out", default="tokens.json")
    args = parser.parse_args()

    # --list-types: print available types and exit
    if args.list_types:
        builtin = list(_BUILTIN_PALETTES.keys())
        external = [k for k in PALETTES.keys() if k not in _BUILTIN_PALETTES]
        print(json.dumps({
            "builtin": builtin,
            "external": external,
            "all": list(PALETTES.keys()),
        }, ensure_ascii=False, indent=2))
        sys.exit(0)

    if args.meta:
        try:
            with open(args.meta, encoding="utf-8") as f:
                meta = json.load(f)
            args.title = meta.get("title", args.title)
            args.type = meta.get("type", args.type)
            args.author = meta.get("author", args.author)
            args.date = meta.get("date", args.date)
        except Exception as e:
            print(json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False), file=sys.stderr)
            sys.exit(1)

    args.title = _decode_escaped_unicode(args.title)
    args.author = _decode_escaped_unicode(args.author)
    args.date = _decode_escaped_unicode(args.date)

    tokens = build_tokens(
        args.title, args.type, args.author, args.date,
        accent_override=args.accent,
        font_family=args.font,
        lang=args.lang,
    )
    tokens["columns"] = args.columns
    tokens["layout"] = args.layout
    tokens["show_cover"] = not args.no_cover
    tokens["show_header"] = not args.no_header

    try:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(tokens, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(3)

    print(json.dumps({
        "status": "ok",
        "out": args.out,
        "mood": tokens["mood"],
        "fonts_rl": f'{tokens["font_display_rl"]} / {tokens["font_body_rl"]}',
        "font_paths_found": list(tokens["font_paths"].keys()),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
