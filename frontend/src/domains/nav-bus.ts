/**
 * 域内页面切换总线 — 核心侧栏 ↔ 业务域的窄接口
 *
 * 二级菜单已平铺到系统左侧栏（SidebarNav），但页面状态仍归域自己所有
 * （域内 URL 同步、患者详情等深层状态不外泄）。这里只转递两类信号：
 *
 *   下行 intent  ：侧栏点击子项 → { domainKey, page, nonce }
 *                  域消费后切页；nonce 自增，保证重复点击同一项也能「回到该页顶层」
 *   上行 active  ：域汇报当前页 → 侧栏据此高亮
 *
 * 两个通道刻意分离：若共用一份状态，域回报会引起下行 effect 再次触发，
 * 形成「后退到患者详情又被重置」的回环。
 */

type Listener = () => void

export interface DomainNavIntent {
  domainKey: string | null
  page: string | null
  /** 点击序号：每次点击自增（含重复点击同一子项） */
  nonce: number
}

export interface DomainActivePage {
  domainKey: string | null
  page: string | null
}

/* ─── 状态 ─── */

const EMPTY_INTENT: DomainNavIntent = { domainKey: null, page: null, nonce: 0 }
const EMPTY_ACTIVE: DomainActivePage = { domainKey: null, page: null }

let intent: DomainNavIntent = EMPTY_INTENT
let active: DomainActivePage = EMPTY_ACTIVE
const listeners = new Set<Listener>()

function emit() {
  for (const fn of listeners) fn()
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/* ─── 下行：侧栏 → 域 ─── */

export function subscribeDomainIntent(listener: Listener): () => void {
  return subscribe(listener)
}

export function getDomainIntent(): DomainNavIntent {
  return intent
}

/** 侧栏点击某个域的子项 */
export function requestDomainPage(domainKey: string, page: string): void {
  intent = { domainKey, page, nonce: intent.nonce + 1 }
  // 乐观高亮：域挂载后会以真实页回报并纠正
  active = { domainKey, page }
  emit()
}

/* ─── 上行：域 → 侧栏 ─── */

export function subscribeDomainActive(listener: Listener): () => void {
  return subscribe(listener)
}

export function getDomainActive(): DomainActivePage {
  return active
}

/** 域汇报当前页，驱动侧栏高亮 */
export function reportDomainPage(domainKey: string, page: string): void {
  if (active.domainKey === domainKey && active.page === page) return
  active = { domainKey, page }
  emit()
}
