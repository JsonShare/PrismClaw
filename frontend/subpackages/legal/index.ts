/**
 * 法律域模块公共出口
 *
 * 唯一被外部消费的符号是 LegalWorkbenchPage —— 由 scripts/compile/domains.yaml 的
 * lazy_import / lazy_export 声明，经 scripts/compile/domains-registry.mjs 生成为
 * `import('@domains/legal/LegalWorkbenchPage').then(m => ({ default: m.LegalWorkbenchPage }))`。
 */

export { LegalWorkbenchPage } from './LegalWorkbenchPage'
