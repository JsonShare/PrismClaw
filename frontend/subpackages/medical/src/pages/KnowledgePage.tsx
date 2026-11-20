/**
 * 医学知识库 · 单页三 Tab（文献 / 指南 / 药品）
 *
 * 设计稿：顶部统一检索框 + 三 Tab（激活态绿色下划线），
 * Tab 同步 ?mw_kb=（保留在 ?domain=medical&page=knowledge 下）。
 * 三个内容区各自独立取数与筛选，互不复用旧的 facet 三栏布局。
 */

import { useState } from 'react'
import { ArrowRight, ClipboardList, FileText, Pill, type LucideIcon } from 'lucide-react'
import { DrugCatalog } from '../features/kb/DrugCatalog'
import { GuidelineFeed } from '../features/kb/GuidelineFeed'
import { LiteratureFeed } from '../features/kb/LiteratureFeed'

type KbTab = 'literature' | 'guidelines' | 'drugs'

const TABS: Array<{ key: KbTab; label: string; icon: LucideIcon; placeholder: string }> = [
  { key: 'literature', label: '搜文献', icon: FileText, placeholder: '海量医学文献任意搜…' },
  { key: 'guidelines', label: '搜指南', icon: ClipboardList, placeholder: '海量医学指南任意搜…' },
  { key: 'drugs', label: '搜药品', icon: Pill, placeholder: '权威药品说明书任意搜…' },
]

function readTab(): KbTab {
  const v = new URLSearchParams(window.location.search).get('mw_kb')
  return v === 'guidelines' || v === 'drugs' ? v : 'literature'
}

/** 写入 URL：刷新 / 分享后可回到同一 Tab */
function writeTab(tab: KbTab) {
  const url = new URL(window.location.href)
  url.searchParams.set('page', 'knowledge')
  url.searchParams.set('mw_kb', tab)
  window.history.replaceState(null, '', url.toString())
}

export function KnowledgePage() {
  const [tab, setTab] = useState<KbTab>(readTab)
  const [keyword, setKeyword] = useState('')

  const switchTab = (next: KbTab) => {
    if (next === tab) return
    setTab(next)
    setKeyword('')
    writeTab(next)
  }

  const placeholder = TABS.find((t) => t.key === tab)?.placeholder ?? ''

  return (
    <div className="mx-auto w-full max-w-[1000px] pb-10">
      <form
        onSubmit={(e) => e.preventDefault()}
        className="mt-2 flex h-12 items-center gap-3 rounded-xl border border-[var(--c-border)] bg-[var(--c-surface)] pl-4 pr-2 transition-colors focus-within:border-[var(--c-success)]"
      >
        <input
          type="text"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          placeholder={placeholder}
          className="min-w-0 flex-1 bg-transparent text-[length:var(--fs-md)] mw-strong outline-none placeholder:text-[var(--c-text-secondary)]"
        />
        <button
          type="submit"
          aria-label="搜索"
          className="flex h-8 w-8 shrink-0 cursor-pointer items-center justify-center rounded-full bg-[var(--c-success-soft)] text-[var(--c-success)] transition-all hover:brightness-95"
        >
          <ArrowRight className="h-4 w-4" />
        </button>
      </form>

      <div className="mt-6 grid grid-cols-3 gap-2 border-b border-[var(--c-border)]">
        {TABS.map(({ key, label, icon: Icon }) => {
          const active = key === tab
          return (
            <button
              key={key}
              type="button"
              onClick={() => switchTab(key)}
              className={`relative flex cursor-pointer items-center justify-center gap-2 pb-3 pt-1 text-[length:var(--fs-md)] transition-colors ${
                active ? 'font-semibold mw-strong' : 'mw-muted hover:text-[var(--c-text)]'
              }`}
            >
              <Icon className="h-4 w-4" />
              {label}
              {active && (
                <span className="absolute -bottom-px left-1/2 h-0.5 w-14 -translate-x-1/2 rounded-full bg-[var(--c-success)]" />
              )}
            </button>
          )
        })}
      </div>

      <div className="mt-5">
        {tab === 'literature' ? (
          <LiteratureFeed keyword={keyword} />
        ) : tab === 'guidelines' ? (
          <GuidelineFeed keyword={keyword} />
        ) : (
          <DrugCatalog keyword={keyword} />
        )}
      </div>
    </div>
  )
}
