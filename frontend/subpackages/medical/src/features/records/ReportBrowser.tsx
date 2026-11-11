/**
 * 病历档案浏览器
 *
 * 白底+发丝边框 Card 风格，与 MedicalSummary 其他卡片一致。
 * 按医学用途分类展示，每个文件可点击弹出预览（Lightbox）。
 */

import { FolderOpen } from 'lucide-react'
import type { PatientReport } from '../../types/view'
import { ReportArchive } from './ReportArchive'

export function ReportBrowser({ reports }: { reports: PatientReport[] }) {
  return (
    <div className="rounded-lg bg-white border border-[var(--c-border)] px-4 py-3.5">
      <h3 className="text-[length:var(--fs-lg)] font-semibold text-[var(--c-accent)] mb-1">
        病历档案
        <span className="ml-2 text-[length:var(--fs-xs)] text-[var(--c-text-secondary)] font-normal">
          共 {reports.length} 份
        </span>
      </h3>

      {reports.length === 0 ? (
        <div className="flex items-center gap-2 text-[length:var(--fs-sm)] text-[var(--c-text-secondary)] py-4 justify-center">
          <FolderOpen className="h-4 w-4" />
          暂无已整理的病历资料
        </div>
      ) : (
        <ReportArchive reports={reports} />
      )}
    </div>
  )
}
