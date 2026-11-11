"""进程级生命周期状态：区分「预期停机取消」与「运行期故障」。

为什么需要单独一层：
* ``asyncio`` 的取消没有类型区分——``CancelledError`` 既可能是用户/服务
  正常停机，也可能是运行期异常路径。上层若把它一律记成「hook 崩溃」，
  停机期的正常取消就会伪装成故障（日志里 13 次 *Hook execution failed*
  即此）。
* 停机编排（assembly）与执行内核（engine）不在同一层，不能互相 import。
  把「是否正在停机」放进最底层的 ``infra``，两边只依赖事实，不依赖实现。

状态是**进程级单向闩锁**：一旦进入停机不再回滚（同一进程不会重新启动
服务生命周期）。测试可用 :func:`reset_for_tests` 复位。
"""
from __future__ import annotations

_shutting_down: bool = False


def mark_shutting_down() -> None:
    """进入停机阶段（幂等）。"""
    global _shutting_down
    _shutting_down = True


def is_shutting_down() -> bool:
    """当前进程是否已进入停机阶段。"""
    return _shutting_down


def reset_for_tests() -> None:
    """仅测试用：复位闩锁。生产代码不得调用。"""
    global _shutting_down
    _shutting_down = False


__all__ = ["mark_shutting_down", "is_shutting_down", "reset_for_tests"]
