"""后台子代理任务句柄登记处（P1a：仅单进程）。

``task_id → (TaskManager, managed_task_id)`` 的强引用映射：
- 真实取消：``cancel_task`` 只能通过本映射把计划中的 ``pt:...`` id 转成
  TaskManager 的句柄再 ``cancel()``；映射未命中说明任务不在本进程内，
  必须显式报错，而不是沿用父级 ``managed_task_id``（那会终止整轮父会话）。
- 强引用：``asyncio.Task`` 由 TaskManager 的 ``TaskHandle`` 持有，本模块
  只保存转译所需的句柄，任务结束后立即移除，避免映射无限增长。

多副本限制：该映射是进程内状态。``affinity`` 网关开启时子代理工具不暴露
后台模式（见 ``SubAgentTool``），因此这里不需要跨 worker 语义。
"""

from __future__ import annotations

from typing import Any

_background_handles: dict[str, tuple[Any, str]] = {}


def register_background_handle(
    task_id: str,
    *,
    task_manager: Any,
    managed_task_id: str,
) -> None:
    _background_handles[task_id] = (task_manager, managed_task_id)


def unregister_background_handle(task_id: str) -> None:
    _background_handles.pop(task_id, None)


async def cancel_background_task(task_id: str) -> bool:
    """取消本进程内的后台子代理任务；映射未命中返回 False。"""
    entry = _background_handles.get(task_id)
    if entry is None:
        return False
    task_manager, managed_task_id = entry
    cancelled = bool(await task_manager.cancel(managed_task_id))
    if not cancelled:
        # 任务已结束或已不在 TaskManager 中：句柄已失效，立即清理，
        # 避免在「任务启动前被取消」的窗口里留下永久条目。
        unregister_background_handle(task_id)
    return cancelled


__all__ = [
    "cancel_background_task",
    "register_background_handle",
    "unregister_background_handle",
]
