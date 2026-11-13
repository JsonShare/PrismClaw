# 医疗信息学插件

## 概述

本插件提供医疗信息学相关能力，包括：

- **FHIR R4 电子病历集成**：通过 SMART-on-FHIR 协议连接 Epic、Oracle Health/Cerner、MEDITECH、athenahealth 等主流 EHR 系统
- **临床笔记结构化提取**：从非结构化临床文本中提取结构化数据，支持 span 级溯源
- **文档解析**：从 PDF/DOCX/XLSX/PPTX/RTF 等二进制文档中提取纯文本
- **FHIR 开发指南**：FHIR R4 REST API 开发、SMART 授权、资源结构参考

## 模块

| 模块 | 说明 |
|---|---|
| fhir | 连接医院 FHIR R4 服务器，拉取患者临床数据和笔记 |
| fhir-developer | FHIR API 开发参考，包括资源结构、HTTP 状态码、SMART 授权 |
| clinical-note-extract | 从临床笔记中提取结构化数据，支持自定义 schema |
| doc-extract | 通用文档转文本工具，支持 PDF/DOCX/XLSX/PPTX/RTF |

## 安全规范

1. **PHI 保护**：所有患者数据必须符合 HIPAA 和相关法规要求
2. **数据本地化**：FHIR 连接器直接从 EHR 系统读取数据，不缓存到本地
3. **访问控制**：SMART-on-FHIR OAuth 授权确保仅授权用户可访问患者数据
4. **审计追踪**：所有 FHIR 查询操作必须记录日志

## 环境要求

- **Node.js**: >= 20（FHIR MCP 服务器运行时）
- **网络访问**: 需要访问目标 EHR 系统的 FHIR R4 端点
- **EHR 支持**: Epic、Oracle Health/Cerner、MEDITECH、athenahealth 以及任何 SMART-on-FHIR 端点

## 输出规范

所有输出必须包含：
- 数据来源标识（EHR 系统名称和端点）
- 数据时间戳
- 置信度评估
- PHI 处理声明
