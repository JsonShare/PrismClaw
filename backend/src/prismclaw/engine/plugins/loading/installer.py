"""插件远程安装。

对标参考实现 ``pluginInstallationHelpers.ts`` / ``installPluginFromMarketplace``。

支持来源：
  - ``github:owner/repo`` 或 ``owner/repo`` 或 ``https://github.com/owner/repo``
  - ``git+https://...`` / ``git:https://...``（任意 git 仓库）
  - ``url:https://.../archive.zip``（zip 归档）

流程：解析 spec → 安全门控（源策略 + 文件夹信任）→ 拉取到 versioned 缓存目录
（对齐参考实现 ``copyPluginToVersionedCache``）→ 校验 ``plugin.json``
（``PluginManifest`` pydantic）→ 写入 ``InstalledPluginsStore``（source=local）。
落盘后由既有 ``PluginDiscoveryEngine.resolve_install_path`` 接入发现流程，无需
改动 discovery 内部结构。

安全：
  - git/url 拉取仅到本地 versioned 目录，不执行远程代码。
  - zip 解压复用 ``prismclaw.skills.installer._extract_zip``（含 zip-slip 防护）。
  - 未信任工作区拒绝安装 UNTRUSTED 来源（local/session）插件。
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from prismclaw.engine.plugins.registry.identity import LOCAL_SOURCE, PluginID
from prismclaw.engine.plugins.loading.install_store import InstalledPluginsStore
from prismclaw.engine.plugins.loading.load_result import ManifestValidationError
from prismclaw.engine.plugins.manifest import PluginManifest
from prismclaw.engine.plugins.trust import (
    is_source_allowed_by_policy,
    is_source_trusted_for_load,
)

logger = logging.getLogger(__name__)


class PluginInstallError(Exception):
    """插件安装失败。"""


@dataclass(slots=True)
class PluginSourceSpec:
    """归一化后的插件来源描述。"""

    kind: str  # github | git | url
    url: str
    name: str
    ref: str | None = None


def _repo_name(url: str) -> str:
    name = url.rstrip("/").split("/")[-1]
    for suffix in (".git", ".zip"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name or "plugin"


def _github_spec(repo: str, ref: str | None) -> PluginSourceSpec:
    if repo.startswith("http"):
        url = repo if repo.endswith(".git") else repo + ".git"
        name = _repo_name(url)
    else:
        owner, _, name = repo.partition("/")
        if not name:
            raise ValueError(f"Invalid github spec (need owner/repo): {repo!r}")
        url = f"https://github.com/{owner}/{name}.git"
    return PluginSourceSpec("github", url, name, ref)


def parse_source_spec(spec: str, *, ref: str | None = None) -> PluginSourceSpec:
    """把用户给定的来源描述解析为归一化 ``PluginSourceSpec``。"""
    raw = spec.strip()
    if not raw:
        raise ValueError("Empty plugin source spec")
    if raw.startswith("github:"):
        return _github_spec(raw[len("github:"):].strip(), ref)
    if raw.startswith("git+") or raw.startswith("git:"):
        rest = raw.split(":", 1)[1].strip()
        return PluginSourceSpec("git", rest, _repo_name(rest), ref)
    if raw.startswith("url:"):
        rest = raw[len("url:"):].strip()
        return PluginSourceSpec("url", rest, _repo_name(rest), ref)
    if raw.startswith("http://") or raw.startswith("https://"):
        if "github.com" in raw:
            return _github_spec(raw, ref)
        return PluginSourceSpec("url", raw, _repo_name(raw), ref)
    if "/" in raw:
        return _github_spec(raw, ref)
    raise ValueError(f"Unrecognized plugin source spec: {spec!r}")


def _versioned_target(root: Path, name: str, version: str) -> Path:
    safe = name.replace("/", "_")
    return root / safe / (version or "latest")


async def _git_clone(url: str, target: Path, *, ref: str | None) -> str:
    cmd = ["git", "clone", "--depth", "1"]
    if ref:
        cmd += ["--branch", ref]
    cmd += [url, str(target)]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"git clone failed: {stderr.decode(errors='replace')[:400]}")
    sha_proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(target), "rev-parse", "HEAD",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await sha_proc.communicate()
    if sha_proc.returncode != 0:
        return ""
    return out.decode().strip()


async def _download_zip(
    url: str,
    target: Path,
    *,
    zip_extract_fn: Any,
    zip_verify_fn: Any,
) -> None:
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60.0), follow_redirects=True,
        headers={"User-Agent": "PrismClaw-PluginInstaller/1.0"},
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.content
    zip_verify_fn(data, None)
    zip_extract_fn(data, target)


def _validate_manifest(target: Path) -> None:
    manifest_path = target / ".plugin" / "plugin.json"
    if not manifest_path.exists():
        manifest_path = target / "plugin.json"
    if not manifest_path.exists():
        raise PluginInstallError(f"No plugin.json found in fetched plugin at {target}")
    try:
        PluginManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - surface as install error
        # pydantic ValidationError 优先转结构化 ManifestValidationError,
        # invalid_fields 直接复用 ValidationError.errors() 原生 list[dict]
        # (含 loc/msg/type 三键, 对齐方案 §2.11.1), 方便下游消费。
        validation_err: ManifestValidationError | None = None
        try:
            from pydantic import ValidationError as _PydanticValidationError

            if isinstance(exc, _PydanticValidationError):
                validation_err = ManifestValidationError(
                    file_path=str(manifest_path),
                    invalid_fields=list(exc.errors()),
                    message=(
                        f"插件 {target.name} 的清单无效："
                        f"共 {exc.error_count()} 处 schema 违规"
                    ),
                )
        except Exception:  # noqa: BLE001 - fallback if pydantic not importable
            validation_err = None
        if validation_err is not None:
            raise PluginInstallError(validation_err.format_message()) from validation_err
        raise PluginInstallError(
            f"Invalid plugin manifest for {target.name}: {exc}"
        ) from exc


async def install_plugin(
    *,
    spec: str | PluginSourceSpec,
    install_root: Path,
    store: InstalledPluginsStore,
    ref: str | None = None,
    version: str | None = None,
    workspace_trusted: bool = True,
    strict_known_sources: set[str] | None = None,
    blocked_sources: set[str] | None = None,
    zip_extract_fn: Any | None = None,
    zip_verify_fn: Any | None = None,
) -> PluginID:
    """从远程来源安装一个插件到本地，并登记到 ``InstalledPluginsStore``。

    返回安装后的 ``PluginID``（source=local）。调用方需将其加入启用列表
    （``settings.plugins.enabled``）或置于被扫描的本地目录，discovery 即会加载。
    """
    src = spec if isinstance(spec, PluginSourceSpec) else parse_source_spec(spec, ref=ref)

    # 安全门控 1：源策略（企业策略层，显式拦截）。
    if not is_source_allowed_by_policy(
        LOCAL_SOURCE, strict_known=strict_known_sources, blocked=blocked_sources
    ):
        raise PluginInstallError(f"Source '{LOCAL_SOURCE}' blocked by policy")
    # 安全门控 2：文件夹信任（未信任工作区拒绝安装 UNTRUSTED 来源）。
    if not is_source_trusted_for_load(LOCAL_SOURCE, workspace_trusted=workspace_trusted):
        raise PluginInstallError(
            "Workspace is not trusted; refusing to install remote plugin"
        )

    version_key = version or src.ref or "latest"
    target = _versioned_target(install_root, src.name, version_key)
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)

    commit_sha: str | None = None
    try:
        if src.kind in ("github", "git"):
            commit_sha = await _git_clone(src.url, target, ref=src.ref)
            version = version or (commit_sha[:12] if commit_sha else None)
        else:  # url -> zip archive
            if zip_extract_fn is None or zip_verify_fn is None:
                raise PluginInstallError(
                    "zip_extract_fn and zip_verify_fn are required for URL-based plugin installation"
                )
            await _download_zip(
                src.url, target,
                zip_extract_fn=zip_extract_fn,
                zip_verify_fn=zip_verify_fn,
            )
        _validate_manifest(target)
    except Exception as exc:
        shutil.rmtree(target, ignore_errors=True)
        if isinstance(exc, PluginInstallError):
            raise
        raise PluginInstallError(
            f"Failed to install plugin '{src.name}' from {src.url}: {exc}"
        ) from exc

    plugin_id = PluginID(name=src.name, source=LOCAL_SOURCE)
    store.upsert(
        plugin_id,
        install_path=str(target),
        version=version,
        git_commit_sha=commit_sha,
    )
    logger.info("Installed plugin %s -> %s", plugin_id, target)
    return plugin_id
