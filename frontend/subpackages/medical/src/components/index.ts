/**
 * 医疗域 · UI 组件桶
 *
 * 页面与 feature 一律从 '../components' 统一取用，避免同一原语出现两种导入路径。
 */

export * from './ui'
export { PagePager } from './PagePager'
export { ToastProvider, useToast } from './ToastProvider'
export type { ToastType } from './ToastProvider'
