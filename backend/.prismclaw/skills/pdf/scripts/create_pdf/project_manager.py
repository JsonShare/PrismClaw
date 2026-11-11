#!/usr/bin/env python3
"""
project_manager.py — PDF project lifecycle manager.

Manages the creation, validation, and inspection of PDF generation projects.
All intermediate files are stored in a structured working directory.

Subcommands:
    init             Create a new PDF project working directory
    import-sources   Import source documents into the project
    validate         Validate project structure and content.json
    info             Display project metadata and status

Usage:
    python3 project_manager.py init <project_name> --type report --dir .prismclaw
    python3 project_manager.py import-sources <project_path> <source_files...>
    python3 project_manager.py validate <project_path>
    python3 project_manager.py info <project_path>

Exit codes: 0 success, 1 bad args, 2 dep missing, 3 runtime error
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

# ── Imports ───────────────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from config import Config, DOC_TYPES, BLOCK_TYPES, PIP_MIRROR

# ── Source Import Constants ───────────────────────────────────────────────────

SOURCE_TO_MD_DIR = Path(SCRIPT_DIR) / "source_to_md"

PDF_SUFFIXES = {".pdf"}
PPT_SUFFIXES = {".pptx", ".pptm", ".ppsx", ".ppsm", ".potx", ".potm"}
DOC_SUFFIXES = {
    ".docx", ".doc", ".odt", ".rtf", ".epub",
    ".html", ".htm", ".tex", ".latex", ".rst", ".org", ".ipynb", ".typ",
}
TEXT_SUFFIXES = {".md", ".markdown", ".txt"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tiff"}
JSON_SUFFIXES = {".json"}

SUPPORTED_SUFFIXES = PDF_SUFFIXES | PPT_SUFFIXES | DOC_SUFFIXES | TEXT_SUFFIXES | IMAGE_SUFFIXES | JSON_SUFFIXES

# ── Constants ─────────────────────────────────────────────────────────────────

# 引擎层在 bash tool / container_backend.py 统一注入的路径 env。
# 技能只读这些 env，零自解析；最后 fallback 到相对路径（脚本被手动直接执行时）。
import os as _os

DEFAULT_BASE_DIR = _os.getenv("PRISMCLAW_SKILL_CACHE", ".prismclaw")

PROJECT_SUBDIRS = ["sources", "exports", "assets/images", "build"]


# ── Project Manager ───────────────────────────────────────────────────────────

class ProjectManager:
    """Manages PDF project lifecycle."""

    def __init__(self, project_dir=None):
        self.project_dir = project_dir

    # ── init ──────────────────────────────────────────────────────────────────

    def init_project(self, name, doc_type, base_dir=DEFAULT_BASE_DIR):
        """
        Create a new PDF project directory with standard structure.

        Only creates the workspace — does NOT generate design tokens.
        Call palette.py separately to generate tokens.json.
        """
        uid = uuid.uuid4().hex[:8]
        date_str = datetime.now().strftime("%Y%m%d")
        project_id = f"pdf-{name}-{date_str}-{uid}"
        project_dir = os.path.join(base_dir, project_id)
        os.makedirs(project_dir, exist_ok=True)

        # Create subdirectories
        for subdir in PROJECT_SUBDIRS:
            os.makedirs(os.path.join(project_dir, subdir), exist_ok=True)

        # Write project metadata
        metadata = {
            "id": project_id,
            "name": name,
            "type": doc_type,
            "created_at": datetime.now().isoformat(),
            "status": "initialized",
        }
        meta_path = os.path.join(project_dir, "project.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

        self.project_dir = project_dir
        print(json.dumps({
            "status": "ok",
            "project_dir": project_dir,
            "project_id": project_id,
        }, ensure_ascii=False, indent=2))
        return project_dir

    # ── import-sources ────────────────────────────────────────────────────────

    def _run_tool(self, cmd: list[str]) -> str:
        """Run a subprocess tool, return stdout. Raises on failure."""
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Tool failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr}"
            )
        return proc.stdout

    def _import_pdf(self, src: Path, md_path: Path) -> None:
        self._run_tool([
            sys.executable,
            str(SOURCE_TO_MD_DIR / "pdf_to_md.py"),
            str(src), "-o", str(md_path),
        ])

    def _import_doc(self, src: Path, md_path: Path) -> None:
        self._run_tool([
            sys.executable,
            str(SOURCE_TO_MD_DIR / "doc_to_md.py"),
            str(src), "-o", str(md_path),
        ])

    def _import_presentation(self, src: Path, md_path: Path) -> None:
        self._run_tool([
            sys.executable,
            str(SOURCE_TO_MD_DIR / "ppt_to_md.py"),
            str(src), "-o", str(md_path),
        ])

    def _safe_filename(self, path: Path, used: set[str]) -> str:
        """Generate a unique filename avoiding collisions."""
        name = path.name
        if name not in used:
            used.add(name)
            return name
        stem, suffix = path.stem, path.suffix
        i = 1
        while True:
            candidate = f"{stem}_{i}{suffix}"
            if candidate not in used:
                used.add(candidate)
                return candidate
            i += 1

    def import_sources(self, inputs):
        """
        Import source documents and normalize them into source_document.md.
        Supports: .md, .txt, .pdf, .json, .docx, .pptx, and common image formats.
        """
        if not self.project_dir:
            print("Error: project path is required", file=sys.stderr)
            sys.exit(1)

        project_path = Path(self.project_dir).resolve()
        sources_dir = project_path / "sources"
        assets_dir = project_path / "assets" / "images"
        sources_dir.mkdir(parents=True, exist_ok=True)
        assets_dir.mkdir(parents=True, exist_ok=True)

        used_source_names = {p.name for p in sources_dir.iterdir()} if sources_dir.exists() else set()
        used_asset_names = {p.name for p in assets_dir.iterdir()} if assets_dir.exists() else set()

        sections: list[str] = []
        source_entries: list[dict] = []
        image_entries: list[dict] = []
        warnings: list[str] = []

        for raw_input in inputs:
            input_path = Path(raw_input).expanduser().resolve()
            if not input_path.exists():
                warnings.append(f"Source not found: {raw_input}")
                continue
            if not input_path.is_file():
                warnings.append(f"Source is not a file: {raw_input}")
                continue

            ext = input_path.suffix.lower()
            if ext not in SUPPORTED_SUFFIXES:
                warnings.append(f"Unsupported source skipped: {raw_input}")
                continue

            source_name = self._safe_filename(input_path, used_source_names)
            copied_path = sources_dir / source_name
            shutil.copy2(input_path, copied_path)

            entry = {
                "source": str(input_path),
                "copied_to": f"sources/{source_name}",
                "type": ext.lstrip(".") or "unknown",
            }

            try:
                if ext in PDF_SUFFIXES:
                    md_path = sources_dir / f"{copied_path.stem}.md"
                    self._import_pdf(copied_path, md_path)
                    if md_path.exists():
                        md_text = md_path.read_text(encoding='utf-8').strip()
                        sidecar = sources_dir / f"{copied_path.stem}_files"
                        if sidecar.is_dir():
                            for img in sorted(sidecar.rglob("*")):
                                if img.is_file():
                                    asset_name = self._safe_filename(img, used_asset_names)
                                    shutil.copy2(img, assets_dir / asset_name)
                                    image_entries.append({"source": str(input_path), "asset": f"assets/images/{asset_name}"})
                                    md_text = md_text.replace(f"{sidecar.name}/{img.name}", f"assets/images/{asset_name}")
                        sections.append(f"\n\n<!-- Source: {input_path.name} -->\n\n{md_text}\n")
                        entry["normalized_markdown"] = f"sources/{md_path.name}"
                    else:
                        warnings.append(f"PDF conversion produced no output: {raw_input}")

                elif ext in DOC_SUFFIXES:
                    md_path = sources_dir / f"{copied_path.stem}.md"
                    self._import_doc(copied_path, md_path)
                    if md_path.exists():
                        md_text = md_path.read_text(encoding='utf-8').strip()
                        sidecar = sources_dir / f"{copied_path.stem}_files"
                        if sidecar.is_dir():
                            for img in sorted(sidecar.rglob("*")):
                                if img.is_file():
                                    asset_name = self._safe_filename(img, used_asset_names)
                                    shutil.copy2(img, assets_dir / asset_name)
                                    image_entries.append({"source": str(input_path), "asset": f"assets/images/{asset_name}"})
                                    # Rewrite image path in markdown
                                    md_text = md_text.replace(f"{sidecar.name}/{img.name}", f"assets/images/{asset_name}")
                        sections.append(f"\n\n<!-- Source: {input_path.name} -->\n\n{md_text}\n")
                        entry["normalized_markdown"] = f"sources/{md_path.name}"
                    else:
                        warnings.append(f"Document conversion produced no output: {raw_input}")

                elif ext in PPT_SUFFIXES:
                    md_path = sources_dir / f"{copied_path.stem}.md"
                    self._import_presentation(copied_path, md_path)
                    if md_path.exists():
                        md_text = md_path.read_text(encoding='utf-8').strip()
                        sidecar = sources_dir / f"{copied_path.stem}_files"
                        if sidecar.is_dir():
                            for img in sorted(sidecar.rglob("*")):
                                if img.is_file():
                                    asset_name = self._safe_filename(img, used_asset_names)
                                    shutil.copy2(img, assets_dir / asset_name)
                                    image_entries.append({"source": str(input_path), "asset": f"assets/images/{asset_name}"})
                                    md_text = md_text.replace(f"{sidecar.name}/{img.name}", f"assets/images/{asset_name}")
                        sections.append(f"\n\n<!-- Source: {input_path.name} -->\n\n{md_text}\n")
                        entry["normalized_markdown"] = f"sources/{md_path.name}"
                    else:
                        warnings.append(f"Presentation conversion produced no output: {raw_input}")

                elif ext in TEXT_SUFFIXES:
                    text = copied_path.read_text(encoding="utf-8").strip()
                    if ext == ".txt":
                        sections.append(f"\n\n# {input_path.name}\n\n{text}\n")
                    else:
                        sections.append(f"\n\n<!-- Source: {input_path.name} -->\n\n{text}\n")
                    entry["normalized_markdown"] = f"sources/{source_name}"

                elif ext in IMAGE_SUFFIXES:
                    asset_name = self._safe_filename(input_path, used_asset_names)
                    asset_path = assets_dir / asset_name
                    shutil.copy2(input_path, asset_path)
                    sections.append(f"\n\n![{input_path.name}](assets/images/{asset_name})\n")
                    entry["asset"] = f"assets/images/{asset_name}"
                    image_entries.append({"source": str(input_path), "asset": f"assets/images/{asset_name}"})

                elif ext in JSON_SUFFIXES:
                    text = copied_path.read_text(encoding="utf-8").strip()
                    sections.append(f"\n\n<!-- Source: {input_path.name} -->\n\n```json\n{text}\n```\n")
                    entry["normalized_markdown"] = f"sources/{source_name}"

            except Exception as e:
                warnings.append(f"Conversion failed for {raw_input}: {e}")

            source_entries.append(entry)

        # Write source_document.md
        source_document_path = project_path / "source_document.md"
        source_text = "".join(sections).strip()
        if source_text:
            source_document_path.write_text(source_text + "\n", encoding="utf-8")

        # Write source_manifest.json
        manifest = {
            "version": 1,
            "created_at": datetime.now().isoformat(),
            "project_dir": str(project_path),
            "source_document": "source_document.md" if source_document_path.exists() else None,
            "sources": source_entries,
            "images": image_entries,
            "warnings": warnings,
        }
        manifest_path = project_path / "source_manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        result = {
            "status": "ok" if source_text else "warning",
            "project_dir": str(project_path),
            "source_document": manifest["source_document"],
            "source_manifest": "source_manifest.json",
            "source_count": len(source_entries),
            "image_count": len(image_entries),
            "warnings": warnings,
        }

        # Update project.json
        meta_path = os.path.join(self.project_dir, "project.json")
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)
            metadata["sources"] = source_entries
            metadata["source_document"] = result.get("source_document")
            metadata["source_manifest"] = result.get("source_manifest")
            metadata["status"] = "sources_imported"
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)

        print(json.dumps(result, ensure_ascii=False, indent=2))

    # ── validate ──────────────────────────────────────────────────────────────

    def validate_project(self):
        """Validate project structure and content.json if present."""
        if not self.project_dir:
            print("Error: project path is required", file=sys.stderr)
            sys.exit(1)

        errors = []
        warnings = []

        # Check directory exists
        if not os.path.isdir(self.project_dir):
            errors.append(f"Project directory not found: {self.project_dir}")
            print(json.dumps({"status": "error", "errors": errors}))
            sys.exit(1)

        # Check project.json
        meta_path = os.path.join(self.project_dir, "project.json")
        if not os.path.exists(meta_path):
            errors.append("project.json not found")
        else:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            if meta.get("type") not in DOC_TYPES:
                warnings.append(f"Unknown doc type: {meta.get('type')}")

        # Check tokens.json
        tokens_path = os.path.join(self.project_dir, "tokens.json")
        if not os.path.exists(tokens_path):
            errors.append("tokens.json not found — run palette.py to generate")

        source_document_path = os.path.join(self.project_dir, "source_document.md")
        source_manifest_path = os.path.join(self.project_dir, "source_manifest.json")
        if os.path.exists(source_manifest_path):
            try:
                with open(source_manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
                if manifest.get("source_document") and not os.path.exists(source_document_path):
                    warnings.append("source_manifest.json references missing source_document.md")
                for warning in manifest.get("warnings", []):
                    warnings.append(f"Source Document: {warning}")
            except json.JSONDecodeError as e:
                errors.append(f"source_manifest.json is invalid JSON: {e}")

        # Check content.json (optional at this stage)
        content_path = os.path.join(self.project_dir, "content.json")
        if os.path.exists(content_path):
            try:
                with open(content_path, "r", encoding="utf-8") as f:
                    content = json.load(f)
                if not isinstance(content, list):
                    errors.append("content.json must be a JSON array")
                else:
                    for i, block in enumerate(content):
                        if not isinstance(block, dict):
                            errors.append(f"Block {i}: must be an object")
                            continue
                        btype = block.get("type")
                        if btype not in BLOCK_TYPES:
                            warnings.append(f"Block {i}: unknown type '{btype}'")
                        if btype in ("image", "figure"):
                            image_path = block.get("path", block.get("src", ""))
                            if not image_path:
                                warnings.append(f"Block {i}: image/figure has no path or src")
                            else:
                                candidate = Path(str(image_path))
                                if not candidate.is_absolute():
                                    candidate = Path(self.project_dir) / candidate
                                if not candidate.exists():
                                    warnings.append(f"Block {i}: image not found: {image_path}")
                        # Check for Chinese quotes
                        for key, val in block.items():
                            if isinstance(val, str):
                                for ch in '\u201c\u201d\u2018\u2019\u300c\u300d\u300e\u300f':
                                    if ch in val:
                                        errors.append(
                                            f"Block {i}, field '{key}': contains Chinese quote '{ch}' — "
                                            f"use escaped English double quotes instead"
                                        )
                                        break
            except json.JSONDecodeError as e:
                errors.append(f"content.json is invalid JSON: {e}")

        status = "ok" if not errors else "error"
        result = {"status": status, "errors": errors, "warnings": warnings}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if errors:
            sys.exit(1)

    # ── info ──────────────────────────────────────────────────────────────────

    def get_project_info(self):
        """Display project metadata and file status."""
        if not self.project_dir:
            print("Error: project path is required", file=sys.stderr)
            sys.exit(1)

        meta_path = os.path.join(self.project_dir, "project.json")
        if not os.path.exists(meta_path):
            print(json.dumps({"status": "error", "message": "Not a valid PDF project"}))
            sys.exit(1)

        with open(meta_path, "r", encoding="utf-8") as f:
            metadata = json.load(f)

        # Check what files exist
        files = {}
        for fname in [
            "tokens.json",
            "content.json",
            "project.json",
            "source_document.md",
            "source_manifest.json",
        ]:
            fpath = os.path.join(self.project_dir, fname)
            files[fname] = os.path.exists(fpath)

        # Check sources
        sources_dir = os.path.join(self.project_dir, "sources")
        source_files = []
        if os.path.isdir(sources_dir):
            source_files = os.listdir(sources_dir)

        # Check exports
        exports_dir = os.path.join(self.project_dir, "exports")
        export_files = []
        if os.path.isdir(exports_dir):
            export_files = os.listdir(exports_dir)

        result = {
            "status": "ok",
            "project_dir": self.project_dir,
            "metadata": metadata,
            "files": files,
            "sources": source_files,
            "exports": export_files,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PDF Project Manager")
    sub = parser.add_subparsers(dest="command")

    # init
    p_init = sub.add_parser("init", help="Create a new PDF project")
    p_init.add_argument("name", help="Project name (used in directory naming)")
    p_init.add_argument("--type", required=True, choices=DOC_TYPES, help="Document type")
    p_init.add_argument("--dir", default=DEFAULT_BASE_DIR, help="Base directory for projects")

    # import-sources
    p_import = sub.add_parser("import-sources", help="Import source documents")
    p_import.add_argument("project", help="Project directory path")
    p_import.add_argument("inputs", nargs="+", help="Source files to import")

    # validate
    p_val = sub.add_parser("validate", help="Validate project structure")
    p_val.add_argument("project", help="Project directory path")

    # info
    p_info = sub.add_parser("info", help="Show project info")
    p_info.add_argument("project", help="Project directory path")

    args = parser.parse_args()

    if args.command == "init":
        pm = ProjectManager()
        pm.init_project(name=args.name, doc_type=args.type, base_dir=args.dir)
    elif args.command == "import-sources":
        pm = ProjectManager(project_dir=args.project)
        pm.import_sources(args.inputs)
    elif args.command == "validate":
        pm = ProjectManager(project_dir=args.project)
        pm.validate_project()
    elif args.command == "info":
        pm = ProjectManager(project_dir=args.project)
        pm.get_project_info()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

