/**
 * 医疗域 · 工作台入口
 *
 * 契约：后端 domains.yaml 的 lazy_import 指向 `@domains/medical/MedicalWorkbenchPage`，
 * lazy_export 为 `MedicalWorkbenchPage` —— 本文件必须保持在分包根目录，不可移入 src/。
 *
 * 样式层在此统一导入一次，按宿主 theme/ 的三分结构组织：
 *   medical.css + v3-records.css + patient-detail.css  主题无关的结构与令牌转发
 *   light.css / dark.css          各自由 [data-theme] 作用域隔离的专属值
 * 明暗切换由宿主 stores/ui.ts 写 document.documentElement 的 data-theme 驱动，
 * 域无需自己监听 prefers-color-scheme —— 宿主「跟随系统」时已把它翻成 data-theme。
 */

import { MedicalWorkbenchApp } from './src/app/MedicalWorkbenchApp'
import './src/styles/medical.css'
import './src/styles/v3-records.css'
import './src/styles/patient-detail.css'
import './src/styles/light.css'
import './src/styles/dark.css'

export function MedicalWorkbenchPage() {
  return <MedicalWorkbenchApp />
}

export default MedicalWorkbenchPage
