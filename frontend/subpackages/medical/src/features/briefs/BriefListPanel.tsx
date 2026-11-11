/**
 * 简报列表面板 — 嵌入患者详情页的右栏 Tab
 *
 * 显示该患者的简报卡片列表 + "新建简报"按钮
 * 点击卡片切换到 BriefView 详情
 */

import { useEffect, useState, useCallback } from 'react'
import { Plus, Loader2 } from 'lucide-react'
import { listBriefs, createBrief } from '../../api/client'
import { BriefCard } from './BriefCard'
import { BriefView } from './BriefView'
import type { BriefListItem } from '../../types/view'

interface BriefListPanelProps {
  patientId: string
}

export function BriefListPanel({ patientId }: BriefListPanelProps) {
  const [briefs, setBriefs] = useState<BriefListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [activeBriefId, setActiveBriefId] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  const loadBriefs = useCallback(async () => {
    try {
      const res = await listBriefs({ patientId, pageSize: 50 })
      setBriefs(res.items)
    } catch { /* 静默 */ }
    finally { setLoading(false) }
  }, [patientId])

  useEffect(() => {
    loadBriefs()
  }, [loadBriefs])

  // 分析中状态时轮询
  useEffect(() => {
    const hasAnalyzing = briefs.some((b) => b.status === '分析中')
    if (!hasAnalyzing) return
    const timer = setInterval(loadBriefs, 6000)
    return () => clearInterval(timer)
  }, [briefs, loadBriefs])

  const handleCreate = async () => {
    setCreating(true)
    try {
      const newBrief = await createBrief({ patientId })
      setBriefs((prev) => [newBrief, ...prev])
      setActiveBriefId(newBrief.id)
    } catch { /* 静默 */ }
    finally { setCreating(false) }
  }

  if (activeBriefId) {
    return (
      <BriefView
        briefId={activeBriefId}
        onBack={() => { setActiveBriefId(null); loadBriefs() }}
      />
    )
  }

  return (
    <div className="rounded-lg">
      {/* 头部 */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--c-border)]">
        <h3 className="text-[length:var(--fs-lg)] font-semibold text-[var(--c-text)]">
          研究简报
          <span className="ml-2 text-[length:var(--fs-sm)] font-normal text-[var(--c-text-secondary)]">
            {briefs.length} 份
          </span>
        </h3>
        <button
          type="button"
          onClick={handleCreate}
          disabled={creating}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-[var(--c-accent-soft)] text-[var(--c-accent)] hover:bg-[var(--c-accent)] hover:text-white text-[length:var(--fs-sm)] font-medium transition-colors disabled:opacity-50"
        >
          {creating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
          新建简报
        </button>
      </div>

      {/* 列表 */}
      <div className="px-4 py-3 space-y-2">
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-[var(--c-accent)]" />
          </div>
        ) : briefs.length === 0 ? (
          <div className="py-8 text-center">
            <p className="text-[length:var(--fs-md)] text-[var(--c-text-secondary)]">暂无简报</p>
            <p className="mt-1 text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">
              上传病历后系统会自动分析生成，或手动点击"新建简报"
            </p>
          </div>
        ) : (
          briefs.map((b) => (
            <BriefCard
              key={b.id}
              brief={b}
              onClick={() => setActiveBriefId(b.id)}
              onRefresh={loadBriefs}
            />
          ))
        )}
      </div>
    </div>
  )
}
