# 精准免疫评估专家 v2.1 — 安装与使用说明

## 包内容

| 文件 | 说明 |
|------|------|
| `SKILL.md` | **技能主文件（v2.1）**——安装时将此文件放入技能目录即可 |
| `SKILL-v2.0-原版.md` | v2.0 原版技能文件，供对比参考 |
| `升级评估报告.md` | v2.0 → v2.1 升级评估报告，详细记录问题分析与升级方案 |

## 安装方式

### 方式一：MedMate Claw 技能管理器安装

1. 打开 MedMate Claw → 设置 → 技能管理
2. 选择"导入技能"，上传 `SKILL.md` 文件
3. 技能将自动注册为 `precision-immune-assessment`（v2.1.0）

### 方式二：手动放置技能目录

1. 在 MedMate Claw 技能目录下创建文件夹：
   ```
   precision-immune-assessment/
   └── SKILL.md
   ```
2. 重启 MedMate Claw 使技能生效

## 依赖技能

本技能运行时需调用以下平台 AI-Doctor 技能族（MedMate Claw 标配，通常已预装）：

| 技能名 | 用途 |
|--------|------|
| sino-search-guideline | 指南检索（参考值校准） |
| sino-search-literature | 文献检索（前沿证据 + GRADE 定级） |
| sino-search-drug-instructions | 药品说明书检索（药品安全链） |
| sino-search-indicator-encyclopedia | 指标百科（参考范围 + 临床意义） |
| sino-knowledge-disease | 疾病知识库（疾病背景关联） |
| sino-knowledge-adverse-event | 不良事件库（CTCAE AE 分级） |
| sino-report-analysis | 报告分析（检测报告结构化解读，可选） |

> 以上技能均使用 `.agents/skills/<技能名>/scripts/` 标准路径调用，无需额外配置。

## 触发词

用户输入以下关键词时自动触发本技能：

精准免疫 / 免疫评估 / 淋巴细胞分型 / T细胞耗竭 / 免疫耗竭 / TRECs / 免疫组库 / TCR多样性 / BCR多样性 / 脓毒症免疫 / 免疫状态分层 / 免疫治疗评估 / 长新冠免疫 / 单核细胞HLA-DR / 免疫功能检测解读

## 版本信息

- **版本**：v2.1.0
- **基础版本**：v1.0（2026-05-04，彭沪主任）
- **升级日期**：2026-08-03
- **升级方**：中康科技 MedMate 团队
- **专家来源**：彭沪，上海市第十人民医院急诊医学科

## v2.1 核心变更（相对 v2.0）

1. 将概念性"medsearch 四库"替换为平台实际 AI-Doctor 技能调用路径
2. 全部脚本路径采用 `.agents/skills/` 可移植路径，适配任意用户机器
3. 新增"技能协作路由"章节（上游/并行/下游技能 + 边界声明）
4. 参考值校准流程与药品安全链流程具体化（可执行步骤）
5. 输出模板增加数据源链接列，循证溯源清单含可点击 URL
6. 新增指标百科、不良事件库两个数据源技能接入
7. 接入 sino-report-analysis 实现检测报告辅助解读

## 合规声明

⚠️ 本技能生成的评估结果仅供执业医师参考，实际临床应用请由执业医师审核把关。
本技能不构成临床诊断，治疗决策需结合患者整体临床情况。
