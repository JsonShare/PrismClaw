"""自动生成 — 由 scripts/compile/domains-registry.mjs 从 domains.yaml 生成。
域启用列表 + 能力开关聚合表 | ⚠ 请勿手动修改！"""

# 编译期裁剪后的启用域列表（domains.yaml enabled，可被 PRISMCLAW_DOMAINS override）。
# 后端 _lifespan 据此调用 domain_registry.load_enabled(...)，安装不再等于启用。
ENABLED_DOMAINS = []

# 每域能力开关：{"tools": {name: bool}, "skills": {name: bool}}
# 未列出的工具/技能默认启用（opt-out）；此处仅记录 domains.yaml 中显式声明的项。
# 遍历所有声明过的域（含禁用域）：显式声明的 false 不因域被禁用/override 而丢失。
CAPABILITY_FILTERS = {
}
