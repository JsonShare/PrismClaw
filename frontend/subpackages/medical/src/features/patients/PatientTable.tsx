/**
 * 患者管理 · 花名册表格
 *
 * 点击整行进入患者详情（独立页面，非弹层），由外层通过 onOpen 驱动路由。
 */

import { ArrowLeft } from 'lucide-react'
import { Pill } from '../../components'
import { RISK_TONE } from '../../lib/tone'
import type { PatientProfile } from '../../types/view'

/** 列宽模板：表头与数据行共用，改动必须两处同步 */
const COLS = 'grid-cols-[0.9fr_0.4fr_0.4fr_0.5fr_1.9fr_1fr_1.4fr_32px]'

const HEADERS = ['姓名', '性别', '年龄', '风险', '诊断 / 分期', '手机号', '证件号', '']
/** 需要居中的列 */
const CENTERED = new Set(['性别', '年龄', '风险'])

interface PatientTableProps {
  rows: PatientProfile[]
  onOpen: (patientId: string) => void
}

export function PatientTable({ rows, onOpen }: PatientTableProps) {
  return (
    <div>
      <div className={`grid ${COLS} gap-2 px-3 pb-2 border-b border-[var(--c-divider)]`}>
        {HEADERS.map((h) => (
          <span
            key={h}
            className={`text-[length:var(--fs-sm)] mw-faint ${CENTERED.has(h) ? 'text-center' : ''}`}
          >
            {h}
          </span>
        ))}
      </div>

      <div>
        {rows.map((p) => (
          <div
            key={p.id}
            role="button"
            tabIndex={0}
            onClick={() => onOpen(p.id)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                onOpen(p.id)
              }
            }}
            className={`mw-pat-row grid ${COLS} gap-2 items-center px-3 py-3 cursor-pointer transition-colors hover:bg-[var(--c-surface-2)]`}
          >
            <p className="text-[length:var(--fs-md)] font-semibold mw-strong truncate">{p.name}</p>
            <p className="text-[length:var(--fs-sm)] mw-muted text-center">{p.gender}</p>
            <p className="text-[length:var(--fs-sm)] mw-num mw-muted text-center">{p.age}</p>
            <Pill tone={RISK_TONE[p.risk]} className="mx-auto">
              {p.risk}
            </Pill>
            <p className="text-[length:var(--fs-sm)] mw-muted truncate">
              {p.diagnosis} <span className="text-[length:var(--fs-xs)] mw-faint mw-num">{p.stage}</span>
            </p>
            <p className="text-[length:var(--fs-sm)] mw-num mw-muted truncate">{p.phone}</p>
            <p className="text-[length:var(--fs-sm)] mw-num mw-muted truncate">{p.idNumber}</p>
            <span className="h-6 w-6 rounded-full bg-[var(--c-surface-2)] flex items-center justify-center">
              <ArrowLeft className="h-3 w-3 rotate-180 text-[var(--c-accent)]" />
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
