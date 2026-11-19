/**
 * 法律域 · 工作台入口（骨架）
 *
 * 契约：domains.yaml 的 lazy_import 指向 `@domains/legal/LegalWorkbenchPage`，
 * lazy_export 为 `LegalWorkbenchPage` —— 本文件必须保持在分包根目录，不可移入 src/。
 *
 * 二级菜单（合同审查 / 合规检查）已平铺到系统左侧栏，
 * 本骨架接受宿主下发的 page 并回显，同时同步到 URL（?lw_page=），
 * 使子页可深链、刷新不丢当前页。切换域时清掉医疗域的 mw_page，避免地址栏残留。
 */

import { useEffect, useRef, useState } from 'react'
import { reportDomainPage } from '@/domains/nav-bus'

const MENU_LABELS: Record<string, string> = {
  contract: '合同审查',
  compliance: '合规检查',
}

const PARAM_PAGE = 'lw_page'
const PARAM_OTHER = 'mw_page'
const DEFAULT_PAGE = 'contract'

function isPageKey(value: string | null): value is string {
  return !!value && Object.prototype.hasOwnProperty.call(MENU_LABELS, value)
}

function parsePage(): string | null {
  if (typeof window === 'undefined') return null
  const value = new URLSearchParams(window.location.search).get(PARAM_PAGE)
  return isPageKey(value) ? value : null
}

/** 写入 URL：设置本域参数，并清掉医疗域参数（避免跨域切换时残留） */
function syncUrl(page: string): void {
  const url = new URL(window.location.href)
  url.searchParams.set(PARAM_PAGE, page)
  url.searchParams.delete(PARAM_OTHER)
  // 医疗的详情态参数也要清：只清 mw_page 会留下 mw_patient，
  // 下次进入医疗域时会被 parseRoute 当成有效详情 id 读出来。
  url.searchParams.delete('mw_patient')
  window.history.replaceState(null, '', url.toString())
}

export interface LegalWorkbenchPageProps {
  /** 系统侧栏下发的目标页 */
  page?: string
  /** 跳转意图序号 */
  pageNonce?: number
}

export function LegalWorkbenchPage({ page }: LegalWorkbenchPageProps) {
  const [current, setCurrent] = useState<string>(() =>
    isPageKey(page ?? null) ? (page as string) : parsePage() ?? DEFAULT_PAGE,
  )

  // 侧栏下发新意图（含重复点击同一项：值不变时无需动作，页面本身无深层状态）
  useEffect(() => {
    if (!isPageKey(page ?? null) || page === current) return
    setCurrent(page as string)
  }, [page, current])

  // 同步 URL + 回报当前页供侧栏高亮
  const reportedRef = useRef<string | null>(null)
  useEffect(() => {
    syncUrl(current)
    if (reportedRef.current !== current) {
      reportedRef.current = current
      reportDomainPage('legal', current)
    }
  }, [current])

  return (
    <div className="flex-1 min-h-0 flex flex-col items-center justify-center gap-3 px-6 text-center">
      <h1 className="text-[length:var(--fs-xl,20px)] font-semibold text-[var(--c-text)]">
        {MENU_LABELS[current]}
      </h1>
      <p className="text-[length:var(--fs-sm,13px)] text-[var(--c-text-secondary)]">
        智能法律工作台 · 合同审查 · 合规检查
      </p>
      <p className="mt-2 text-[length:var(--fs-xs,12px)] text-[var(--c-text-secondary)]">
        模块骨架已接入，功能建设中
      </p>
    </div>
  )
}

export default LegalWorkbenchPage
