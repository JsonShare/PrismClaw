/**
 * 简报阅读视图 — 展示简报全文 + 引文交互 + 审计结果
 *
 * 布局：顶部状态栏 → 段落列表 → 审计结果
 * 每个段落内嵌引文标记（sup），悬停弹出 CitationPopover
 * 探索性段落用浅蓝底+左侧竖条标识
 */

import { useEffect, useState, useCallback } from 'react'
import { ArrowLeft, RotateCcw, Loader2, ShieldCheck, ShieldAlert } from 'lucide-react'
import { getBrief, reanalyzeBrief } from '../../api/client'
import { CitationPopover } from './CitationPopover'
import type { BriefDetail, BriefSection, CitationItem, BriefStatus } from '../../types/view'

interface BriefViewProps {
  briefId: string
  onBack: () => void
}

const STATUS_TONE: Record<BriefStatus, string> = {
  '分析中': 'text-blue-600 dark:text-blue-400',
  '已通过': 'text-emerald-600 dark:text-emerald-400',
  '待修正': 'text-amber-600 dark:text-amber-400',
  '已废弃': 'text-gray-500 dark:text-gray-400',
}

/** 轮询间隔（分析中状态时） */
const POLL_INTERVAL = 4000

export function BriefView({ briefId, onBack }: BriefViewProps) {
  const [brief, setBrief] = useState<BriefDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [reanalyzing, setReanalyzing] = useState(false)
  const [error, setError] = useState('')

  const loadBrief = useCallback(async () => {
    try {
      const b = await getBrief(briefId)
      setBrief(b)
      setError('')
    } catch {
      setError('加载简报失败')
    } finally {
      setLoading(false)
    }
  }, [briefId])

  // 初次加载 + 分析中时轮询
  useEffect(() => {
    loadBrief()
  }, [loadBrief])

  useEffect(() => {
    if (!brief || brief.status !== '分析中') return
    const timer = setInterval(loadBrief, POLL_INTERVAL)
    return () => clearInterval(timer)
  }, [brief?.status, loadBrief])

  const handleReanalyze = async () => {
    if (!brief || brief.status === '分析中') return
    setReanalyzing(true)
    try {
      await reanalyzeBrief(brief.id)
      await loadBrief()
    } catch { /* 静默 */ }
    finally { setReanalyzing(false) }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[200px]">
        <Loader2 className="h-5 w-5 animate-spin text-[var(--c-accent)]" />
      </div>
    )
  }

  if (error || !brief) {
    return (
      <div className="p-6 text-center text-[var(--c-text-secondary)]">
        <p>{error || '简报不存在'}</p>
        <button type="button" onClick={onBack} className="mt-3 text-[var(--c-accent)] hover:underline text-[length:var(--fs-sm)]">
          返回列表
        </button>
      </div>
    )
  }

  // 构建引文索引
  const citationMap = new Map<string, CitationItem>()
  for (const c of brief.citations) {
    citationMap.set(c.citationId, c)
  }

  return (
    <div className="rounded-lg bg-[var(--c-surface-2)] overflow-hidden">
      {/* 顶部状态栏 */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-[var(--c-border)]">
        <button
          type="button"
          onClick={onBack}
          className="p-1.5 rounded-md hover:bg-[var(--c-surface-2)] text-[var(--c-text-secondary)] transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
        </button>
        <div className="flex-1 min-w-0">
          <h2 className="text-[length:var(--fs-lg)] font-semibold text-[var(--c-text)]">
            研究简报
            <span className="ml-2 text-[length:var(--fs-sm)] font-normal text-[var(--c-text-secondary)]">v{brief.version}</span>
          </h2>
          <p className={`text-[length:var(--fs-xs)] ${STATUS_TONE[brief.status]}`}>
            {brief.status}
            {brief.finishedAt && ` · ${new Date(brief.finishedAt).toLocaleString('zh-CN')}`}
          </p>
        </div>
        {brief.status !== '分析中' && (
          <button
            type="button"
            onClick={handleReanalyze}
            disabled={reanalyzing}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-[var(--c-accent-soft)] text-[var(--c-accent)] hover:bg-[var(--c-accent)] hover:text-white text-[length:var(--fs-sm)] font-medium transition-colors disabled:opacity-50"
          >
            {reanalyzing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
            重新分析
          </button>
        )}
      </div>

      {/* 主体内容 */}
      <div className="px-5 py-4 max-w-[820px] mx-auto w-full">
        {brief.status === '分析中' ? (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <Loader2 className="h-8 w-8 animate-spin text-[var(--c-accent)]" />
            <p className="mt-4 text-[length:var(--fs-md)] text-[var(--c-text-secondary)]">分析进行中，请稍候…</p>
            <p className="mt-1 text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">可以离开页面，完成后自动刷新</p>
          </div>
        ) : (
          <>
            {/* 段落列表 */}
            <div className="space-y-4">
              {brief.sections.map((section) => (
                <SectionBlock
                  key={section.id}
                  section={section}
                  citationMap={citationMap}
                />
              ))}
            </div>

            {/* 审计结果 */}
            {brief.auditResult && (
              <AuditBlock audit={brief.auditResult} />
            )}

            {/* 错误信息 */}
            {brief.errorMessage && (
              <div className="mt-4 p-3 rounded-lg bg-red-50 dark:bg-red-900/10 border border-red-200 dark:border-red-900/30">
                <p className="text-[length:var(--fs-sm)] text-red-700 dark:text-red-300">{brief.errorMessage}</p>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

// ── 段落块 ──────────────────────────────────────────────────

function SectionBlock({
  section,
  citationMap,
}: {
  section: BriefSection
  citationMap: Map<string, CitationItem>
}) {
  // 解析内容中的引文标记 [cit:xxx]
  const parts = section.content.split(/(\[cit:[^\]]+\])/g)

  return (
    <div
      className={`rounded-lg border border-[var(--c-border)] p-4 ${
        section.isSpeculative
          ? 'bg-blue-50/60 dark:bg-blue-900/10 border-l-[3px] border-l-blue-400 dark:border-l-blue-600'
          : 'bg-[var(--c-surface-2)]'
      }`}
    >
      <div className="flex items-center gap-2 mb-2">
        <h3 className="text-[length:var(--fs-md)] font-semibold text-[var(--c-text)]">
          {section.title}
        </h3>
        {section.isSpeculative && (
          <span className="px-1.5 py-0.5 rounded text-[length:var(--fs-xs)] font-medium bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300">
            探索性
          </span>
        )}
      </div>
      <div className="text-[length:var(--fs-md)] text-[var(--c-text-secondary)] leading-relaxed">
        {parts.map((part, i) => {
          const match = part.match(/^\[cit:([^\]]+)\]$/)
          if (match) {
            const citId = match[1]
            const citation = citationMap.get(citId)
            if (citation) {
              return (
                <CitationPopover key={i} citation={citation} trigger="hover">
                  <sup className="cursor-help text-[var(--c-accent)] hover:underline font-medium">
                    [{citId.slice(0, 6)}]
                  </sup>
                </CitationPopover>
              )
            }
            return <sup key={i} className="text-[var(--c-text-secondary)]">[{citId.slice(0, 6)}]</sup>
          }
          return <span key={i}>{part}</span>
        })}
      </div>
    </div>
  )
}

// ── 审计结果块 ──────────────────────────────────────────────

function AuditBlock({ audit }: { audit: { passed: boolean; violations?: Array<{ rule: string; citationId: string; detail: string }> } }) {
  if (audit.passed && (!audit.violations || audit.violations.length === 0)) {
    return (
      <div className="mt-4 flex items-center gap-2 p-3 rounded-lg bg-emerald-50 dark:bg-emerald-900/10 border border-emerald-200 dark:border-emerald-900/30">
        <ShieldCheck className="h-4 w-4 text-emerald-600 dark:text-emerald-400 shrink-0" />
        <span className="text-[length:var(--fs-sm)] text-emerald-700 dark:text-emerald-300 font-medium">审计通过</span>
      </div>
    )
  }

  return (
    <div className="mt-4 rounded-lg bg-amber-50 dark:bg-amber-900/10 border border-amber-200 dark:border-amber-900/30 p-3">
      <div className="flex items-center gap-2 mb-2">
        <ShieldAlert className="h-4 w-4 text-amber-600 dark:text-amber-400 shrink-0" />
        <span className="text-[length:var(--fs-sm)] text-amber-700 dark:text-amber-300 font-medium">
          审计发现 {audit.violations?.length ?? 0} 条问题
        </span>
      </div>
      {audit.violations && audit.violations.length > 0 && (
        <ul className="space-y-1">
          {audit.violations.map((v, i) => (
            <li key={i} className="text-[length:var(--fs-xs)] text-amber-700 dark:text-amber-300">
              <span className="font-medium">{v.rule}</span>
              {v.citationId && <span className="mx-1">·</span>}
              {v.citationId && <span>{v.citationId}</span>}
              {v.detail && <span className="ml-1 text-amber-600 dark:text-amber-400">— {v.detail}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
