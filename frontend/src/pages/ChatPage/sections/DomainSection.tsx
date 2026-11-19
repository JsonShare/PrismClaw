import { Suspense, useSyncExternalStore } from 'react'
import iconMenuFold from '../../../images/other_icon/menu_fold.png'
import {
  getDomainEntry,
  getRegistryVersion,
  subscribeRegistryVersion,
} from '../../../domains/registry'
import { getDomainIntent, subscribeDomainIntent } from '../../../domains/nav-bus'
import { useUiStore } from '../../../stores/ui'
import { Loader2 } from 'lucide-react'

interface DomainSectionProps {
  domainKey: string
}

function DomainLoadingFallback() {
  return (
    <div className="flex-1 flex items-center justify-center">
      <Loader2 size={24} className="animate-spin text-[var(--c-text-secondary)]" />
    </div>
  )
}

/**
 * 业务域页面容器。
 *
 * 二级菜单已平铺到系统左侧栏，因此这里不再渲染域内侧栏，
 * 只把侧栏的下行意图（page + nonce）透传给域；
 * 域切页后自行调用 reportDomainPage() 回报，侧栏据此高亮。
 *
 * ⚠ 侧栏收起时本分区没有任何导航入口（ChatHeader 只挂在 ChatSection 上），
 * 因此这里补一条仅在收起态出现的窄工具条 —— 否则鼠标用户会困在域内。
 */
export function DomainSection({ domainKey }: DomainSectionProps) {
  // 域是异步发现的：刷新时 activeSection 可能先从 localStorage 恢复成 domain:xxx，
  // 而注册表还没填充完。此时不能直接返回 null（会白屏），要转圈等发现完成。
  const registryVersion = useSyncExternalStore(subscribeRegistryVersion, getRegistryVersion)
  const intent = useSyncExternalStore(subscribeDomainIntent, getDomainIntent)
  const sidebarCollapsed = useUiStore((s) => s.sidebarCollapsed)
  const toggleSidebar = useUiStore((s) => s.toggleSidebar)

  const entry = getDomainEntry(domainKey)
  if (!entry) {
    // 已发现过域但依然找不到 = 该域被裁剪或未启用，没有可渲染内容
    return registryVersion > 0 ? null : <DomainLoadingFallback />
  }

  const WorkbenchPage = entry.WorkbenchPage
  const hasIntent = intent.domainKey === domainKey

  return (
    <div className="flex-1 min-w-0 min-h-0 flex flex-col relative app-panel overflow-hidden">
      {/* 侧栏收起态：补一条极窄工具条承载展开按钮。
          不能用绝对定位浮层 —— 域页面左上角是页头/统计卡，浮上去会遮挡内容。 */}
      {sidebarCollapsed && (
        <div className="shrink-0 flex items-center h-9 px-2">
          <button
            type="button"
            onClick={toggleSidebar}
            className="btn btn-icon btn-ghost"
            title="展开侧边栏"
            aria-label="展开侧边栏"
          >
            <img src={iconMenuFold} alt="" className="w-5 h-5 app-icon" />
          </button>
        </div>
      )}
      {/* 域根组件自带 h-full，须包一层 flex-1 min-h-0，否则工具条会把内容挤出视口 */}
      <div className="flex-1 min-h-0 min-w-0 flex flex-col">
        <Suspense fallback={<DomainLoadingFallback />}>
          <WorkbenchPage
            page={hasIntent ? intent.page ?? undefined : undefined}
            pageNonce={hasIntent ? intent.nonce : 0}
          />
        </Suspense>
      </div>
    </div>
  )
}
