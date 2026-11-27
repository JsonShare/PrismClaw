# 医疗合同专家

对医疗与采购协议做问答式分析，逐条给出带原文引用的解读与风险提示。

## 类型

Agent 型（单个 AI 专家），expertType = agent。

## 来源

独立专家包，与其他专家平级。

## 能力

- 合同问答：针对条款提问给出定位与解释。
- 溯源引用：每个结论标注原文章节与页码/段落。
- 批量扫描：多份合同按统一维度筛查异常条款。

## 使用示例

- 这份医疗合同的关键条款有哪些？逐条标注原文位置。
- 批量扫描这几份采购合同，列出异常条款。
- 解释这条责任限制条款，并提示需确认的问题。

## 包结构

- `agents/contracts-med.md`：专家定义
- `skills/`：2 个技能（`contracts`、`contracts-med-playbook`）
- `skills/contracts-med-playbook/`：执业资料与流程参考
- `skills/contracts-med-playbook/references/practice-profile/`：执业档案模板
- `skills/contracts-med-playbook/references/mcp/`：MCP 配置（仅供查阅）
- `avatars/expert.png`：专家头像
- `LICENSE`：源插件许可证

## 注意事项

- 合同解释不构成法律意见；重大争议转交法务专家团或执业律师。
- 交付为草稿或研究笔记，签署与对外发送前须由律师复核；未核实的法规与阈值标 [待核验]。

