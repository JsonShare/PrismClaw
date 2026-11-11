/**
 * 医疗域模块公共出口
 *
 * 唯一被外部消费的符号是 MedicalWorkbenchPage —— 由 scripts/compile/domains.yaml 的
 * lazy_import / lazy_export 声明，经 scripts/compile/domains-registry.mjs 生成为
 * `import('@domains/medical/MedicalWorkbenchPage').then(m => ({ default: m.MedicalWorkbenchPage }))`。
 * 改名或移动该文件都会让懒加载失败，务必保持路径不变。
 *
 * 其余导出（视图模型 / 后端契约 / API 客户端）是为后续接线准备的公共面，
 * 当前页面并未消费 —— 页面数据来自 src/data/mock-data.ts。
 */

export { MedicalWorkbenchPage } from './MedicalWorkbenchPage'

/* 视图模型（页面渲染层使用） */
export type * from './src/types/view'

/* API 客户端（已接线） */
export * from './src/api/client'
