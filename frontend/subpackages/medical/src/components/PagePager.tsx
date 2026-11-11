/**
 * 医疗域 · 轻量分页组件
 *
 * 支持超大页码：页码序列用省略号压缩（1 … 4 5 6 … 1000），
 * 总页数 > 10 时提供「跳至 N 页」输入直达。
 * 总页数 <= 1 时不渲染。
 */

import { useState } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { buildPageNumbers } from '../lib/format'

interface PagePagerProps {
  /** 当前页（从 1 开始） */
  page: number
  /** 数据总条数 */
  total: number
  /** 每页条数 */
  pageSize: number
  onChange: (page: number) => void
}

export function PagePager({ page, total, pageSize, onChange }: PagePagerProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  // 防御：当前页越界（如筛选后总页数变少）时收敛到有效区间
  const cur = Math.min(Math.max(1, page || 1), totalPages)
  const [jump, setJump] = useState('')

  if (totalPages <= 1) return null

  const doJump = () => {
    const n = Number.parseInt(jump, 10)
    if (!Number.isNaN(n)) onChange(Math.min(Math.max(1, n), totalPages))
    setJump('')
  }

  return (
    <div className="flex items-center justify-between gap-3 pt-4 flex-wrap">
      <p className="text-[length:var(--fs-sm)] mw-faint">
        共 <span className="mw-num font-semibold mw-strong">{total}</span> 条记录
        <span className="mx-1.5">·</span>第 {cur} / {totalPages} 页
      </p>

      <div className="flex items-center gap-2">
        {totalPages > 10 && (
          <div className="mw-glass rounded-full flex items-center gap-1.5 px-2.5 h-9 text-[length:var(--fs-sm)] mw-faint">
            跳至
            <input
              type="text"
              value={jump}
              inputMode="numeric"
              aria-label="跳至页码"
              placeholder={String(cur)}
              onChange={(e) => setJump(e.target.value.replace(/\D/g, ''))}
              onKeyDown={(e) => {
                if (e.key === 'Enter') doJump()
              }}
              onBlur={() => jump && doJump()}
              className="w-10 h-6 rounded-full bg-[var(--c-surface-3)] text-center text-[length:var(--fs-sm)] mw-num mw-strong outline-none placeholder:text-[var(--c-text-secondary)]"
            />
            页
          </div>
        )}

        <div className="mw-glass rounded-full flex items-center gap-0.5 p-1">
          <button
            type="button"
            disabled={cur === 1}
            onClick={() => onChange(cur - 1)}
            aria-label="上一页"
            className="h-7 w-7 rounded-full flex items-center justify-center text-[var(--c-text-secondary)] transition-all disabled:opacity-35 disabled:cursor-not-allowed hover:bg-[var(--c-surface-3)] cursor-pointer"
          >
            <ChevronLeft className="h-3.5 w-3.5" />
          </button>

          {buildPageNumbers(cur, totalPages).map((p, i) =>
            p === '…' ? (
              <span key={`gap-${i}`} className="w-6 text-center text-[length:var(--fs-sm)] mw-faint select-none">
                …
              </span>
            ) : (
              <button
                key={p}
                type="button"
                onClick={() => onChange(p)}
                className={`min-w-7 h-7 px-1.5 rounded-full text-[length:var(--fs-sm)] font-medium transition-all cursor-pointer ${
                  p === cur
                    ? 'bg-[var(--c-btn-primary-bg)] text-[var(--c-btn-primary-fg)]'
                    : 'text-[var(--c-text-secondary)] hover:bg-[var(--c-surface-3)] hover:text-[var(--c-text)]'
                }`}
              >
                {p}
              </button>
            ),
          )}

          <button
            type="button"
            disabled={cur === totalPages}
            onClick={() => onChange(cur + 1)}
            aria-label="下一页"
            className="h-7 w-7 rounded-full flex items-center justify-center text-[var(--c-text-secondary)] transition-all disabled:opacity-35 disabled:cursor-not-allowed hover:bg-[var(--c-surface-3)] cursor-pointer"
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </div>
  )
}
