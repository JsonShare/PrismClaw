"""Identifier preservation rules — single source of truth.

Moved here from ``hardening.py`` and ``schema/config.py`` to eliminate
dual maintenance (the old comment "compaction.__init__ → kernel cycle"
is resolved by making this a pure-constants module with no imports).

Two prior copies had drifted in wording:
- hardening.py had SDD W8/W12 semantic anchors
- config.py had section-10/section-11 formatting anchors + mutual-exclusivity rule

Merge takes the union of both.
"""

IDENTIFIER_PRESERVATION_INSTRUCTIONS = (
    "在摘要中逐字保留标识符：UUID、哈希、ID、主机名、IP、端口、URL、"
    "文件名、文件路径、模型名、会话 ID、提交 SHA、错误码。不得重写、缩写或改写这些标记。"
    "\n\n"
    "用户陈述的事实必须在压缩后继续存在（SDD context-governance W8）："
    "用户处境的每项事实都是持久上下文——数字、日期、截止时间、"
    "性能阈值（如 P99 < 500ms）、团队规模、角色与风格偏好、过往事件、"
    "业务规则、技术专有名词。每项逐字带入摘要第 10 节。"
    "既有摘要中的每项用户事实必须出现在新摘要中——压缩链一旦丢弃某项，它就永久丢失。"
    "\n\n"
    "已取代的需求必须作为作废项保留（SDD context-governance W12）："
    "用户改变主意时（更正、撤回、取消、替换），旧值不是当前上下文，但不能删除。"
    "列在第 11 节，每条一行：作废事实 → 替代它的内容（取消时写 → 无）。"
    "这是禁止清单：其中内容绝不能再次作为当前事实出现。"
    "既有摘要中的作废条目必须逐字继承——丢失会在后续生成中复活。"
    "某项事实只出现在第 10 节或第 11 节其中一处，不能同时出现。"
)
