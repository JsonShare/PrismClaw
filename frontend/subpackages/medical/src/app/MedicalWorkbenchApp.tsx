/**
 * 医疗域 · 临床工作台外壳
 *
 * 布局：侧栏（190px，分组导航） / 内容区（1fr，内部滚动 + 容器查询）。
 *
 * 与原型 MainLayout 的差异：
 *   - 去掉全屏渐变背景与 aurora 光斑 —— 外层 DomainSection 已提供 glass-panel-floating 容器
 *   - 侧栏抽屉化：<lg 转为抽屉 + 顶部开关条；原型把 aside 设为 fixed 却漏了 -translate-x-full，
 *     导致窄屏侧栏永久压在内容上、汉堡按钮只切遮罩（原型缺陷）。此处按抽屉意图补全，
 *     开关条改为独立一行，避免原型那种浮动圆钮盖住页面标题。
 *   - 内容区滚动下沉到 main，外壳本身不滚动
 */

import { useState } from 'react'
import { Menu, X } from 'lucide-react'
import { NAV_ITEMS } from '../navigation/nav-config'
import { useWorkbenchNav } from '../navigation/useWorkbenchNav'
import { DrugsPage } from '../pages/DrugsPage'
import { GuidelinesPage } from '../pages/GuidelinesPage'
import { LiteraturePage } from '../pages/LiteraturePage'
import { MedicalRecordsPage } from '../pages/MedicalRecordsPage'
import { PatientDetailPage } from '../pages/PatientDetailPage'
import { PatientsPage } from '../pages/PatientsPage'
import { WorkbenchSidebar } from './WorkbenchSidebar'
import { ParseQueueFloat } from '../features/records/ParseQueueFloat'
import { ToastProvider } from '../components/ToastProvider'

export function MedicalWorkbenchApp() {
  const { route, isPatientDetail, go, openPatient, goBack } = useWorkbenchNav()
  const [drawerOpen, setDrawerOpen] = useState(false)

  /** 抽屉内点击导航后自动收起（与原型 SidebarInner 的 onNavigate 一致） */
  const navigateAndClose = (page: Parameters<typeof go>[0]) => {
    go(page)
    setDrawerOpen(false)
  }

  const currentLabel = NAV_ITEMS.find((i) => i.key === route.page)?.label ?? ''

  return (
    <ToastProvider>
      <div className="relative h-full w-full flex gap-3 p-3 sm:p-4 lg:p-5 min-h-0 overflow-hidden">
      {/* 遮罩：仅 <lg 且抽屉展开时存在，点击收起。
          层级 z-30 < 开关条 z-40 < 抽屉 z-50：
          开关条必须高于遮罩，否则 X 会被遮罩的 backdrop-filter 糊掉。 */}
      {drawerOpen && (
        <div
          className="mw-scrim lg:hidden absolute inset-0 z-30"
          onClick={() => setDrawerOpen(false)}
        />
      )}

      {/* 侧栏：<lg 抽屉（默认移出画布） / ≥lg 常驻静态列
          宽度 192px 与科研域侧栏一致（原 190px）。 */}
      <aside
        className={`absolute inset-y-0 left-0 z-50 w-[192px] flex p-3 lg:p-0 min-h-0 transform transition-transform duration-300 lg:static lg:z-auto lg:translate-x-0 lg:shrink-0 ${
          drawerOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <WorkbenchSidebar active={route.page} onNavigate={navigateAndClose} />
      </aside>

      {/* 内容列：容器查询上下文，所有内部自适应以本区宽度为基准 */}
      <div className="flex flex-col flex-1 min-w-0 min-h-0">
        {/* 抽屉开关条：仅 <lg 出现，≥lg 完全不渲染（不占任何高度）。
            展开时右移到抽屉（192px）之外，避免按钮被抽屉压在下面 —— 原型把圆钮
            固定在 left-[206px] 也是这个原因，但那个位置在抽屉收起时会悬空。 */}
        <div
          className={`lg:hidden relative z-40 flex items-center gap-2.5 mb-3 shrink-0 transition-[padding] duration-300 ${
            drawerOpen ? 'pl-[204px]' : 'pl-0'
          }`}
        >
          <button
            type="button"
            onClick={() => setDrawerOpen((v) => !v)}
            aria-label={drawerOpen ? '收起导航' : '展开导航'}
            aria-expanded={drawerOpen}
            className="h-9 w-9 shrink-0 rounded-lg flex items-center justify-center cursor-pointer text-[length:var(--fs-md)] text-[var(--c-text-secondary)] bg-[var(--c-surface-2)] hover:bg-[var(--c-surface-3)] transition-colors duration-200"
          >
            {drawerOpen ? <X className="h-[18px] w-[18px]" /> : <Menu className="h-[18px] w-[18px]" />}
          </button>
          <span className="text-[length:var(--fs-sm)] font-medium text-[var(--c-text-secondary)]">
            {currentLabel}
          </span>
        </div>

        <main className="mw-cq flex-1 min-w-0 min-h-0 overflow-y-auto mw-scroll pr-1">
          {isPatientDetail && route.patientId !== null ? (
            <PatientDetailPage
              patientId={route.patientId}
              onBack={goBack}
            />
          ) : route.page === 'records' ? (
            <MedicalRecordsPage onOpenPatient={openPatient} />
          ) : route.page === 'patients' ? (
            <PatientsPage onOpenPatient={openPatient} />
          ) : route.page === 'literature' ? (
            <LiteraturePage />
          ) : route.page === 'guidelines' ? (
            <GuidelinesPage />
          ) : (
            <DrugsPage />
          )}
        </main>
      </div>

      {/* 全局解析队列浮层：百度网盘风格，右下角悬浮 */}
      <ParseQueueFloat />
    </div>
    </ToastProvider>
  )
}
