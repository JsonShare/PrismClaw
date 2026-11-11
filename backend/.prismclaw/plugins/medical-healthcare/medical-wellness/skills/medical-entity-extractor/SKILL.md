---
category: 健康管理
invocation_policy: auto
priority: 9
name: medical-entity-extractor
name_zh: 医学实体抽取
description: 从用户自然语言中抽取医学实体（症状、药物、检验值、诊断、过敏史、时间）并结构化输出。用于健康档案入库、对话信息结构化、从口语描述中提取用药与指标字段。当需要把口语化健康描述转成结构化数据，或询问「提取病历要素」「结构化这段描述」「识别用药和指标」时使用。
license: MIT
metadata:
  author: "NAPSTER AI"
  maintainer: "NAPSTER AI"
  openclaw:
    requires:
      bins: []
---

# 医学实体抽取

把患者或家属的口语化描述转成结构化医学实体。输入是一段自然语言，输出是严格的 JSON——不解释、不总结成散文、不补全原文没有的信息。

## 抽取范围

1. **症状**：症状名称、严重程度、持续时间、起病时点、进展趋势
2. **药物**：药名、剂量、频次、给药途径、用药状态
3. **检验与指标**：化验值、生命体征、测量值，含单位与时间
4. **诊断**：明确诊断、疑似诊断、已排除
5. **时间**：症状何时开始、持续多久、与用药或事件的先后关系
6. **待办动作**：复诊、续方、回电、待解答的问题

## 输入格式

```json
[
  {
    "id": "msg-123",
    "body": "三天前换了新的降压药（赖诺普利 10mg），之后一直头晕。今天早上量血压 145/92。",
    "date": "2026-02-27T10:30:00Z"
  }
]
```

`id` 用于回填；`body` 是要抽取的原文；其余字段（来源、优先级等）按调用方上下文原样透传即可，不参与抽取。

## 输出格式

```json
[
  {
    "id": "msg-123",
    "entities": {
      "symptoms": [
        {
          "name": "头晕",
          "severity": "moderate",
          "duration": "3 天",
          "onset": "换用新降压药之后",
          "progression": null
        }
      ],
      "medications": [
        {
          "name": "赖诺普利",
          "dosage": "10mg",
          "frequency": null,
          "route": null,
          "status": "new"
        }
      ],
      "lab_values": [
        {
          "type": "blood_pressure",
          "value": "145/92",
          "unit": "mmHg",
          "timestamp": "今天早上",
          "normal_range": null
        }
      ],
      "diagnoses": [
        {
          "name": "高血压",
          "certainty": "confirmed"
        }
      ],
      "action_items": [
        {
          "type": "medication_review",
          "urgency": "routine",
          "reason": "可能的药物不良反应（头晕）"
        }
      ]
    },
    "null_reason": null
  }
]
```

## 字段取值约定

字段名与枚举值一律保留英文，便于下游入库；实体内容用原文语言（中文场景即中文），不要翻译成英文病名。

### symptoms
- `name`：原文用词，不替换成标准术语
- `severity`：`mild` / `moderate` / `severe`；原文没说不填
- `duration`、`onset`、`progression`（`improving` / `stable` / `worsening`）：原文没提则 `null`

### medications
- `name`：商品名与通用名按原文保留，两者都给时用 `name` + `generic_name`
- `dosage`、`frequency`、`route`：没写则 `null`，不要按常识补
- `status`：`new` / `existing` / `stopped`

### lab_values
- `type`：`blood_pressure`、`blood_glucose`、`cholesterol`、`weight` 等
- `value` 与 `unit` 必须成对来自原文；原文只有数值没有单位时 `unit` 填 `null`，不要替用户补

### diagnoses
- `certainty`：`confirmed` / `suspected` / `ruled_out`

### action_items
- `type`：`appointment` / `refill` / `question` / `callback` / `medication_review`
- `urgency`：`routine` / `urgent` / `emergency`

## 中文口语处理

需要识别的常见表达：

- **缩写与俗称**：血压、心率、呼吸、血氧；「心梗」→ 心肌梗死，「拉肚子」→ 腹泻，「血糖高」→ 血糖升高
- **量词与口语数值**：「一百四十五比九十二」「两片」「一天三次」「半粒」
- **模糊时间**：「昨天开始」「这周一直」「前两天」「自从换了药」
- **否定与不确定**：「没有发烧」「好像有点」「医生说可能是」——只按字面抽取，不要升格
- **家属代述**：「我妈最近老说头晕」——症状归属患者本人，不要记成叙述者

原文信息不足时，把对应字段置 `null` 并在 `null_reason` 写明缺什么，例如 `"未提及用药频次"`。禁止用医学常识补全剂量、单位或病程。

## 安全边界

- 本技能只做抽取，不做诊断、不给用药建议。抽取结果里出现「建议」「应该服用」这类内容即为越界。
- 原文涉及自伤、自杀、胸痛持续不缓解、意识改变等急症线索时，除正常抽取外，在 `action_items` 里置一条 `urgency: "emergency"`，交由上层流程处理。
- 输出只有 JSON，不带 Markdown 代码围栏、不带前言后语。

## 调用方式

由技能激活触发（模型判断或用户指定），无需外部 CLI。每次调用处理一批消息，逐条返回；某条无法抽取时该条 `entities` 置空并以 `null_reason` 说明，不要跳过。
