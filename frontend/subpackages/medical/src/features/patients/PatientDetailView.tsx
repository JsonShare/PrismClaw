/**
 * 患者详情页 · 两栏布局（从零设计）
 *
 * 左侧：患者档案区（flex-1）
 *   ─ 顶部：患者基本信息条（横排）
 *   ─ 中间：患者概览区（并列卡片）
 *   ─ 底部：病情档案区（治疗时间线 + 病历档案 并列）
 * 右侧 800px（可拖拽至 1000px）：AI 咨询
 *
 * 不再有三栏布局、左侧 240px 身份卡
 */

import { MessageSquare } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Btn } from '../../components'
import type { PatientProfile, ConsultationEntry, PatientReport } from '../../types/view'
import { listConsultations, listPatientReports } from '../../api/client'
import { PatientIdentityBanner } from './PatientIdentityBanner'
import { MedicalSummary } from './MedicalSummary'
import { ReportBrowser } from '../records/ReportBrowser'
import { MedicalChatArea } from '../consultation/MedicalChatArea'
import { ProgressRing } from './ProgressRing'
import { BriefListPanel } from '../briefs/BriefListPanel'

const CHAT_RATIO_DEFAULT = 0.42  /* 右栏占视窗比 */
const CHAT_RATIO_MIN = 0.25
const CHAT_RATIO_MAX = 0.55

interface PatientDetailViewProps {
  patient: PatientProfile
  onBack: () => void
}

export function PatientDetailView({ patient, onBack }: PatientDetailViewProps) {
  const [consultations, setConsultations] = useState<ConsultationEntry[]>([])
  const [showChat, setShowChat] = useState(true)
  const [chatRatio, setChatRatio] = useState(CHAT_RATIO_DEFAULT)
  const [activeSessionId, setActiveSessionId] = useState<string | undefined>()
  const [reports, setReports] = useState<PatientReport[]>([])
  const draggingRef = useRef(false)

  useEffect(() => {
    let cancelled = false
    listPatientReports(patient.id)
      .then((res) => { if (!cancelled) setReports(res.items) })
      .catch(() => { /* 静默 */ })
    return () => { cancelled = true }
  }, [patient.id])

  useEffect(() => {
    let cancelled = false
    listConsultations({ patientId: patient.id })
      .then((list) => { if (!cancelled) setConsultations(list) })
      .catch(() => { /* 静默 */ })
    return () => { cancelled = true }
  }, [patient.id])

  const handleStartConsultation = useCallback(() => {
    setActiveSessionId(undefined)
    setShowChat(true)
  }, [])

  const handleOpenConsultation = useCallback((sessionId: string) => {
    setActiveSessionId(sessionId)
    setShowChat(true)
  }, [])

  /** 拖拽调整咨询栏比例 */
  const layoutRef = useRef<HTMLDivElement>(null)
  const handleDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    draggingRef.current = true
    const startX = e.clientX
    const startRatio = chatRatio
    const layout = layoutRef.current
    if (!layout) return
    const layoutWidth = layout.getBoundingClientRect().width
    const onMove = (ev: MouseEvent) => {
      if (!draggingRef.current) return
      const delta = startX - ev.clientX // 向左拖=右栏变宽
      const ratioDelta = delta / layoutWidth
      const next = Math.min(CHAT_RATIO_MAX, Math.max(CHAT_RATIO_MIN, startRatio + ratioDelta))
      setChatRatio(next)
    }
    const onUp = () => {
      draggingRef.current = false
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [chatRatio])

  const allParsing = reports.length > 0 && reports.every((r) => r.state === '解析中')

  if (allParsing) {
    return <ParsingFullScreen name={patient.name} total={reports.length} />
  }

  return (
    <div
      ref={layoutRef}
      className="patient-detail-layout"
      style={{
        '--detail-left': `${(1 - chatRatio) * 100}%`,
        '--detail-right': `${chatRatio * 100}%`,
      } as React.CSSProperties}
    >
      {/* ══ 左侧：患者档案区 ══ */}
      <div className="patient-detail-left">
        {/* 顶部：基本信息条 */}
        <PatientIdentityBanner patient={patient} reports={reports} onBack={onBack} />

        {/* 中间：概览 + 底部档案（可滚动）
            间距一律用 mt-3 表达，不再用空 div 撑高：
            左栏是 flex column，两种写法占位完全相同（实测 11.25px），
            但空盒子在结构上是「什么都不画的块」，会与真残留容器无法区分。 */}
        <div className="mt-3 flex-1 min-h-0 overflow-y-auto px-5 py-4" style={{containerType: "inline-size"}}>
          {/* 中间：患者概览 + 底部档案（一体） */}
          <MedicalSummary patient={patient}>
            {/* 底部：病历档案区 */}
            <div className="mt-3 space-y-3">
              {reports.length > 0 && <ReportBrowser reports={reports} />}
            </div>
          </MedicalSummary>

          {/* 研究简报 */}
          <div className="mt-3">
            <BriefListPanel patientId={patient.id} />
          </div>

          {/* 历史咨询 */}
          {consultations.length > 0 && (
            <div className="mt-3 rounded-lg bg-[var(--c-surface-2)] px-4 py-3.5">
              <h3 className="text-[length:var(--fs-lg)] font-semibold text-[var(--c-text)] mb-2.5">
                历史咨询
                <span className="ml-2 text-[length:var(--fs-sm)] text-[var(--c-text-secondary)] font-normal">共 {consultations.length} 条</span>
              </h3>
              <div className="space-y-1.5">
                {consultations.slice(0, 5).map((c) => (
                  <button
                    key={c.id}
                    type="button"
                    onClick={() => handleOpenConsultation(c.sessionId)}
                    className="w-full flex items-center gap-2 px-3 py-2 rounded-md text-left bg-[var(--c-surface-3)] hover:bg-[var(--c-accent-soft)] cursor-pointer transition-colors"
                  >
                    <MessageSquare className="h-3 w-3 shrink-0 text-[var(--c-text-secondary)]" />
                    <span className="flex-1 truncate text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">{c.title || '咨询会话'}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ══ 拖拽把手（桌面端） ══ */}
      {showChat && (
        <div
          className="layout-drag-handle"
          onMouseDown={handleDragStart}
          title="拖拽调整宽度"
        >
          <div className="grip-dots">
            <span className="grip-dot" />
            <span className="grip-dot" />
            <span className="grip-dot" />
          </div>
        </div>
      )}

      {/* ══ 右侧：AI 咨询 ══ */}
      {showChat ? (
        <div className="patient-detail-chat">
          <div className="chat-inner">
            <div className="shrink-0 flex items-center gap-2.5 px-4 py-2.5">
              <div className="w-[3px] h-4 rounded-full bg-[var(--c-accent)] shrink-0" />
              <div className="min-w-0">
                <p className="text-[length:var(--fs-sm)] font-semibold text-[var(--c-text)] truncate">{patient.name}</p>
                <p className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)] truncate">AI 助手 · 基于该患者档案</p>
              </div>
            </div>

            <div className="flex-1 min-h-0 flex flex-col">
              <MedicalChatArea patient={patient} initialSessionId={activeSessionId} />
            </div>
          </div>
        </div>
      ) : (
        <div className="shrink-0 flex items-start pt-3 pr-3">
          <Btn size="sm" onClick={handleStartConsultation}>
            <MessageSquare className="h-3.5 w-3.5" />
            AI 咨询
          </Btn>
        </div>
      )}
    </div>
  )
}

/** 解析中全屏态 */
function ParsingFullScreen({ name, total }: { name: string; total: number }) {
  return (
    <div className="h-full w-full grid place-items-center bg-[var(--c-surface-2)]">
      <div className="flex flex-col items-center text-center max-w-sm px-6">
        <ProgressRing progress={0.3} size={120} stroke={12}>
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--c-accent)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="20 6 9 17 4 12" />
          </svg>
        </ProgressRing>
        <h2 className="mt-6 text-[length:var(--fs-xl)] font-semibold text-[var(--c-text)] leading-tight">病历已收到，正在安全整理</h2>
        <p className="mt-2.5 text-[length:var(--fs-md)] text-[var(--c-text-secondary)] leading-relaxed">{name} 的 {total} 份档案正在识别中，可以离开页面，整理完成后再回来查看。</p>
      </div>
    </div>
  )
}
