<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# PRISMCLAW.md

本仓库的工作指南。`legal-chineselaw` 是一个 PrismClaw 插件市场——包含十二个第一方法律插件、一个第三方供应商插件，以及五个托管 Agent Cookbook。此处的工作主要是编辑提示词内容（skills、agents、hooks）、插件元数据或 Cookbook 配置，而非应用代码。

## 目录结构

```
.plugin/marketplace.json   # 市场清单 — 每个插件一条记录
<plugin>/                         # 12 个第一方插件（commercial-legal、privacy-legal……）
  .plugin/plugin.json      # 插件清单（name、version、description、author）
  .mcp.json                       # 插件连接的 MCP 服务器
  PRISMCLAW.md                       # 执业档案模板（详见下方"插件 PRISMCLAW.md"）
  README.md                       # 各插件文档
  skills/<name>/SKILL.md          # 每个目录对应一个 skill
  agents/<name>.md                # 子 Agent 定义
  hooks/hooks.json                # Hook 配置（大多数插件附带空白存根）
  .gitignore
external_plugins/<vendor>/        # 供应商维护的插件（CoCounsel）— ⚠️ 本目录尚未创建，仅供参考
managed-agent-cookbooks/<name>/   # CMA agent.yaml + subagents/ + steering-examples.json
scripts/                          # validate.py、lint-tool-scope.py、orchestrate.py、
                                  # deploy-managed-agent.sh、test-cookbooks.sh
references/                       # 共享模板（company-profile、dashboard）
```

## 验证——提交 PR 前请先运行

本仓库遵循与 `anthropics/claude-plugins-official` 在 CI 中相同的规范。请在本地运行等效检查：

```bash
# 1. 市场 + 各插件 schema 验证（以此为准）
prismclaw plugin validate .plugin/marketplace.json
for d in */; do [ -f "$d/.plugin/plugin.json" ] && prismclaw plugin validate "$d"; done
prismclaw plugin validate external_plugins/cocounsel-legal  # ⚠️ external_plugins/ 目录尚未创建，此命令暂不可用

# 2. Cookbook 工具作用域 lint（编排器不得过度授权工具）
python3 scripts/lint-tool-scope.py

# 3. JSON/YAML 格式校验
python3 -c "import json,glob; [json.load(open(f)) for f in glob.glob('**/*.json', recursive=True)]"
```

### 市场不变量（I1–I11）

`claude-plugins-official` 在 schema 检查之上叠加了以下规则，本仓库同样适用——最容易踩坑的几条：

- **I1** — `plugins[]` 应按 name 字母排序（不区分大小写）。*目前存在一个已知警告：数组按策划的展示顺序排列。如需新增插件，请先询问再重新排序整个数组。*
- **I2** — 插件名称不得重复。
- **I3** — `description` 长度为 10–2000 个字符，首尾不得有空白字符。
- **I8** — 每个供应商 `source`（`"./<dir>"`）必须指向包含 `.plugin/plugin.json` 的目录。
- **I9** — `source` 路径/URL 不得包含 shell 元字符或 `..`。
- **I10** — `name`/`description` 中不得出现隐藏 Unicode 字符（零宽字符、双向控制字符）。
- **I11** — `name` 必须匹配 `^[a-z0-9][a-z0-9-]{1,63}$`。

### Frontmatter 要求

每个 `agents/*.md` 需要 `name` 和 `description`。每个 `skills/<name>/SKILL.md` 需要 `description`。每个 `commands/*.md` 需要 `description`。多行 description 使用 `>` 块标量完全没问题——`prismclaw plugin validate` 能正确解析。

## 约定

### 保持 `marketplace.json` 与 `plugin.json` 同步

对于第一方插件，`marketplace.json` 中的 `name`、`description` 和 `author` 应与插件自身的 `.plugin/plugin.json` 对应字段保持一致。若在一处修改了插件 description，另一处也必须同步修改。

### 散文中的 skill 名称必须使用规范名称

当 `SKILL.md` 或 `commands/*.md` 告知用户"运行 `/foo`"时，`foo` 必须是实际注册的命令名——取自 `commands/*.md` 的 frontmatter `name` 字段，**不是**目录名。

命令名必须全插件包唯一。注册表按名字落键，不同子插件下的同名命令会相互覆盖，只有最后注册的那份生效，其余静默失效。所以通用名字要带子插件前缀：`/aig-use-case-triage`、`/privacy-use-case-triage`，而不是裸的 `/use-case-triage`。像 `/triage` 这样代替 `/aig-use-case-triage` 的缩写看似正确，实际输入后不会有任何响应。

引用技能时要用完整命名：`/<子插件>:<技能名>`（如 `/commercial-legal:nda-review`），裸技能名解析不到。内置命令（`/mcp`、`/plugin`）直接可用。

### 插件 PRISMCLAW.md 是模板，不是项目上下文

每个 `<plugin>/PRISMCLAW.md` 是一个执业档案模板，由 `cold-start-interview` skill 复制到用户机器上的 `~/.prismclaw/plugins/legal-chineselaw/<plugin>/PRISMCLAW.md`。安装插件时，它*不会*作为项目上下文加载——`prismclaw plugin validate` 会对此发出警告，这是预期行为。不要通过将内容移入 skill 来"修复"这个警告。

### `external_plugins/` 由供应商维护

> ⚠️ **注意：`external_plugins/` 目录尚未在本仓库中创建，以下内容为规划预留。**

`external_plugins/` 下的插件由供应商构建和维护（README.md 中有相关政策）。未经与供应商确认，不得修改供应商编写的内容；空白规范化和格式调整通常没问题，因为供应商通过 PR 而非镜像 fork 的方式提交变更。

### 格式规范

- 所有 JSON 和 `.mcp.json` 文件使用 2 空格缩进。
- 每个文本文件末尾必须有换行符。
- 不得有行尾空白字符。
- Markdown 表格：列对齐很好，但不做强制要求；只需保持列数一致即可。

## Cookbook

每个 `managed-agent-cookbooks/<name>/` 包含 `agent.yaml`（编排器）、`subagents/*.yaml`（叶节点）、`steering-examples.json` 和 `README.md`。`scripts/lint-tool-scope.py` 强制执行以下两条规则：

1. 编排器只获得本地工具（`read`、`grep`、`glob`、`agent_toolset`）；MCP 和写入工具归属于特定的子 Agent 叶节点。
2. README 的安全表格和 `agent.yaml` 注释必须与 YAML 实际授予的内容一致。不得声明子 Agent 实际上没有的工具。

## 不要动的内容

- 各插件的 `.gitignore` 文件在不同插件间存在细微差异，可能是有意为之；统一前请先询问。
- 本市场当前没有任何子插件配置 `hooks/hooks.json`。Hook 是可选的，缺失这些文件不是 bug。护栏规则写在各技能正文与 `PRISMCLAW.md` 里，不依赖工具级 hook。
- `references/` 仅存在于仓库根目录，不随任何插件目录一起发布。部分插件的 `PRISMCLAW.md` 模板引用它时好像它在目录内——这是已知的缺口，不要悄悄移动它。
