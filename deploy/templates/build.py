#!/usr/bin/env python3
"""DEF-08: 沙箱模板镜像构建脚本。

从 SandboxTemplate 规格生成 Dockerfile，构建并推送容器镜像。

用法:
    python deploy/templates/build.py --all --push
    python deploy/templates/build.py --template office-v1
    python deploy/templates/build.py --dry-run
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# 确保项目 src 在 sys.path 中（三目录重构后 src 位于 backend/）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "backend" / "src"))

from prismclaw.engine.runtime.template import SandboxTemplate


def generate_dockerfile(template: SandboxTemplate) -> str:
    """从模板规格生成 Dockerfile 内容。"""
    lines = [
        f"FROM {template.base_image}",
        "",
        "RUN apt-get update && apt-get install -y --no-install-recommends \\",
        "    ca-certificates curl python3 python3-pip python3-venv \\",
        "    && rm -rf /var/lib/apt/lists/*",
        "",
    ]

    if template.preinstalled_apt:
        pkgs = " ".join(template.preinstalled_apt)
        lines.append(f"RUN apt-get update && apt-get install -y --no-install-recommends {pkgs} \\")
        lines.append("    && rm -rf /var/lib/apt/lists/*")
        lines.append("")

    if template.preinstalled_pip:
        pkgs = " ".join(template.preinstalled_pip)
        lines.append(f"RUN pip3 install --no-cache-dir --break-system-packages {pkgs}")
        lines.append("")

    if template.preinstalled_npm:
        pkgs = " ".join(template.preinstalled_npm)
        lines.append("RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \\")
        lines.append(f"    && npm install -g {pkgs} \\")
        lines.append("    && rm -rf /var/lib/apt/lists/*")
        lines.append("")

    # 工作目录
    lines.append("WORKDIR /workspace")
    lines.append("")

    return "\n".join(lines)


def build_template_image(
    template: SandboxTemplate,
    push: bool = False,
    dry_run: bool = False,
) -> str:
    """构建模板 Docker 镜像。

    返回 image_tag。
    """
    image_tag = f"prismclaw/{template.template_id}:{template.build_hash}"
    dockerfile = generate_dockerfile(template)

    if dry_run:
        print(f"[dry-run] Template: {template.template_id}")
        print(f"[dry-run] Image tag: {image_tag}")
        print(f"[dry-run] Dockerfile:\n{dockerfile}")
        return image_tag

    # 写入临时 Dockerfile
    build_dir = Path(f"/tmp/prismclaw-template-{template.template_id}")
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / "Dockerfile").write_text(dockerfile)

    # docker build
    cmd = ["docker", "build", "-t", image_tag, str(build_dir)]
    print(f"Building {image_tag}...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: docker build failed for {template.template_id}")
        print(result.stderr)
        sys.exit(1)

    # docker push
    if push:
        cmd = ["docker", "push", image_tag]
        print(f"Pushing {image_tag}...")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"ERROR: docker push failed for {template.template_id}")
            print(result.stderr)
            sys.exit(1)

    return image_tag


def build_tenant_template(tenant_template: "TenantTemplate", base_template: SandboxTemplate, push: bool = False) -> str:
    from prismclaw.engine.runtime.template import TenantTemplate
    image_tag = f"prismclaw/{tenant_template.template_id}:{tenant_template.build_hash or 'latest'}"
    lines = [f"FROM prismclaw/{base_template.template_id}:{base_template.build_hash}", ""]
    if tenant_template.extra_apt:
        pkgs = " ".join(tenant_template.extra_apt)
        lines.append(f"RUN apt-get update && apt-get install -y --no-install-recommends {pkgs} && rm -rf /var/lib/apt/lists/*")
        lines.append("")
    if tenant_template.extra_pip:
        pkgs = " ".join(tenant_template.extra_pip)
        lines.append(f"RUN pip3 install --no-cache-dir --break-system-packages {pkgs}")
        lines.append("")
    if tenant_template.extra_npm:
        pkgs = " ".join(tenant_template.extra_npm)
        lines.append(f"RUN npm install -g {pkgs}")
        lines.append("")
    lines.append("WORKDIR /workspace")
    lines.append("")
    dockerfile = "\n".join(lines)
    # build logic similar to build_template_image...
    return image_tag


def main() -> None:
    parser = argparse.ArgumentParser(description="Build sandbox template images")
    parser.add_argument("--all", action="store_true", help="Build all built-in templates")
    parser.add_argument("--template", type=str, help="Build a specific template by ID")
    parser.add_argument("--push", action="store_true", help="Push images after building")
    parser.add_argument("--dry-run", action="store_true", help="Only print what would be done")
    args = parser.parse_args()

    from prismclaw.engine.runtime.builtin_templates import build_builtin_templates

    templates = build_builtin_templates()

    if args.template:
        tmpl = templates.get(args.template)
        if tmpl is None:
            print(f"Unknown template: {args.template}")
            print(f"Available: {list(templates.keys())}")
            sys.exit(1)
        build_template_image(tmpl, push=args.push, dry_run=args.dry_run)
    elif args.all:
        for tmpl in templates.values():
            build_template_image(tmpl, push=args.push, dry_run=args.dry_run)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
