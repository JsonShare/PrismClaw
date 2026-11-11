#!/usr/bin/env python3
"""
config.py — Unified configuration for the PDF generation pipeline.

Provides centralized access to:
  - Path configuration (scripts, references, fonts, etc.)
  - Document type palettes (15 types)
  - Typography system (font families, type scale, spacing)
  - Content block type registry
  - Dependency registry

Usage:
    from config import Config
    cfg = Config()
    print(cfg.SCRIPTS_DIR)
    print(cfg.PALETTES["report"])
"""

import os

# ── Path Configuration ────────────────────────────────────────────────────────

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS_DIR = os.path.join(SKILL_ROOT, "scripts")
REFERENCES_DIR = os.path.join(SKILL_ROOT, "references")

CREATE_DIR = os.path.join(SCRIPTS_DIR, "create_pdf")
FILL_DIR = os.path.join(SCRIPTS_DIR, "fill_pdf")
PARSE_DIR = os.path.join(SCRIPTS_DIR, "parse_pdf")
TOOLKIT_DIR = os.path.join(SCRIPTS_DIR, "toolkit")

FONTS_DIR = os.path.join(CREATE_DIR, "fonts")
FONT_SERIF_DIR = os.path.join(FONTS_DIR, "SourceHanSerifCN")
FONT_FANGSONG_DIR = os.path.join(FONTS_DIR, "fangsong")

# ── Document Types ────────────────────────────────────────────────────────────

DOC_TYPES = [
    "report", "proposal", "resume", "portfolio", "academic",
    "general", "minimal", "stripe", "diagonal", "frame",
    "editorial", "magazine", "darkroom", "terminal", "poster",
]

# ── Typography ────────────────────────────────────────────────────────────────

FONT_FAMILIES = {
    "fangsong": {
        "regular": "ZhuqueFangsong-Regular.ttf",
        "bold": "ZhuqueFangsong-Regular.ttf",  # fangsong has no bold variant
        "dir": FONT_FANGSONG_DIR,
        "typst_family": "Zhuque Fangsong",
    },
    "serif": {
        "regular": "SourceHanSerifCN-Regular.ttf",
        "bold": "SourceHanSerifCN-Bold.ttf",
        "dir": FONT_SERIF_DIR,
        "typst_family": "Source Han Serif CN",
    },
}

TYPE_SCALE = {
    "h1": {"size": 22, "leading": 1.3},
    "h2": {"size": 15, "leading": 1.4},
    "h3": {"size": 11.5, "leading": 1.5},
    "h4": {"size": 10.5, "leading": 1.5},
    "h5": {"size": 10, "leading": 1.5},
    "h6": {"size": 10, "leading": 1.5},
    "body": {"size": 10.5, "leading": 1.6},
    "caption": {"size": 8.5, "leading": 1.4},
    "meta": {"size": 8, "leading": 1.3},
}

# ── Layout ────────────────────────────────────────────────────────────────────

SPACING = {
    "margin_outer": "2.8cm",
    "margin_top": "2.8cm",
    "margin_bottom": "2.5cm",
    "section_gap": 26,   # pt, space before h1
    "para_gap": 16,      # pt, space after paragraph
    "line_gap": 12,      # pt, body leading
}

# ── Content Block Types ───────────────────────────────────────────────────────

BLOCK_TYPES = [
    "h1", "h2", "h3", "h4", "h5", "h6",
    "body", "bullet", "numbered", "callout",
    "table", "image", "figure", "code", "math",
    "chart", "flowchart", "bibliography",
    "divider", "caption", "pagebreak", "spacer",
]

# ── Dependencies ──────────────────────────────────────────────────────────────

DEPENDENCIES = {
    "create": ["typst"],
    "fill": ["pypdf"],
    "reformat": ["pypdf"],
    "parse": ["pymupdf4llm"],
    "toolkit": ["pypdf", "reportlab", "Pillow", "pymupdf"],
}

PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"


# ── Config class ──────────────────────────────────────────────────────────────

class Config:
    """Static accessor for all PDF skill configuration."""

    SKILL_ROOT = SKILL_ROOT
    SCRIPTS_DIR = SCRIPTS_DIR
    REFERENCES_DIR = REFERENCES_DIR
    CREATE_DIR = CREATE_DIR
    FILL_DIR = FILL_DIR
    PARSE_DIR = PARSE_DIR
    TOOLKIT_DIR = TOOLKIT_DIR
    FONTS_DIR = FONTS_DIR

    DOC_TYPES = DOC_TYPES
    FONT_FAMILIES = FONT_FAMILIES
    TYPE_SCALE = TYPE_SCALE
    SPACING = SPACING
    BLOCK_TYPES = BLOCK_TYPES
    DEPENDENCIES = DEPENDENCIES
    PIP_MIRROR = PIP_MIRROR

    @classmethod
    def export_config(cls):
        """Export config as JSON-serializable dict."""
        return {
            "doc_types": cls.DOC_TYPES,
            "font_families": {k: v["typst_family"] for k, v in cls.FONT_FAMILIES.items()},
            "type_scale": cls.TYPE_SCALE,
            "spacing": cls.SPACING,
            "block_types": cls.BLOCK_TYPES,
        }
