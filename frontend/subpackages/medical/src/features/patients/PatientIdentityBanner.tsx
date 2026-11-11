/**
 * 患者基本信息条 · 顶部横排
 *
 * 顶部单行横排：返回 + 姓名/风险 + 基本信息 + 过敏/状态/上传
 * 不再是左侧 240px 竖排身份卡
 */

import { ArrowLeft, CheckCircle2, Loader2, Upload } from 'lucide-react'
import { Pill } from '../../components'
import { RISK_TONE } from '../../lib/tone'
import type { PatientProfile, PatientReport } from '../../types/view'

interface PatientIdentityBannerProps {
  patient: PatientProfile
  reports: PatientReport[]
  onBack?: () => void
}

export function PatientIdentityBanner({ patient, reports, onBack }: PatientIdentityBannerProps) {
  const parsing = reports.filter((r) => r.state === '解析中').length
  const done = reports.length - parsing

  return (
    <div className="shrink-0 px-5 py-3">
      <div className="flex items-center gap-4">
        {/* 返回按钮 */}
        {onBack && (
          <button
            type="button"
            onClick={onBack}
            className="h-7 w-7 rounded-md grid place-items-center text-[var(--c-text-secondary)] hover:text-[var(--c-text)] hover:bg-[var(--c-surface-2)] transition-colors cursor-pointer shrink-0"
            title="返回"
          >
            <ArrowLeft className="h-4 w-4" />
          </button>
        )}

        {/* 左：姓名 + 风险 */}
        <div className="flex items-center gap-2 shrink-0">
          <h1 className="text-[length:var(--fs-xl)] font-bold text-[var(--c-text)] leading-tight">
            {patient.name}
          </h1>
          {patient.risk && (
            <Pill tone={RISK_TONE[patient.risk]}>{patient.risk}</Pill>
          )}
        </div>

        {/* 分隔 */}
        <div className="h-5 w-px bg-[var(--c-border)] shrink-0" />

        {/* 中：基本信息键值 */}
        <div className="flex items-center gap-3 flex-wrap text-[length:var(--fs-sm)]">
          {patient.gender && <KV label="性别" value={patient.gender} />}
          <KV label="年龄" value={`${patient.age} 岁`} />
          {patient.department && <KV label="科室" value={patient.department} />}
          <KV label="ID" value={patient.id} />
          {patient.diagnosis && <KV label="诊断" value={patient.diagnosis} />}
          {patient.stage && <KV label="分期" value={patient.stage} valueClassName="text-[var(--c-accent)] font-medium" />}
        </div>

        {/* 右：过敏 + 状态 + 上传 */}
        <div className="ml-auto flex items-center gap-3 shrink-0">
          {/* 过敏 */}
          {patient.allergies.length > 0 && (
            <div className="flex items-center gap-1">
              {patient.allergies.map((a) => (
                <span
                  key={a}
                  className="inline-flex items-center px-1.5 py-0.5 rounded-full bg-[var(--c-danger-soft)] text-[var(--c-danger)] text-[length:var(--fs-xs)] font-medium"
                >
                  {a}
                </span>
              ))}
            </div>
          )}

          {/* 整理状态 */}
          {reports.length > 0 && (
            <div className="flex items-center gap-1.5">
              {parsing > 0 ? (
                <>
                  <Loader2 className="h-3 w-3 text-[var(--c-accent)] animate-spin shrink-0" />
                  <span className="text-[length:var(--fs-xs)] text-[var(--c-accent)]">{parsing} 份识别中</span>
                </>
              ) : (
                <>
                  <CheckCircle2 className="h-3 w-3 text-[var(--c-success)] shrink-0" />
                  <span className="text-[length:var(--fs-xs)] text-[var(--c-success)]">已整理</span>
                </>
              )}
              <span className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]">{done}/{reports.length}</span>
            </div>
          )}

          {/* 上传 */}
          <button
            type="button"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[var(--c-accent)] text-[length:var(--fs-sm)] text-white font-medium hover:opacity-90 transition-opacity cursor-pointer"
          >
            <Upload className="h-3.5 w-3.5" />
            上传病历
          </button>
        </div>
      </div>
    </div>
  )
}

/** 键值：label 左灰 + value 右黑 */
function KV({ label, value, valueClassName }: { label: string; value?: string; valueClassName?: string }) {
  if (!value) return null
  return (
    <span className="inline-flex items-baseline gap-1">
      <span className="text-[var(--c-text-secondary)]">{label}</span>
      <span className={`text-[var(--c-text)] ${valueClassName ?? ''}`}>{value}</span>
    </span>
  )
}
