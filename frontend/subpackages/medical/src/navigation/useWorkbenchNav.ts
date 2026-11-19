/**
 * 医疗域 · 工作台路由（内部状态 + URL 同步 + 受控外部跳转）
 *
 * 设计取舍：
 *   - 不引入 react-router：域页面是懒加载进 ChatPage 的单个组件，挂不了独立路由树
 *   - URL 用 query 参数同步（?mw_page=&mw_patient=），刷新可保持当前视图
 *   - 视图切换走 pushState，因此浏览器前进/后退可用；
 *     页面内「返回」按钮优先 history.back()，无历史时回退到患者列表
 *   - 二级菜单已平铺到系统侧栏：外部通过 page / pageNonce 下发跳转意图，
 *     本 hook 消费后写入自身状态，并上报当前页供侧栏高亮
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

/**
 * 已消费过的跳转意图序号（模块级，跨组件挂载存活）。
 *
 * 没有它会产生回归：用户打开患者详情 → 切到「新任务」→ 再切回医疗域时，
 * 组件重新挂载、组件内 ref 归零，会把上一次点击留下的陈旧意图再消费一遍，
 * 导致详情页被清掉。记在模块级就能确保一次点击只生效一次。
 */
let consumedIntentNonce = -1

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

/** 把路由序列化回 URL（保留其他既有 query 参数，并清掉法律域的 lw_page） */
function toUrl(route: WorkbenchRoute): string {
  const url = new URL(window.location.href)
  url.searchParams.set(PARAM_PAGE, route.page)
  url.searchParams.delete('lw_page')
  if (route.patientId === null) url.searchParams.delete(PARAM_PATIENT)
  else url.searchParams.set(PARAM_PATIENT, String(route.patientId))
  return url.toString()
}

/** 尚未消费的跳转意图（纯函数，不写模块变量 —— 会被 StrictMode 双调用） */
function pendingIntentRoute(externalPage?: string, externalNonce = 0): WorkbenchRoute | null {
  if (!isPageKey(externalPage ?? null)) return null
  if (externalNonce <= consumedIntentNonce) return null
  return { page: externalPage as WorkbenchPageKey, patientId: null }
}

export interface WorkbenchNav {
  route: WorkbenchRoute
  /** 是否处于患者详情页 */
  isPatientDetail: boolean
  /** 打开患者详情（压入历史） */
  openPatient: (patientId: string) => void
  /** 返回：优先浏览器后退，无历史时回患者列表 */
  goBack: () => void
}

export interface UseWorkbenchNavOptions {
  /** 系统侧栏下发的目标页；undefined 表示由域自行决定 */
  externalPage?: string
  /** 跳转意图序号：重复点击同一子项时自增 */
  externalNonce?: number
}

export function useWorkbenchNav(options: UseWorkbenchNavOptions = {}): WorkbenchNav {
  const { externalPage, externalNonce = 0 } = options
  // 首屏若带未消费的跳转意图，直接用目标页，避免先渲染 URL 旧页再跳一下
  const [route, setRoute] = useState<WorkbenchRoute>(
    () => pendingIntentRoute(externalPage, externalNonce) ?? parseRoute(),
  )
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

  // 首屏把已被采用的意图写回 URL，并记为已消费 —— 否则刷新会回到上一个页。
  // 只在挂载时执行一次；后续切换都由 intent effect / openPatient / popstate 负责。
  useEffect(() => {
    const pending = pendingIntentRoute(externalPage, externalNonce)
    consumedIntentNonce = Math.max(consumedIntentNonce, externalNonce)
    // pending 非空 = 首屏已采用意图页，需把 URL 对齐到它；
    // pending 为空 = 当前页本就来自 URL，无需改动
    if (pending) window.history.replaceState(null, '', toUrl(routeRef.current))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 系统侧栏下发新的跳转意图：nonce 自增即视为一次新意图（含重复点击同一项）
  useEffect(() => {
    if (!isPageKey(externalPage ?? null)) return
    if (externalNonce <= consumedIntentNonce) return
    consumedIntentNonce = externalNonce
    const prev = routeRef.current
    // 已在目标页顶层 = 无需任何动作
    if (prev.page === externalPage && prev.patientId === null) return
    // 在目标页的详情层 = 重置到该页顶层（同样是 replace，不动历史栈）
    // 外部跳转一律用 replaceState：避免侧栏点击污染患者详情的历史栈深度
    commit({ page: externalPage as WorkbenchPageKey, patientId: null }, false)
  }, [externalPage, externalNonce, commit])

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
    openPatient,
    goBack,
  }
}
