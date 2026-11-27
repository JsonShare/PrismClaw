---
name: clinical-note-extract
name_zh: 病历结构化抽取
description: 按用户定义的字段清单，从中文病历、检验单、出院小结等非结构化文本中抽取结构化数据，每个值都带原文出处（span）、缺失项显式标注理由，并按 presence/temporality/experiencer 三轴标注断言状态（有/无/疑似、当前/既往/假设、本人/家属/其他）。用于健康档案入库、队列数据整理、入排标准比对、随访表填充。当用户说「提取这些字段」「把病历转成表格」「结构化这段病历」「抽出用药和检验值」时使用。
category: 临床诊疗
invocation_policy: auto
priority: 9
---

# 病历结构化抽取

## 原则

**只抽原文写了的，不补原文没写的。** 抽取器的价值在于「可核对」——每个值都能指回原文的某一句，缺失的字段老实标缺失，而不是靠医学常识把空补上。

补出来的字段看起来完整，但无法核对，一旦进入下游分析就是污染源。

## 三步流程

### 第 1 步：定义 schema

把用户需求转成字段清单，每个字段声明四件事：

```json
{
  "hba1c": {
    "desc": "糖化血红蛋白，找检验结果里的 HbA1c 或糖化血红蛋白",
    "type": "number",
    "unit": "%",
    "check": {"kind": "range", "min": 3, "max": 20}
  },
  "diagnosis": {
    "desc": "主要诊断，取出院诊断或首次病程记录中的诊断结论",
    "type": "string"
  },
  "metformin_dose": {
    "desc": "二甲双胍日剂量，注意区分单次剂量与日总量",
    "type": "string",
    "unit": "mg/d"
  }
}
```

字段描述要写清**在哪找、怎么区分**，不要只写字段名。定义完先跟用户确认，再开始抽。

字段还有两个可选键：`finding: true` 表示该字段要看断言语境（症状、诊断、暴露），抽取侧按下面的三轴标注；`check` 由校验脚本在抽取之后按 `kind` 分派——抽取侧看到 `check` 但不执行，只负责原样取值：

| `check.kind` | 参数 | 校验脚本做什么 |
|---|---|---|
| `range` | `min` / `max` / `unit` | 数值越界记 FAIL；原文单位与 `unit` 不符记 `unit_suspect` |
| `date` | — | 按原文精度解析（`2020` / `2020-06` / `2020-06-15`）；补全精度或非法日期记 FAIL |
| `pattern` | `regex` | 不匹配记 FAIL |
| `enum` | `values` | 不在枚举内记 FAIL；**不要把候选答案写进 `values`**，抽取方会看到 |
| `terminology` | `via` | 没有对应编码连接器时不判定，挂 `code_status: unvalidated` 并在报告点名 |

新增一种 kind = 在校验脚本加一个分支，抽取规则与 worker 契约都不变。

### 第 2 步：抽取

对每份病历输出一条记录：

```json
{
  "note_id": "note-001",
  "fields": {
    "hba1c": {
      "value": 8.5,
      "span": "HbA1c 8.5%",
      "presence": "present",
      "temporality": "current",
      "experiencer": "patient"
    },
    "metformin_dose": {
      "value": null,
      "span": null,
      "null_reason": "not_mentioned"
    }
  }
}
```

字段规则：

- `span` 必须是**原文的连续片段**，能直接检索到；跨句拼接、改写措辞的都不算 span，会被校验脚本判为「检索不到」
- `value` 为 null 时，`null_reason` 必填，取值：`not_mentioned`（原文没提）、`illegible`（字迹/扫描不清）、`redacted`（已脱敏遮盖）、`ambiguous`（原文表述矛盾无法判定）、`out_of_scope`（字段不适用于本次就诊）
- 数值带单位，单位与原文一致；原文单位异常（如 CRP 写成 mg/ml）保留原值并在 `notes` 里标 `unit_suspect`
- 不确定是否属于该字段的，宁可不抽，把线索写进 `notes`
- 一个字段只放一个值。要「所有用药」这类清单时，先用自由文本抽出清单，再按清单逐项建 schema 重抽一次，不要在单字段里塞数组
- 同一字段出现多个冲突值时（如两次 FEV1），按 `desc` 的限定取（如「用药后」）；`desc` 没限定时取最近或最具体的一个，两个 span 都写进 `notes`

**三条硬规则**（违反任何一条，结果不可用）：

1. **一次只抽一份病历。** 遇到多份拼接、多个就诊日期、整目录输入时，拆开逐份处理，或先请用户确认范围。批量混抽会让靠后病历的召回率断崖式下降，且错误不会报错。
2. **绝不输出编码。** `value` 只放原文用词（「重度慢阻肺」），不要给 ICD/SNOMED/LOINC/RxNorm 编码。编码集有版本漂移，凭记忆给出的码往往已过期；需要编码时由下游单独做映射。
3. **不补全部分值。** 原文写「2020 年 6 月」，就输出 `2020-06`，不要补成 `2020-06-01`；原文写「约 8%」，保留「约」，不要改成 8.0。

**多份病历怎么办。** 硬规则「一次只抽一份」约束的是**每个抽取上下文只看一份**，不是拒绝批量输入。多份时按份隔离：每份病历单独交给 `note-extract-worker` 子代理（无工具、不共享上下文），一份一个 worker，彼此看不到对方的原文；每份再各自跑第 3 步校验。不要把多份病历拼进同一个抽取上下文——那正是召回率骤降且不报错的原因。

数值的合理区间只用来**发现单位或转写错误**，不用来改值——超出下表时照原样输出，把疑点写进 `notes`：

| 指标 | 单位 | 合理区间 |
|---|---|---|
| 心率 | bpm | 20–300 |
| 收缩压 / 舒张压 | mmHg | 40–300 / 20–200 |
| 体温 | °C / °F | 30–45 / 86–113 |
| SpO2 | % | 40–100 |
| HbA1c | % | 3–20 |
| eGFR | mL/min/1.73m² | 1–200 |
| 年龄 | 岁 | 0–130 |
| 体重 / 身高 | kg / cm | 0.3–700 / 20–275 |

### 第 3 步：校验

用脚本核对，不要只靠肉眼。带 schema 跑才能校验 `check`：

```bash
python scripts/validate_extraction.py --note note.txt --result result.json --schema schema.json
python scripts/validate_extraction.py --note note.txt --result result.json --schema schema.json --json
```

脚本是确定性的，不做医学判断，检查五件事：

1. 每个非空 `span` 能否在原文中检索到（忽略空白与全半角差异）
2. 每个 null 是否带合法 `null_reason`
3. 断言三轴取值是否合法
4. 字段的 `type` 与 `check` 是否通过（`range` / `date` / `pattern` / `enum` / `terminology`）
5. 原文单位与 schema 期望单位是否一致（不一致记 `unit_suspect`）

**可信判定：一个字段可信 = `span_verified` 为 true，且它的 `check` 没有 FAIL。两者缺一不可。**
有 FAIL 就回去改，不要带着错误出处交付；`terminology` 缺连接器时挂 `unvalidated`，不算 FAIL，但必须在报告里点名，不能静默当成已验证。

报告必须交代完整性：**请求 N 字段 · 有值 M · 空 K（按 `null_reason` 分项）**，再按 `check.kind` 给出通过/标记数。任何未通过的 check、未命中的 span，都不许静默混进结果。

## 断言三轴（中文适配）

同一句话含金量可能完全不同：「否认胸痛」和「胸痛」都出现了「胸痛」，但一个是阴性。中文病历的否定、既往、家属表述尤其容易抽错。

三个轴**相互独立**，只标偏离默认值的轴：

| 轴 | 取值 | 默认 | 什么情况偏离 |
|---|---|---|---|
| `presence` | `present` / `absent` / `possible` | present | 否定词（否认、无、未见、阴性、不伴、已排除）→ absent；模糊词（可能、疑似、待排、不能排除、倾向、？）→ possible |
| `temporality` | `current` / `historical` / `hypothetical` | current | 既往（既往史、曾有、术后、已愈、×年前）→ historical；预防用、监测、待排查、若出现 → hypothetical |
| `experiencer` | `patient` / `family_member` / `other` | patient | 家属（母亲、父亲、兄长、家族史）→ family_member；供者、胎儿等 → other |

三轴可叠加。「母亲否认既往心梗」= `{presence: absent, temporality: historical, experiencer: family_member}`。

完整规则、中文词表与判定示例见 `references/assertion-axes.md`。

## 常见中文病历陷阱

| 陷阱 | 例子 | 正确做法 |
|---|---|---|
| 否定范围判断错 | 「无发热、咳嗽、咳痰」否定的是三个症状 | 三个字段都标 absent |
| 双重否定 | 「未排除结核」 | presence=possible，不是 absent |
| 既往被当作现症 | 「既往高血压 10 年，今测血压正常」 | 高血压 historical；血压值 current |
| 家族史当成患者史 | 「父亲糖尿病」 | experiencer=family_member |
| 预防用药当成治疗用药 | 「予奥美拉唑预防应激性溃疡」 | temporality=hypothetical |
| 医嘱与执行混淆 | 「拟行胸部 CT」未执行 | 标 `planned`，不当已完成事实 |
| 数值与单位分离 | 「糖化 8.5」单位在表头 | 单位从表头补，span 含数值 |
| 区间值 | 「血压 130-140/80-90」 | 保留区间，不要取平均 |

## 输出

默认给四样东西：

1. **结构化记录**：JSON（或用户指定的表格格式）
2. **核对表**：`字段 | 值 | 断言 | 出处片段 | 状态`，一行一字段，按 schema 顺序
3. **完整性汇总**：请求 N · 有值 M · 空 K（按 `null_reason` 分项），以及各 `check.kind` 的通过/标记数、`terminology` 未验证项
4. **待人工确认清单**：`ambiguous`、`unit_suspect`、`unvalidated` 的字段，逐条说明为什么需要人工看

状态列取值：`有值` · `已核`（check 通过）· `越界` · `单位存疑` · `未验证`（术语连接器缺失）· `未提及` / `字迹不清` / `已脱敏`（对应 null_reason）。

核对表的意义是让审核者不必重读病历就能逐行接受或驳回；reviewer 的接受/驳回结果就是这套抽取的黄金标注，能留下就留下。

## 与其它技能的关系

- 抽取结果用于病例复盘 → `clinical-case-review`
- 抽取结果用于生成计划 → `treatment-plan-writer`
- 需按试验入排标准比对 → 先抽取，再交 `clinical-trial-lookup` 找试验
