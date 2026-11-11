# 快速开始

**60 秒。** 让您快速开始使用医疗健康插件。

## 安装步骤

1. **打开 PrismClaw**（在您的终端中）
2. **添加市场。** 输入 `/plugin marketplace add `（末尾加一个空格），然后将解压后的 `medical-healthcare` 文件夹拖到终端窗口——它会自动填写路径。然后按 Enter。
3. **安装您的插件。** 从下表中选择与您工作匹配的：
   ```
   /plugin install medical-clinical@medical-healthcare
   ```
4. **⚠️ 重启 PrismClaw。** 关闭并重新打开。
5. **运行设置。** 需要 2 分钟（快速启动）或 10-15 分钟（完整）。
   ```
   /clinical-cold-start-interview
   ```

## 哪个插件适合我？

| 您是…… | 安装…… | 第一个命令 |
|---|---|---|
| 临床医生 / 护士 | `medical-clinical` | `/medical-clinical:patient-summary` |
| 合规专员 / 隐私官 | `medical-compliance` | `/medical-compliance:hipaa-review` |
| 医学研究者 / 方法学家 | `medical-research` | `/medical-research:literature-review` |
| 医疗机构运营 / 病历管理 | `medical-operations` | `/medical-operations:record-management` |

## 安全提醒

本插件生成的所有内容均为草稿，不构成医疗建议。在临床实践中使用之前，必须由持相应资质的医疗专业人员审核确认。