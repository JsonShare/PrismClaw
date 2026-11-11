<!--
本文件是医疗健康插件的工作指南。
-->

# PRISMCLAW.md

本仓库的工作指南。`medical-healthcare` 是一个 PrismClaw 插件市场——包含六个第一方医疗插件，以及两个本地 MCP 服务器（documents、fhir）。此处的工作主要是编辑提示词内容（skills、agents、hooks）、插件元数据或 MCP 配置，而非应用代码。

## 目录结构

```
.plugin/marketplace.json            # 市场清单 — 每个插件一条记录
<plugin>/                           # 6 个第一方插件
  .plugin/plugin.json               # 插件清单（name、version、description、author）
  mcp.json                          # 插件连接的 MCP 服务器
  PRISMCLAW.md                      # 执业档案模板（详见下方"插件 PRISMCLAW.md"）
  README.md                         # 各插件文档
  skills/<name>/SKILL.md            # 每个目录对应一个 skill
  agents/<name>.md                  # 子 Agent 定义
  commands/<name>.md                # 命令定义
  hooks/hooks.json                  # Hook 配置
  .gitignore
mcp_servers/                        # 本地 MCP 服务器
  documents/                        # 合同分析引擎（Node.js）
  fhir/                             # FHIR R4 连接器（Node.js）
  shared/                           # 共享模块
safety/                             # 安全验证脚本（Python）
references/                         # 共享模板（company-profile、dashboard）
```

## 插件列表

| 插件 | 说明 | 核心技能 |
|------|------|---------|
| medical-clinical | 临床医生工作台 | 临床评估、处方安全、ICD-10 编码、操作编码、临床笔记提取 |
| medical-compliance | 医疗合规 | 欺诈检测、事前授权、HIPAA 合规、数据隐私 |
| medical-operations | 医疗运营 | 保险理赔、记录管理、预约管理、缺口分析 |
| medical-research | 医学科研 | 临床试验方案、文献综述、统计分析 |
| medical-informatics | 医疗信息学 | FHIR R4 集成、FHIR 开发、文档解析 |
| medical-contracts | 医疗合同 | 合同分析、批量扫描、溯源引用 |

## 数据路径约定

所有插件级数据写入 `~/.prismclaw/data/healthcare/<component-name>/`，永远不写入插件安装路径（升级时会被覆盖）。通过 `PRISMCLAW_HEALTHCARE_DATA` 环境变量可以覆盖根目录。

- **documents 服务器**：`~/.prismclaw/data/healthcare/documents/`
- **fhir 服务器**：服务器状态存储在临时目录
- **fraud-detection 技能**：`~/.prismclaw/data/healthcare/fraud-detection/`

## MCP 服务器

### 本地服务器（stdio 模式）

- **Documents MCP 服务器**：`mcp_servers/documents/src/index.mjs` — 合同问答引擎
- **FHIR MCP 服务器**：`mcp_servers/fhir/src/index.mjs` — FHIR R4 连接器

### 远程服务器（HTTP 模式）

以下远程 MCP 服务器需要 PrismClaw 运行时支持 `type: http` 才能使用：

- CMS Coverage：`https://hcls.mcp.claude.com/cms_coverage/mcp`
- ICD10 Codes：`https://hcls.mcp.claude.com/icd10_codes/mcp`
- NPI Registry：`https://hcls.mcp.claude.com/npi_registry/mcp`
- Clinical Trials：`https://hcls.mcp.claude.com/clinical_trials/mcp`
- PubMed：`https://pubmed.mcp.claude.com/mcp`

## 验证——提交 PR 前请先运行

本仓库遵循与 `anthropics/claude-plugins-official` 在 CI 中相同的规范。请在本地运行等效检查：

```bash
# 1. 市场 + 各插件 schema 验证
prismclaw plugin validate .plugin/marketplace.json
for d in */; do [ -f "$d/.plugin/plugin.json" ] && prismclaw plugin validate "$d"; done

# 2. Cookbook 工具作用域 lint
python3 scripts/lint-tool-scope.py

# 3. JSON/YAML 格式校验
python3 -c "import json,glob; [json.load(open(f)) for f in glob.glob('**/*.json', recursive=True)]"
```

## 约定

### 保持 `marketplace.json` 与 `plugin.json` 同步

对于第一方插件，`marketplace.json` 中的 `name`、`description` 和 `author` 应与插件自身的 `.plugin/plugin.json` 对应字段保持一致。

### 插件 PRISMCLAW.md 是模板，不是项目上下文

每个 `<plugin>/PRISMCLAW.md` 是一个执业档案模板，由该子插件的冷启动命令（`<前缀>-cold-start-interview`）复制到用户机器上的配置路径。安装插件时，它不会作为项目上下文加载。

### 格式规范

- 所有 JSON 和 `.mcp.json` 文件使用 2 空格缩进。
- 每个文本文件末尾必须有换行符。
- 不得有行尾空白字符。

## Cookbook

每个 `managed-agent-cookbooks/<name>/` 包含 `agent.yaml`（编排器）、`subagents/*.yaml`（叶节点）、`steering-examples.json` 和 `README.md`。`scripts/lint-tool-scope.py` 强制执行以下两条规则：

1. 编排器只获得本地工具（`read`、`grep`、`glob`、`agent_toolset`）；MCP 和写入工具归属于特定的子 Agent 叶节点。
2. README 的安全表格和 `agent.yaml` 注释必须与 YAML 实际授予的内容一致。