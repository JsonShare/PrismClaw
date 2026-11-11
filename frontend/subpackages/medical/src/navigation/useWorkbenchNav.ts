/**
 * 医疗域 · 工作台路由（内部状态 + URL 同步）
 *
 * 设计取舍：
 *   - 不引入 react-router：域页面是懒加载进 ChatPage 的单个组件，挂不了独立路由树
 *   - URL 用 query 参数同步（?mw_page=&mw_patient=），刷新可保持当前视图
 *   - 视图切换走 pushState，因此浏览器前进/后退可用；
 *     页面内「返回」按钮优先 history.back()，无历史时回退到患者列表
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { isPageKey, type WorkbenchPageKey } from './nav-config'

/** 工作台路由状态 */
export interface WorkbenchRoute {
  page: WorkbenchPageKey
  /** 患者详情页的患者 id（短码如 P0001）；非详情页为 null */
  patientId: string | null
}

const PARAM_PAGE = 'mw_page'
const PARAM_PATIENT = 'mw_patient'

const DEFAULT_ROUTE: WorkbenchRoute = { page: 'records', patientId: null }

/** 从当前 URL 解析路由 */
function parseRoute(): WorkbenchRoute {
  if (typeof window === 'undefined') return DEFAULT_ROUTE
  const params = new URLSearchParams(window.location.search)
  const page = params.get(PARAM_PAGE)
  if (!isPageKey(page)) return DEFAULT_ROUTE

  const raw = params.get(PARAM_PATIENT)
  const patientId = page === 'patients' && raw !== null ? raw : null
  return { page, patientId }
}

/** 把路由序列化回 URL（保留其他既有 query 参数） */
function toUrl(route: WorkbenchRoute): string {
  const url = new URL(window.location.href)
  url.searchParams.set(PARAM_PAGE, route.page)
  if (route.patientId === null) url.searchParams.delete(PARAM_PATIENT)
  else url.searchParams.set(PARAM_PATIENT, String(route.patientId))
  return url.toString()
}

export interface WorkbenchNav {
  route: WorkbenchRoute
  /** 是否处于患者详情页 */
  isPatientDetail: boolean
  /** 切换到某个一级页面（压入历史，会清空 patientId） */
  go: (page: WorkbenchPageKey) => void
  /** 打开患者详情（压入历史） */
  openPatient: (patientId: string) => void
  /** 返回：优先浏览器后退，无历史时回患者列表 */
  goBack: () => void
}

export function useWorkbenchNav(): WorkbenchNav {
  const [route, setRoute] = useState<WorkbenchRoute>(parseRoute)
  /** 当前路由的镜像，供回调同步读取，避免在 setState updater 里做副作用 */
  const routeRef = useRef(route)
  /** 会话内已压入的历史条目数，决定 goBack 能否真的后退 */
  const depthRef = useRef(0)

  const commit = useCallback((next: WorkbenchRoute, push: boolean) => {
    if (push) {
      window.history.pushState(null, '', toUrl(next))
      depthRef.current += 1
    } else {
      window.history.replaceState(null, '', toUrl(next))
    }
    routeRef.current = next
    setRoute(next)
  }, [])

  const go = useCallback(
    (page: WorkbenchPageKey) => {
      const prev = routeRef.current
      if (prev.page === page && prev.patientId === null) return
      commit({ page, patientId: null }, true)
    },
    [commit],
  )

  const openPatient = useCallback(
    (patientId: string) => {
      commit({ page: 'patients', patientId }, true)
    },
    [commit],
  )

  const goBack = useCallback(() => {
    if (depthRef.current > 0) {
      window.history.back()
      return
    }
    commit({ page: 'patients', patientId: null }, false)
  }, [commit])

  // 浏览器前进/后退：同步回内部状态
  useEffect(() => {
    const onPop = () => {
      depthRef.current = Math.max(0, depthRef.current - 1)
      const next = parseRoute()
      routeRef.current = next
      setRoute(next)
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  return {
    route,
    isPatientDetail: route.page === 'patients' && route.patientId !== null,
    go,
    openPatient,
    goBack,
  }
}
