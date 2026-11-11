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

- `span` 必须是**原文的连续片段**，能直接检索到；跨句拼接的值要拆开或标 `span_ok: false`
- `value` 为 null 时，`null_reason` 必填，取值：`not_mentioned`（原文没提）、`illegible`（字迹/扫描不清）、`redacted`（已脱敏遮盖）、`ambiguous`（原文表述矛盾无法判定）
- 数值带单位，单位与原文一致；原文单位异常（如 CRP 写成 mg/ml）保留原值并在 `notes` 里标 `unit_suspect`
- 不确定是否属于该字段的，宁可不抽，把线索写进 `notes`

**三条硬规则**（违反任何一条，结果不可用）：

1. **一次只抽一份病历。** 遇到多份拼接、多个就诊日期、整目录输入时，拆开逐份处理，或先请用户确认范围。批量混抽会让靠后病历的召回率断崖式下降，且错误不会报错。
2. **绝不输出编码。** `value` 只放原文用词（「重度慢阻肺」），不要给 ICD/SNOMED/LOINC/RxNorm 编码。编码集有版本漂移，凭记忆给出的码往往已过期；需要编码时由下游单独做映射。
3. **不补全部分值。** 原文写「2020 年 6 月」，就输出 `2020-06`，不要补成 `2020-06-01`；原文写「约 8%」，保留「约」，不要改成 8.0。

### 第 3 步：校验

用脚本核对出处，不要只靠肉眼：

```bash
python scripts/validate_extraction.py --note note.txt --result result.json
```

脚本检查四件事：

1. 每个非空 `span` 能否在原文中检索到（忽略空白差异）
2. 每个 null 是否带合法 `null_reason`
3. 断言三轴取值是否合法
4. 数值字段是否通过 `check`（范围/正则/枚举）

输出逐字段 OK/FAIL 与汇总。有 FAIL 就回去改，不要带着错误出处交付。

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

默认给三样东西：

1. **结构化记录**：JSON（或用户指定的表格格式）
2. **校验报告**：脚本输出，逐字段 OK/FAIL
3. **待人工确认清单**：`ambiguous` 与 `unit_suspect` 的字段，说明为什么需要人工看

字段多时用表格呈现：`字段 | 值 | 出处片段 | 断言 | 备注`。

## 与其它技能的关系

- 抽取结果用于病例复盘 → `clinical-case-review`
- 抽取结果用于生成计划 → `treatment-plan-writer`
- 需按试验入排标准比对 → 先抽取，再交 `clinical-trial-lookup` 找试验
