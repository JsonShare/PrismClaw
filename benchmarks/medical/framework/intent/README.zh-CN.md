# 个人健康数据管理与医生助手评测

新增集合按 Mirobody 的定位设计：**为医生整理资料，为个人管理自己的健康记录**。
主集合共 **32 题：24 题能力测量、8 题回归控制**，另有 **8 题患者隔离保留集**、
**4 组可靠性对照（8 个 episode）**。原有 `local_models` 100 题保持不变。
数据位于 [`demo/intent/`](../../demo/intent/)，命令在仓库根目录运行。

## 规模与区分度

| 任务 | 能力题 | 回归题 |
| --- | ---: | ---: |
| 28 天的条件统计、次日配对、跨指标分析 | 12 | 0 |
| 用药计划版本、历史日期还原、按需计划和日志边界 | 4 | 0 |
| 多次报告定位、日期角色、稀疏历史比较 | 4 | 2 |
| 可逐项追溯的医生资料摘要 | 4 | 2 |
| 依据原报告参考范围归档结果 | 0 | 2 |
| 家人共享与撤销授权 | 0 | 2 |
| **合计** | **24** | **8** |

分析题包含条件均值、次日对比、完整配对覆盖率、皮尔逊相关、排除最高活动日、
连续达标天数、按实际记录日加权的均值、个人历史基线，共八种运算。
题目明确 Asia/Shanghai 账号日界、滞后方向、缺失处理及并列规则；数值有可执行金标，
不能用泛泛解释或全月均值替代。医生摘要包含两个指标共六次观测，要求数值、
单位、就诊日、来源绑定。简单回归与能力题分别出分，不用容易题抬高主成绩。
这些配额是设计选择，不是用户流量占比。

开发集使用 20 位合成人物，语言为简中 20、英文 11、日文 1；这个小集合不支持
按语言下结论。保留集使用另外十人分配区中的八人，8 题均未交给答题模型。
开发与保留答题包各自唯一且跨集合不重合。保留集按患者隔离，但不对作者保密、
也未按模板隔离，不能称为临床独立测试集。

## 纳入的公开 benchmark 思路

| 一手来源 | 采用内容 | 当前实现 |
| --- | --- | --- |
| [PHIA](https://arxiv.org/abs/2406.06464) | 可执行的个人可穿戴数据分析与时间比较 | 自有合成记录上的统计和加权比较 |
| [LifeAgentBench](https://arxiv.org/abs/2601.13880) | 条件查询、跨生活维度推理 | 按日联结、缺失分母、连续达标 |
| [WearableQA](https://arxiv.org/abs/2609.05405) | 纵向单指标与跨指标数据推理 | 相关、滞后、个人基线；不纳入诊断预测 |
| [HealthBench](https://openai.com/index/healthbench/) | 逐题 rubric、补充上下文、健康资料保真 | 报告核对、资料交接，不引入问诊语料 |

这是在新合成记录上重建任务类型，**没有复制上述原题，也不冒充其官方成绩**。
每题保留参考来源。LifeAgentBench 的使用限制以及 WearableQA 的
[CC BY-NC 4.0](https://github.com/facebookresearch/WearableQA/blob/main/LICENSE)
使直接搬入原始数据不适合产品仓库。
云端《真实用户问题调研-20261024.md》中的报告定位、识别疑问、用药记录、
家人共享、复诊准备提供了意图线索；问诊平台的频次没有被当成 Mirobody 的需求分布。
停药建议、诊断、治疗、症状归因和医患对话不属于本集合主任务。

## 证据与金标

使用新的 mirobody-gen 种子 **20261025** 生成 30 人的合成记录；
[`manifest.json`](../../demo/intent/manifest.json) 固定生成器 commit 和五个源文件的哈希。
每项生成器快照包含 JSONL 行号和字段指针，构建时核对数值及所属人物。
事实含类型、单位、容差和重算规则；另用手工小例子独立验证统计算法。
计划变更按字段继承，包括频次，未来版本不能提前生效；无服药日志不等于漏服。

输入是文本快照，不是原始报告或 OCR 成绩。生成器 `collected` 在此称为
`encounter_date`（就诊日期），不冒充采样时间。发报告日期、OCR 歧义、工具超时
和授权状态属于标注来源的人工控制。隐藏 OCR 正确值、潜在诊断、金标、rubric、
源路径和集合标签不进入答题包。源记录一致性不等于医学真实性，本次未做临床专家
验证或生成器的图像审计。

四组可靠性对照为：两组每日图表输入顺序变化、一组历史证据、一组工具不可用。
图表检查合法 JSON、折线类型、单位、日期、点值与顺序。历史压力要求至少两次
真实原生摘要收据；故障压力要求实际注入收据。编写历史或展示 timeout 消息，
不能证明产品实际完成压缩或故障恢复；没有收据的维度返回 `not_exercised`。

## 校验、重建与外部评分

```bash
python -m benchmarks.intent.run validate
pytest -q mirobody/tests/test_intent_evaluation.py
ruff check benchmarks/intent mirobody/tests/test_intent_evaluation.py

PYTHONPATH=/path/to/mirobody-gen python -m mirobody_gen build \
  --seed 20261025 --people 30 --clinical --out /private/intent-cohort
python -m benchmarks.intent.build \
  --corpus /private/intent-cohort --generator /path/to/mirobody-gen \
  --out demo/intent --held-out /private/intent-held-out.jsonl
```

按 manifest 的生成器 commit 重建可复现文件。保留题答案放在公开目录之外，
当前答题/审查命令不能选择；manifest 只公布数量、人物 family 与哈希。
重建会使作者看到答案，里程碑使用前应约定解封规则。

```bash
# 审查题目质量，不是在评模型答案。
python -m benchmarks.intent.run audit \
  --provider openrouter --model anthropic/claude-opus-5.5 \
  --reasoning-effort medium --max-output-tokens 8192 --timeout-seconds 180 \
  --env-file /private/mirobody.env --out internal/intent-audit

# 全部32题；加 --role capability 可单独选择24题。
python -m benchmarks.intent.run baseline \
  --provider openrouter --model openai/gpt-6-luna \
  --reasoning-effort medium --max-output-tokens 8192 --timeout-seconds 180 \
  --env-file /private/mirobody.env --out internal/intent-answers

python -m benchmarks.intent.run score \
  --provider openrouter --model anthropic/claude-opus-5.5 \
  --reasoning-effort medium --max-output-tokens 8192 --timeout-seconds 180 \
  --env-file /private/mirobody.env \
  --answers internal/intent-answers/answers.jsonl --out internal/intent-score

# 用同一批原答案追加独立裁判。
python -m benchmarks.intent.run score \
  --provider nebula --model deepseek-v4-pro \
  --reasoning-effort high --max-output-tokens 8192 --timeout-seconds 180 \
  --env-file /private/mirobody.env \
  --answers internal/intent-answers/answers.jsonl --out internal/intent-nebula-score
```

`nebula` 绑定固定端点 `https://llm.ai-nebula.com/v1`，读取项目现有环境变量
`NEUBULA_API_KEY`。DeepSeek V4 Pro 显式启用 thinking，并设置 `reasoning_effort`，
不发送采样温度。追加裁判复用原答案，不重新作答；不同裁判的分数分别保留。
参数依据 [Nebula 模型接口文档](https://docs.ai-nebula.com/en/api-reference/endpoint/list-models)
和 [DeepSeek thinking 文档](https://api-docs.deepseek.com/guides/thinking_mode/)。

`--smoke` 选择每类前两题，共 12 题；`--limit` 可再缩小，答题与评分须用同一选择。
`--dataset reliability` 选择八个对照 episode。密钥来自环境或 `--env-file`，
绑定对应服务的固定 HTTPS 端点，不进入结果。结果放在 `internal/` 等忽略目录或仓库外。

`baseline` 直接提供可见证据，未运行产品 prompt、数据库、工具、授权或压缩，
是题目难度校准，不是 harness 成绩。实际产品答案可用 JSONL 提交给 `score`：

```json
{"id":"intent-development-interpretation-01","case_sha256":"<冻结题目的digest>","status":"ok","answer":"实际渲染答案","receipts":{}}
```

`case_sha256` 必填，为完整冻结题目的 `benchmarks.intent.schema.digest(case)`；
旧版本或未绑定题目的答案会在付费调用前失败。产品适配器须绑定合成人物与独立授权测试账号，保留 SSE/工具轨迹，从实际观测生成收据。
回答声称调用了工具，不算执行证据。

外部裁判逐项评 rubric，并引用答案的真实行号、提取原样事实；程序核对完整 ID、
可见证据引用、实际出现的数值、相邻单位、Markdown 表格同列或所属小节标题的单位，再执行单位归一
和容差检查。相邻单位匹配不跨任意行或单元格；显式单位列及相邻的“数值/单位”字段
绑定自己的结果。天数、配对数、相关系数属于无量纲事实。日期的语义归一及事实对应关系仍依赖
可能犯错的 LLM，有争议必须复核，不能把裁判当真值。
缓存绑定完整题目、证据、模型、端点、prompt、参数、评测器版本和执行收据。
原始响应、实际模型身份、用量、答题失败及裁判失败保留；固定分母不缩小，裁判故障
不直接算作产品答错。

比较两份同题评分文件，保留失败项与固定分母：

```bash
python -m benchmarks.intent.compare \
  --left internal/score-a/score.jsonl --right internal/score-b/score.jsonl \
  --out internal/comparison.json
```

实测对照、逐题差异、失败与局限见 [validation.json](validation.json)。
开发集同题对照只用于检查区分度，不是独立排行榜，也不建立临床有效性。
完整契约见 [English README](README.md)。

## 当前模型与校准方式

当前通过 OpenRouter 调用 `anthropic/claude-opus-5.5` 和 `openai/gpt-6-luna`，
两者都设为 medium 推理、8192 输出 token 预算、180 秒超时。两者分别审查题目，
并做跨家族评分；同一裁判评两种答案的结果也分别保留，自评分只作诊断参考。
[GPT‑6 Luna 官方文档](https://developers.openai.com/api/docs/models/gpt-6-luna)和
[Claude 型号文档](https://platform.claude.com/docs/en/models/overview)对应请求型号；
实际返回型号、完整参数、原始响应与用量均保存。参数与实际请求、缓存身份绑定。
OpenAI 直连使用 `max_completion_tokens`，OpenRouter 使用 `max_tokens`。

新模型外审促成了个人日界、21/7 基线窗口、任务专用 rubric、家人称谓和完整三次
报告比较的纠正。没有按谁赢来选题或删题，规模仍为32题。可见答题包完全相同的
任务可以复用相同型号、相同参数的原答案，并记录原始 case/request 哈希；输入
改变的任务重新作答。旧题包与结果分开保存，不与新集合哈希混算。8道保留题仍未调用模型。

独立逐题复核发现了模型外审漏掉的两处假区分：剔除日期按列举顺序判分，以及把
药品规格当成每次剂量。评测器8允许两个不同的剔除日期按任意顺序列出；药物事实
明确提取记录的规格，与已有的每次剂量指示分别处理。4道药物题的评审输入已更改，
旧评语不能计入新 rubric；追加的 DeepSeek 外审已覆盖这4道修订题。相邻键值表格中的纯数字与本组引用的单位行
可以绑定，跨记录借用单位仍然拒绝。

评测器9还支持从所属小节标题读取纯数值列的单位，不跨标题借用、不覆盖明确的
行列单位。中文逗号和 ISO 日期后的分隔逗号不会与后续数字拼接，数字千分位仍
正常解析。原始评语在新版本下离线重验；仅校验失败或截断的评审可以按明确的
更大输出预算补跑，每次原始尝试及参数均保留。

两种答题模型均已得到32份有效答案。OpenRouter 额度用尽后，用户授权通过 Nebula
追加 **DeepSeek V4 Pro 同一裁判**，现已完成 **32题外审＋64份答案评审**。基础参数
为 high thinking、8192输出 token、180秒超时；仅4个校验失败或截断的评审以16384
输出 token、300秒超时补跑，没有重新生成答案。另两份有效原评语由评测器9离线
重验恢复，不再调用模型。原始尝试、实际型号、参数、裁判原分与来源复核分分别保留。

| 答题模型 | DeepSeek原始能力题／回归题 | 来源复核后能力题／回归题 | 来源复核后合计 |
| --- | --- | --- | --- |
| Claude Opus 5.5 | 24/24 · 8/8 | 23/24 · 8/8 | 31/32 |
| GPT-6 Luna | 21/24 · 8/8 | 21/24 · 8/8 | 29/32 |

来源复核纠正了一次 Luna 剔除日期的误扣分，以及双方共有的一次漏判：只有规格
500 mg 的早期记录不能推出每次500 mg。这是 coding agent 对源记录、规则和算术的
核对，**不是临床专家验证**。Luna 两项相反修正恰好抵消，总分没变，所以必须看逐题
记录。题目外审的两条数值异议都把 UTC 日期当成当地日；另一次 abstention 把审题
误当成审答案。原意见与独立计算均保留。

当前只有 **2道能力题**形成已确认的模型差异：条件配对成员、当地日界的连续区间。
Opus 来源复核后仍为23/24，接近上限。这版适合作为小型回归基础，单独用于衡量
头部模型的 harness 提升还不够。下一版应预先定义6道更难的替换题，仍放在原来的
24道能力题配额内：修订报告与日期角色、设备重复上传与日界变化、规格／每次剂量／
版本／实际服药区分、稀疏跨指标配对、受限且可追溯的就诊摘要，以及有真实执行收据
的上下文压缩后事实保留。先独立验证金标，再调用模型，不按谁赢筛题。本次冻结集
没有暗中加入这些替换题。早前未完成的 OpenRouter 评审作为历史保留，不混入本轮
DeepSeek 同裁判统计。
