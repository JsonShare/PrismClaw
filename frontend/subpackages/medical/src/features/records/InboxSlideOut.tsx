/**
 * 待指派 · 右侧常驻侧窗（覆盖式，始终全宽展开）
 *
 * 行为：
 * - 不再有触发按钮：有待分配项（inbox.length>0）时侧窗常显示，始终全宽展开、不折叠；
 *   open 由父组件按 count 派生，清空时滑出卸载。
 * - 无全屏透明遮罩：常驻面板不拦截主视图点击，仅面板自身可交互。
 */

import { useEffect, useState } from 'react'
import { Inbox } from 'lucide-react'
import { resolveReportType } from '../../lib/reportTypes'
import type { PatientReport } from '../../types/view'

interface InboxSlideOutProps {
  /** 是否展开（默认收起，由父组件 inboxOpen 控制；点击 × 收起） */
  open: boolean
  /** 待指派文档列表（未归档报告） */
  reports: PatientReport[]
  /** 点卡片 → 开指派抽屉（父组件 openAssign(report, 'assign', candidates)） */
  onPick: (report: PatientReport) => void
  /** 点击 × 收起侧窗（由父组件把 inboxOpen 置 false） */
  onClose?: () => void
}

const TERMINAL_STATUS = ['已解析', '失败']

export function InboxSlideOut({ open, reports, onPick, onClose }: InboxSlideOutProps) {
  // ── 退出动画：保持挂载到滑出完成再卸载 ──
  const [render, setRender] = useState(open)
  const [closing, setClosing] = useState(false)
  useEffect(() => {
    if (open) {
      setRender(true)
      setClosing(false)
    } else if (render) {
      setClosing(true)
      const t = setTimeout(() => { setRender(false); setClosing(false) }, 240)
      return () => clearTimeout(t)
    }
  }, [open, render])

  if (!render) return null

  return (
    <aside
      className={`inbox-slideout${closing ? ' closing' : ''}`}
      role="complementary"
      aria-label="待指派"
    >
      <div className="inbox-slideout-header">
        <div className="inbox-slideout-title">
          待指派
          {reports.length > 0 && <span className="inbox-slideout-count">{reports.length}</span>}
        </div>
        {onClose && (
          <button
            type="button"
            className="inbox-close"
            aria-label="收起待指派"
            onClick={onClose}
          >
            ×
          </button>
        )}
      </div>

      <div className="inbox-slideout-body">
        {reports.length === 0 ? (
          <div className="inbox-empty">
            <Inbox className="h-8 w-8 opacity-30" />
            <span>暂无待指派文档</span>
          </div>
        ) : (
          reports.map((r) => (
            <button
              key={r.id}
              type="button"
              className="report-card inbox-report-card"
              data-status={r.status}
              onClick={() => onPick(r)}
            >
              <div className="report-line1">
                <span className="report-type-tag">{resolveReportType(r.reportType).label}</span>
                <span className="report-name">{r.name}</span>
              </div>
              <div className="report-line2">
                {!TERMINAL_STATUS.includes(r.status)
                  ? `解析中 · ${r.status} ${r.progress}%`
                  : r.errorMessage
                    ? `解析失败：${r.errorMessage}`
                    : r.patientName && r.patientName !== r.id
                      ? `疑似 ${r.patientName}`
                      : '未识别患者'}
              </div>
            </button>
          ))
        )}
      </div>
    </aside>
  )
}
