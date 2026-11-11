"""PrismClaw 业务域分包根包。

后端域包统一置于本命名空间下，与前端 `frontend/subpackages/<domain>` 结构对齐：
每个业务域为 `subpackages.<domain>`（如 `subpackages.medical`），通过
pyproject 的 `[project.entry-points."prismclaw.domains"]` 注册为可发现的核心扩展。

运行时 `backend/src` 在 PYTHONPATH 中（见 scripts/lib/common.sh、deploy/templates/build.py），
故 `import subpackages.<domain>` 可直接解析。
"""
