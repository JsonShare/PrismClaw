"""医疗域原始文件存储 —— 落在用户的默认工作区内。

布局：<workspaces>/web_{user_id}/default/uploads/{report_id}/{filename}

为什么这么设计：
  * 业务上传与「系统新任务」共用同一个工作区根——后者本来就在
    ~/.prismclaw/workspaces/web_{user_id}/default 下干活，上传落在这里，
    用户在工作区文件树里看得见、域内与通用工具读得到同一处。
    （此前落在 <项目根>/var/medical/uploads/...，属源码树内的运行时产物。）
  * 按 user 分目录：web_{user_id} 即用户边界，配合接口层的 create_by 校验，
    越权读取在结构上不成立（目录隔离 + 归属判断两道独立约束）。
  * 按 report_id 分目录：同一报告多次上传不互相覆盖，也便于整报告级清理。

只做一件事：把上传字节落到稳定路径并返回该路径。不做 OCR、不做分类。
"""
from __future__ import annotations

import re
from pathlib import Path

from prismclaw.infra.workspace_paths import resolve_workspace_inputs_dir

# 只允许这些字符，其余全部替换为下划线（防目录穿越与跨平台非法名）
_UNSAFE = re.compile(r"[^A-Za-z0-9_.一-鿿-]")


def _safe(segment: str, fallback: str) -> str:
    cleaned = _UNSAFE.sub("_", (segment or "").strip())
    # 防止 "." / ".." 这类纯点段被当成相对路径
    return cleaned.strip(".") or fallback


def resolve_upload_dir(user_id: str, report_id: str) -> Path:
    """返回该报告的上传目录（不创建）。落点：默认工作区的 inputs/<report_id>。"""
    base = resolve_workspace_inputs_dir(user_id)
    return base / _safe(report_id, "report")


def save_upload_bytes(user_id: str, report_id: str, filename: str, data: bytes) -> str:
    """写入上传字节，返回落盘后的绝对路径。

    同名文件加序号后缀，避免覆盖（不同报告有独立目录，同目录重名只可能来自重复上传）。
    """
    target_dir = resolve_upload_dir(user_id, report_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    base = _safe(Path(filename or "").name, "file")
    path = target_dir / base
    if path.exists():
        stem, dot, ext = base.rpartition(".")
        for n in range(1, 100):
            candidate = target_dir / (f"{stem}-{n}.{ext}" if dot else f"{base}-{n}")
            if not candidate.exists():
                path = candidate
                break

    path.write_bytes(data)
    return str(path)
