"""启动链路导入冒烟。

测试套件不导入 ``app`` 启动链与 ``runner`` 编排层，模块级 ImportError
只有在真正启动服务时才暴露（曾因再导出缺失导致 uvicorn lifespan 启动失败）。
这里只做导入，不构造任何 app。
"""
from __future__ import annotations


def test_startup_import_chain() -> None:
    import prismclaw.app._lifespan  # noqa: F401
    import prismclaw.app.gateway.takeover.service  # noqa: F401
    import prismclaw.engine.execution.loop.runner  # noqa: F401
    import prismclaw.engine.execution.query.turn_preparation  # noqa: F401
