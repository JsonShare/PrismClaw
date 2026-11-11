/**
 * 指南知识库 · 右栏阅读器
 *
 * 核心推荐逐条编号呈现（编号用机构色），支持收藏与下载 / 查看原文。
 */

import { Bookmark, Download, ExternalLink } from 'lucide-react'
import { Btn, Num, Panel, toneClass, useToast } from '../../components'
import { GUIDELINE_ORG_META } from '../../data/mock-data'
import type { GuidelineEntry } from '../../types/view'

interface GuidelineReaderProps {
  entry: GuidelineEntry
  favorite: boolean
  onToggleFavorite: (entry: GuidelineEntry) => void
}

export function GuidelineReader({ entry, favorite, onToggleFavorite }: GuidelineReaderProps) {
  const { toast } = useToast()
  const meta = GUIDELINE_ORG_META[entry.cat]
  const tone = toneClass(meta.tone)

  return (
    <Panel className={`overflow-hidden mw-sticky ${tone}`}>
      <div
        className="px-5 py-4 border-b border-dashed border-[var(--c-border)]"
        style={{ background: 'color-mix(in srgb, var(--mw-tone) 6%, transparent)' }}
      >
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            {/* 机构缩写徽章：直接吃 --mw-tone，不走 .mw-pill（避免被未分层的 .mw-pill 背景覆盖） */}
            <span className="text-[length:var(--fs-xs)] font-bold px-2 py-0.5 rounded bg-[var(--mw-tone)] text-[var(--c-surface)]">
              {meta.org}
            </span>
            <Num className="text-[length:var(--fs-sm)] mw-faint">{entry.ver}</Num>
          </div>
          <button
            type="button"
            onClick={() => onToggleFavorite(entry)}
            title={favorite ? '取消收藏' : '收藏'}
            aria-label={favorite ? '取消收藏' : '收藏'}
            className="rounded-full p-1.5 hover:bg-[var(--c-surface-3)] transition-colors cursor-pointer"
          >
            <Bookmark
              className={`h-4 w-4 ${
                favorite ? 'text-[var(--c-accent)] fill-[var(--c-accent)]' : 'text-[var(--c-text-secondary)]'
              }`}
            />
          </button>
        </div>

        <h3 className="text-[length:var(--fs-md)] font-bold mw-strong leading-snug mt-2">{entry.title}</h3>
        <p className="text-[length:var(--fs-sm)] mw-faint mt-1">
          {meta.full} · {entry.year} 年发布
        </p>
      </div>

      <div className="p-5">
        <p className="text-[length:var(--fs-xs)] mw-faint tracking-wider mb-1.5">概述</p>
        <p className="text-[length:var(--fs-md)] mw-muted leading-relaxed">{entry.abstract}</p>

        <p className="text-[length:var(--fs-xs)] mw-faint tracking-wider mt-4 mb-2">核心推荐</p>
        <div className="space-y-2">
          {entry.points.map((pt, i) => (
            <div
              key={pt}
              className="flex items-start gap-2.5 pb-2 border-b border-dashed border-[var(--c-border)] last:border-0"
            >
              <span
                className="h-5 w-5 shrink-0 rounded-full text-[length:var(--fs-xs)] font-semibold flex items-center justify-center mt-0.5 bg-[var(--mw-tone)] text-[var(--c-surface)]"
              >
                {i + 1}
              </span>
              <p className="text-[length:var(--fs-sm)] mw-muted leading-relaxed">{pt}</p>
            </div>
          ))}
        </div>

        <div className="flex gap-2 mt-5">
          <Btn
            size="sm"
            className="flex-1 justify-center"
            onClick={() => toast('PDF 下载任务已创建', 'success')}
          >
            <Download className="h-3.5 w-3.5" />
            下载 PDF
          </Btn>
          <Btn
            size="sm"
            variant="primary"
            className="flex-1 justify-center"
            onClick={() => toast('正在打开原文页面', 'info')}
          >
            <ExternalLink className="h-3.5 w-3.5" />
            查看原文
          </Btn>
        </div>
      </div>
    </Panel>
  )
}
