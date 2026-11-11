"""Context compression — checkpoint, dedup, fidelity, compaction, and summary.

不再 re-export 子模块符号。所有外部消费者应通过深路径导入，例如::

    from prismclaw.engine.compression.context_engine import DefaultContextEngine
    from prismclaw.engine.compression.preservation import project_preservation_runtime

依赖方向（单向，禁止反向）::

    schema ← foundation/state ← compression.ports ← compression.* ← execution ← assembly/app
"""
