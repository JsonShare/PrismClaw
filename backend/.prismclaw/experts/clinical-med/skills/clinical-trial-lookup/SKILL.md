---
name: clinical-trial-lookup
name_zh: 临床试验检索
description: 检索 ClinicalTrials.gov 临床试验注册库（API v2，免密钥），按疾病、干预、申办方、地点、状态、期别筛选，并取回单个试验的详情与研究中心分布。适用于为肿瘤、罕见病、慢病患者查找在研试验与入组机会，核对某方案的注册信息，或为 MDT 准备可入组选项。当用户问「有没有相关临床试验」「有哪些在研方案」「这个药做过什么试验」「哪些中心在招」时使用。不替代入组评估与知情同意流程。
category: 临床诊疗
invocation_policy: auto
priority: 8
---

# 临床试验检索

直连 ClinicalTrials.gov API v2，覆盖全球注册试验。**无需 API key**，脚本只用 Python 标准库。

## 硬约束：检索词必须是英文

ClinicalTrials.gov 是英文库，中文病名基本返回空结果。脚本检测到中文会提示。

先把用户的中文表述翻成标准英文病名再检索：

| 中文 | 英文检索词 |
|---|---|
| 非小细胞肺癌 | non-small cell lung cancer |
| 小细胞肺癌 | small cell lung cancer |
| 肝细胞癌 | hepatocellular carcinoma |
| 鼻咽癌 | nasopharyngeal carcinoma |
| 胶质母细胞瘤 | glioblastoma |
| 食管鳞癌 | esophageal squamous cell carcinoma |
| 2 型糖尿病 | type 2 diabetes |
| 慢性肾病 | chronic kidney disease |
| 心力衰竭 | heart failure |
| 质子治疗 | proton beam therapy（**不要只写 proton**） |

**`proton` 单用会命中 "proton pump inhibitor" 和 "Proton MRI"**，必须写 `proton beam therapy` 或 `proton radiotherapy`。同理，缩写容易误命中，优先用全称。

## 用法

在本技能根目录执行：

```bash
# 按疾病 + 状态检索
python scripts/query_trials.py search --condition "proton beam therapy" --status RECRUITING --limit 10

# 按期别 + 干预筛选
python scripts/query_trials.py search --condition "non-small cell lung cancer" --intervention pembrolizumab --phase PHASE3

# 只看中国中心
python scripts/query_trials.py search --condition "nasopharyngeal carcinoma" --location China --status RECRUITING

# 只看已公布结果的
python scripts/query_trials.py search --condition "type 2 diabetes" --with-results --limit 20

# 取单个试验详情
python scripts/query_trials.py detail NCT04613596

# 需要结构化数据时
python scripts/query_trials.py search --condition "glioblastoma" --json
```

参数：

| 参数 | 说明 |
|---|---|
| `--condition` | 疾病/适应症（英文） |
| `--intervention` | 干预或药物名 |
| `--sponsor` | 申办方 |
| `--location` | 地点，如 `China`、`Beijing` |
| `--term` | 自由词全文检索 |
| `--status` | `RECRUITING` / `NOT_YET_RECRUITING` / `ACTIVE_NOT_RECRUITING` / `COMPLETED` / `TERMINATED` 等，可多值 |
| `--phase` | `EARLY_PHASE1` / `PHASE1` / `PHASE2` / `PHASE3` / `PHASE4` / `NA`，可多值 |
| `--with-results` | 仅返回已公布结果的试验 |
| `--limit` | 返回条数，默认 10，上限 200 |
| `--json` | 输出原始 JSON |

## 输出怎么读

默认中文摘要，每条含：NCT 号、状态、期别、入组数、启动日期、适应症、干预、国家分布、中国中心（若有）、申办方、摘要首段。

**中国中心单独一行**：跨国有试验会把 `China（N 个中心）` 排在前面，并列出具体城市，便于判断患者就近入组可能性。

## 限定条件（必须随结果一起说明）

1. **注册 ≠ 可入组**：登记信息可能滞后于实际招募状态，最终以中心确认为准。
2. **覆盖不全**：ClinicalTrials.gov 主要收录美国及跨国注册试验。中国本土试验常在「药物临床试验登记与信息公示平台」与 ChiCTR 注册，**本技能查不到**，涉及国内试验时要额外提示。
3. **期别字段可能缺失**：显示「未标注期别」时不要推断为早期或后期。
4. **摘要为英文原文**：转述给患者时要做通俗化，并保留 NCT 号便于溯源。
5. 检索结果用于**信息参考与转诊线索**，不构成入组建议；入组需由研究团队按方案标准评估。

## 与其它技能的关系

- 病例复盘时判断「是否考虑过试验入组」→ `clinical-case-review`
- 需要按入排标准逐条比对患者资料 → 先 `clinical-note-extract` 抽结构化字段，再人工比对
