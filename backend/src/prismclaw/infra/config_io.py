"""配置文件读写层：YAML 为默认格式，JSON 作为兼容格式保留。

设计要点：

- **读**：按扩展名分派（``.yaml``/``.yml`` → YAML，其余 → JSON）。
- **写**：YAML 走 ``ruamel.yaml`` round-trip，保留文件内注释与键顺序；
  未安装 ruamel 时回退 PyYAML（注释会丢失，但功能不中断）。
- **原子性**：先写同目录临时文件再 ``rename``，避免写中断留下半截配置。
- **权限**：配置里可能含密钥，落盘统一 ``0600``。
"""

from __future__ import annotations

import io
import json
import os
import tempfile
from pathlib import Path
from typing import Any

YAML_SUFFIXES = (".yaml", ".yml")


def is_yaml(path: Path) -> bool:
    return path.suffix.lower() in YAML_SUFFIXES


def _ruamel_yaml() -> Any | None:
    """构造 ruamel round-trip 实例；未安装返回 None。"""
    try:
        from ruamel.yaml import YAML
    except Exception:  # pragma: no cover - 依赖缺失时的降级路径
        return None
    parser = YAML()
    parser.preserve_quotes = True
    parser.width = 4096
    parser.indent(mapping=2, sequence=4, offset=2)
    return parser


def load_document(path: Path) -> dict[str, Any] | None:
    """读取配置文档；不存在、解析失败或顶层非对象时返回 None。

    YAML 优先用 ruamel 读取，返回 ``CommentedMap``（dict 子类）——
    调用方原地修改后交给 :func:`dump_document` 即可保留注释。
    """
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        if is_yaml(path):
            parser = _ruamel_yaml()
            if parser is not None:
                data = parser.load(text)
            else:  # pragma: no cover
                import yaml

                data = yaml.safe_load(text)
        else:
            data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def dump_document(path: Path, data: dict[str, Any]) -> None:
    """原子写回配置文档（YAML 保留注释，JSON 用缩进格式）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if is_yaml(path):
        parser = _ruamel_yaml()
        if parser is not None:
            buffer = io.StringIO()
            parser.dump(data, buffer)
            content = buffer.getvalue()
        else:  # pragma: no cover
            import yaml

            content = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=4096)
    else:
        content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_cfg_", suffix=path.suffix)
    try:
        with os.fdopen(fd, mode="w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(tmp_path, 0o600)
        Path(tmp_path).replace(path)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise
