/**
 * 医疗域 · 临床工作台外壳
 *
 * 布局：内容区单列（二级菜单已平铺到系统左侧栏，域内不再有侧栏/抽屉）。
 *
 * 路由来源：
 *   - 系统侧栏点击子项 → 宿主经 page / pageNonce 下发意图（见 DomainSection）
 *   - 刷新 / 浏览器前进后退 → 域内 URL（?mw_page=&mw_patient=）自行解析
 *   - 域切页后通过 reportDomainPage 回报，侧栏据此高亮
 */

import { useEffect } from 'react'
import { reportDomainPage } from '@/domains/nav-bus'
import { useWorkbenchNav } from '../navigation/useWorkbenchNav'
import { DrugsPage } from '../pages/DrugsPage'
import { GuidelinesPage } from '../pages/GuidelinesPage'
import { LiteraturePage } from '../pages/LiteraturePage'
import { MedicalRecordsPage } from '../pages/MedicalRecordsPage'
import { PatientDetailPage } from '../pages/PatientDetailPage'
import { PatientsPage } from '../pages/PatientsPage'
import { ParseQueueFloat } from '../features/records/ParseQueueFloat'
import { ToastProvider } from '../components/ToastProvider'

interface MedicalWorkbenchAppProps {
  /** 系统侧栏下发的目标页 */
  page?: string
  /** 跳转意图序号：重复点击同一子项时自增 */
  pageNonce?: number
}

export function MedicalWorkbenchApp({ page, pageNonce }: MedicalWorkbenchAppProps) {
  const { route, isPatientDetail, openPatient, goBack } = useWorkbenchNav({
    externalPage: page,
    externalNonce: pageNonce,
  })

  // 当前页回报给系统侧栏（高亮）
  useEffect(() => {
    reportDomainPage('medical', route.page)
  }, [route.page])

  return (
    <ToastProvider>
      <div className="relative h-full w-full flex flex-col p-3 sm:p-4 lg:p-5 min-h-0 overflow-hidden">
        <main className="mw-cq flex-1 min-w-0 min-h-0 overflow-y-auto mw-scroll pr-1">
          {isPatientDetail && route.patientId !== null ? (
            <PatientDetailPage patientId={route.patientId} onBack={goBack} />
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

        {/* 全局解析队列浮层：百度网盘风格，右下角悬浮 */}
        <ParseQueueFloat />
      </div>
    </ToastProvider>
  )
}

export default MedicalWorkbenchApp
