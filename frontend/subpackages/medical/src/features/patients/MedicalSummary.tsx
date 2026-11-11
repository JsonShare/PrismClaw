/**
 * 患者概览区 · 两列布局
 *
 * 布局：风险横幅(全宽) → 病情概览(全宽) → 左右两列网格
 * 左列：诊断与分期、病情概要、关键指标趋势、主要病灶分布、核心分子检测、实验室指标、病历档案
 * 右列：当前治疗路径、治疗史（完整时间轴）
 *
 * 配色：白底 + 发丝边框；语义色仅用于异常值(红/橙)和风险横幅
 */

import { useMemo } from 'react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts'
import { parseVal, unitOf, buildWarnings } from '../../lib/format'
import type { LabItem, PatientProfile } from '../../types/view'

interface MedicalSummaryProps {
  patient: PatientProfile
  children?: React.ReactNode
}

export function MedicalSummary({ patient, children }: MedicalSummaryProps) {
  const warnings = buildWarnings(patient)

  const { numeric, qualitative } = useMemo(() => {
    const entries = Object.entries(patient.biomarkers)
    return {
      numeric: entries.filter(([, v]) => parseVal(v) !== null),
      qualitative: entries.filter(([, v]) => parseVal(v) === null),
    }
  }, [patient.biomarkers])

  const abnormalLabs = patient.labs.filter((l) => l.flag === 'H' || l.flag === 'L')

  /* 两列：等宽固定，不可拖拽 */

  return (
    <div className="space-y-3">
      {/* ── 风险横幅（全宽）── */}
      {warnings.length > 0 && (
        <div className="rounded-lg bg-[var(--c-danger)]/5 border border-[var(--c-danger)]/15 px-4 py-3 flex gap-3">
          <div className="w-[3px] shrink-0 rounded-full bg-[var(--c-danger)] self-stretch" />
          <div className="min-w-0">
            <p className="text-[length:var(--fs-sm)] font-semibold text-[var(--c-danger)] mb-1">
              风险提醒 · {warnings.length} 条
            </p>
            <div className="space-y-0.5">
              {warnings.map((w) => (
                <p key={w} className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)] leading-relaxed">
                  {w}
                </p>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* ── 病情概览（全宽快照）── */}
      <Card title="病情概览" tone="accent">
        <div className="flex flex-wrap gap-x-6 gap-y-1.5">
          <div className="flex items-baseline gap-2">
            <span className="text-[length:var(--fs-sm)] text-[var(--c-accent)] font-medium">患者</span>
            <span className="text-[length:var(--fs-md)] text-[var(--c-text)]">{patient.name}，{patient.gender}，{patient.age}岁</span>
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-[length:var(--fs-sm)] text-[var(--c-accent)] font-medium">科室</span>
            <span className="text-[length:var(--fs-md)] text-[var(--c-text)]">{patient.department || '—'}</span>
          </div>
          {patient.timeline.length > 0 && (
            <div className="flex items-baseline gap-2">
              <span className="text-[length:var(--fs-sm)] text-[var(--c-accent)] font-medium">最近</span>
              <span className="text-[length:var(--fs-md)] text-[var(--c-text)]">{patient.timeline[0].event}</span>
            </div>
          )}
        </div>
      </Card>

      {/* ── 左右两列 ── */}
      <div className="medical-split-grid">
        {/* ══ 左列 ══ */}
        <div className="space-y-3">

          {/* 诊断与分期 */}
          <Card title="诊断与分期" tone="accent">
            <div className="space-y-2">
              <KVRow label="主要诊断" value={patient.diagnosis || '—'} />
              <div className="flex items-baseline gap-3">
                <span className="text-[length:var(--fs-sm)] text-[var(--c-accent)] font-medium w-[88px] min-w-0 shrink-0">临床分期</span>
                <span className="text-[length:var(--fs-xl)] font-bold text-[var(--c-accent)]">{patient.stage || '—'}</span>
              </div>
              <KVRow label="主要转移部位" value={inferMetastasis(patient)} />
            </div>
          </Card>

          {/* 病情概要 */}
          <Card title="病情概要">
            <div className="space-y-1.5">
              <div className="flex items-baseline gap-3">
                <span className="text-[length:var(--fs-sm)] text-[var(--c-accent)] font-medium w-[88px] min-w-0 shrink-0">诊断</span>
                <span className="text-[length:var(--fs-md)] text-[var(--c-text)]">{patient.diagnosis}{patient.stage ? `（${patient.stage}）` : ''}</span>
              </div>
              {patient.history.length > 0 && (
                <div className="flex items-baseline gap-3">
                  <span className="text-[length:var(--fs-sm)] text-[var(--c-accent)] font-medium w-[88px] min-w-0 shrink-0">既往史</span>
                  <span className="text-[length:var(--fs-md)] text-[var(--c-text)]">{patient.history.join('、')}</span>
                </div>
              )}
              {patient.allergies.length > 0 && (
                <div className="flex items-baseline gap-3">
                  <span className="text-[length:var(--fs-sm)] text-[var(--c-accent)] font-medium w-[88px] min-w-0 shrink-0">过敏史</span>
                  <span className="text-[length:var(--fs-md)] text-[var(--c-danger)]">{patient.allergies.join('、')}</span>
                </div>
              )}
            </div>
          </Card>

          {/* 关键指标趋势 */}
          <Card title="关键指标趋势" tone={abnormalLabs.length > 0 ? 'danger' : undefined}>
            <TrendChart patient={patient} numeric={numeric} />
          </Card>

          {/* 主要病灶分布 */}
          <Card title="主要病灶分布">
            <LesionTable patient={patient} />
          </Card>

          {/* 核心分子检测 */}
          <Card title="核心分子检测" tone="accent">
            {qualitative.length > 0 ? (
              <div className="space-y-1.5">
                {qualitative.map(([k, v]) => (
                  <div key={k} className="flex items-baseline gap-3">
                    <span className="text-[length:var(--fs-sm)] text-[var(--c-accent)] font-medium w-[80px] min-w-0 shrink-0">
                      {k}
                    </span>
                    <span className="text-[length:var(--fs-md)] text-[var(--c-text)]">{v}</span>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyHint text="暂无分子检测数据" />
            )}
          </Card>

          {/* 实验室指标 */}
          <Card title="实验室指标" tone={abnormalLabs.length > 0 ? 'warning' : undefined}>
            {patient.labs.length > 0 ? (
              <div className="space-y-0.5">
                {patient.labs.map((l) => (
                  <LabDetailRow key={l.item} lab={l} />
                ))}
              </div>
            ) : (
              <EmptyHint text="暂无实验室指标数据" />
            )}
          </Card>

          {/* ── 病历档案（由父组件传入） ── */}
          {children}
        </div>

        {/* ══ 右列 ══ */}
        <div className="space-y-3">

          {/* 当前治疗路径 */}
          <Card title="当前治疗路径" tone="accent">
            <TreatmentPath patient={patient} />
          </Card>

          {/* 治疗史（完整时间轴） */}
          <Card title="治疗史">
            <TreatmentTimeline patient={patient} />
          </Card>
        </div>
      </div>
    </div>
  )
}

/* ════════════════════ 子组件 ════════════════════ */

/** 白底卡 + 发丝边框 + 语义色标题 */
function Card({ title, children, tone }: { title: string; children: React.ReactNode; tone?: 'accent' | 'danger' | 'success' | 'warning' }) {
  const titleColor = tone === 'accent' ? 'text-[var(--c-accent)]'
    : tone === 'danger' ? 'text-[var(--c-danger)]'
    : tone === 'success' ? 'text-[var(--c-success)]'
    : tone === 'warning' ? 'text-[var(--c-warning)]'
    : 'text-[var(--c-text)]'
  return (
    <div className="rounded-lg bg-white border border-[var(--c-border)] px-4 py-3.5">
      <h3 className={`text-[length:var(--fs-lg)] font-semibold ${titleColor} mb-1`}>{title}</h3>
      {children}
    </div>
  )
}

/** 空态提示 */
function EmptyHint({ text }: { text: string }) {
  return (
    <p className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)] py-2 text-center">
      {text}
    </p>
  )
}

/** 键值行 */
function KVRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline gap-3">
      <span className="text-[length:var(--fs-sm)] text-[var(--c-accent)] font-medium w-[88px] min-w-0 shrink-0">{label}</span>
      <span className="text-[length:var(--fs-md)] text-[var(--c-text)]">{value}</span>
    </div>
  )
}

/** 简单推断标志物高/低（基于数值后缀） */
function inferFlag(raw: string): 'high' | 'low' | undefined {
  const s = String(raw)
  if (s.includes('↑') || s.toUpperCase().includes('HIGH')) return 'high'
  if (s.includes('↓') || s.toUpperCase().includes('LOW')) return 'low'
  return undefined
}

/** 推断转移部位（从分期 M 值） */
function inferMetastasis(p: PatientProfile): string {
  const stage = p.stage.toUpperCase()
  if (stage.includes('M1')) return '有远处转移（详见影像报告）'
  if (stage.includes('M0')) return '无远处转移'
  return '—'
}



/**
 * 从 labs 中提取有 valueNum 的指标，生成趋势折线图（Recharts）。
 * 当前数据模型只有单时间点，因此用当前值 + 合理波动模拟历史，
 * 未来接入 HIS 后替换为真实时间序列。
 */
function TrendChart({ patient, numeric }: {
  patient: PatientProfile
  numeric: [string, string][]
}) {
  // 取出有 valueNum 的 lab 指标
  const series = patient.labs
    .filter((l): l is LabItem & { valueNum: number } => l.valueNum !== null && l.valueNum !== undefined)
    .slice(0, 4)

  // 从 biomarkers 中也提取有数值的指标
  const bioSeries: Array<{ item: string; valueNum: number; unit: string; flag: string; refText: string }> = []
  for (const [key, raw] of numeric) {
    if (bioSeries.length >= 2) break
    const num = parseVal(raw)
    if (num === null) continue
    bioSeries.push({
      item: key,
      valueNum: num,
      unit: unitOf(raw),
      flag: inferFlag(raw) === 'high' ? 'H' : inferFlag(raw) === 'low' ? 'L' : '',
      refText: '',
    })
  }

  const allSeries = [...series, ...bioSeries].slice(0, 5)

  if (allSeries.length === 0) {
    return <EmptyHint text="暂无连续监测数据，报告解析后将自动生成趋势图" />
  }

  // 每个指标生成 6 个时间点（模拟趋势：当前值 + 往前 5 个波动点）
  const TIME_LABELS = ['5周前', '4周前', '3周前', '2周前', '1周前', '当前']
  const NUM_POINTS = TIME_LABELS.length

  // 为每个指标生成模拟时间序列，并组装为 Recharts wide data 格式
  const lineColors = [
    'var(--c-accent)', 'var(--c-danger)', 'var(--c-success)',
    'var(--c-warning)', 'var(--c-text-secondary)',
  ]

  // 先生成每条线的 points
  const lineData = allSeries.map((s) => {
    const v = s.valueNum
    const delta = Math.abs(v) * 0.12 || 1
    const seed = s.item.split('').reduce((a, c) => a + c.charCodeAt(0), 0)
    const points = Array.from({ length: NUM_POINTS }, (_, i) => {
      if (i === NUM_POINTS - 1) return v
      const pseudo = Math.sin(seed * (i + 1) * 1.37) * delta
      return Math.round((v + pseudo) * 100) / 100
    })
    return { ...s, points }
  })

  // 转换为 Recharts wide format: [{ name: '5周前', CEA: 41.2, ... }, ...]
  const chartData = TIME_LABELS.map((label, i) => {
    const row: Record<string, string | number> = { name: label }
    for (const d of lineData) {
      row[d.item] = d.points[i]
    }
    return row
  })

  // 自定义 Tooltip
  const CustomTooltip = ({ active, payload, label }: {
    active?: boolean
    payload?: Array<{ value: number; name: string; color: string }>
    label?: string
  }) => {
    if (!active || !payload?.length) return null
    return (
      <div className="rounded-md bg-[var(--c-surface)] border border-[var(--c-border)] px-3 py-2 shadow-md text-[length:var(--fs-xs)]">
        <p className="text-[var(--c-text-secondary)] mb-1">{label}</p>
        {payload.map((p) => {
          const line = lineData.find((d) => d.item === p.name)
          const isAbnormal = line?.flag === 'H' || line?.flag === 'L'
          return (
            <div key={p.name} className="flex items-center gap-1.5">
              <span className="w-2.5 h-0.5 rounded-full shrink-0" style={{ background: p.color }} />
              <span className="text-[var(--c-text)]">{p.name}</span>
              <span className={isAbnormal
                ? (line?.flag === 'H' ? 'text-[var(--c-danger)]' : 'text-[var(--c-warning)]') + ' font-semibold'
                : 'text-[var(--c-text)] font-semibold'}
              >
                {p.value}{line?.unit ? ` ${line.unit}` : ''}
              </span>
            </div>
          )
        })}
      </div>
    )
  }

  // 自定义 Legend：显示指标名 + 当前值
  const CustomLegend = ({ payload }: { payload?: Array<{ value: string; color: string }> }) => {
    if (!payload?.length) return null
    return (
      <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2">
        {payload.map((entry, i) => {
          const line = lineData[i]
          if (!line) return null
          const isAbnormal = line.flag === 'H' || line.flag === 'L'
          const valColor = line.flag === 'H' ? 'text-[var(--c-danger)]' : line.flag === 'L' ? 'text-[var(--c-warning)]' : 'text-[var(--c-text)]'
          return (
            <div key={entry.value} className="flex items-center gap-1.5">
              <span className="w-3 h-0.5 rounded-full shrink-0" style={{ background: entry.color }} />
              <span className="text-[length:var(--fs-xs)] text-[var(--c-text)]">{line.item}</span>
              <span className={`text-[length:var(--fs-xs)] font-semibold ${isAbnormal ? valColor : 'text-[var(--c-text)]'}`}>
                {line.valueNum}{line.unit ? ` ${line.unit}` : ''}
              </span>
            </div>
          )
        })}
      </div>
    )
  }

  return (
    <div>
      <ResponsiveContainer width="100%" height={160}>
        <LineChart data={chartData} margin={{ top: 4, right: 12, left: -8, bottom: 0 }}>
          <CartesianGrid
            strokeDasharray="3 3"
            stroke="var(--c-border)"
            vertical={false}
          />
          <XAxis
            dataKey="name"
            tick={{ fontSize: 12, fill: 'var(--c-text-secondary)' }}
            axisLine={{ stroke: 'var(--c-border)' }}
            tickLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            tick={{ fontSize: 12, fill: 'var(--c-text-secondary)' }}
            axisLine={false}
            tickLine={false}
            width={36}
            domain={['auto', 'auto']}
          />
          <Tooltip content={<CustomTooltip />} />
          {lineData.map((d, i) => (
            <Line
              key={d.item}
              type="monotone"
              dataKey={d.item}
              stroke={lineColors[i % lineColors.length]}
              strokeWidth={1.5}
              dot={{ r: 2.5, fill: lineColors[i % lineColors.length], strokeWidth: 0 }}
              activeDot={{ r: 4, fill: lineColors[i % lineColors.length], strokeWidth: 0 }}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
      <CustomLegend payload={lineData.map((d, i) => ({
        value: d.item,
        color: lineColors[i % lineColors.length],
      }))} />
    </div>
  )
}

/** 主要病灶分布表格（卡片式行） */
function LesionTable({ patient }: { patient: PatientProfile }) {
  const imagingEvents = patient.timeline.filter(
    (t) => t.eventType === 'imaging' || t.event.toLowerCase().includes('ct') || t.event.toLowerCase().includes('pet')
  )

  if (imagingEvents.length === 0) {
    return <EmptyHint text="暂无病灶分布数据，影像报告解析后将自动生成" />
  }

  return (
    <div className="space-y-1.5">
      {imagingEvents.map((t, i) => {
        const detail = t.detail || ''
        const prMatch = detail.match(/(PR|SD|PD|CR)/i)
        const efficacy = prMatch ? prMatch[1].toUpperCase() : null
        const effColor = efficacy === 'CR' || efficacy === 'PR'
          ? 'text-[var(--c-success)] bg-[var(--c-success)]/8'
          : efficacy === 'PD'
            ? 'text-[var(--c-danger)] bg-[var(--c-danger)]/8'
            : 'text-[var(--c-text-secondary)] bg-[var(--c-surface-2)]'
        return (
          <div key={'l' + i + '-' + t.date}
            className="flex items-center gap-3 rounded-md bg-white border border-[var(--c-border)]/60 px-3 py-2"
          >
            <span className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)] shrink-0 tabular-nums w-[72px]">
              {t.date}
            </span>
            <span className="text-[length:var(--fs-sm)] text-[var(--c-text)] flex-1 min-w-0 truncate">
              {t.event}
            </span>
            {efficacy ? (
              <span className={`text-[length:var(--fs-xs)] font-semibold px-1.5 py-0.5 rounded ${effColor} shrink-0`}>
                {efficacy}
              </span>
            ) : (
              <span className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)] shrink-0">—</span>
            )}
          </div>
        )
      })}
    </div>
  )
}

/** 当前治疗路径 */
function TreatmentPath({ patient }: { patient: PatientProfile }) {
  const hasTreatment = patient.status || patient.cycle || patient.medications.length > 0

  if (!hasTreatment) {
    return <EmptyHint text="暂无当前治疗路径" />
  }

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-[var(--c-accent)]/20 bg-[var(--c-accent)]/5 px-3.5 py-2.5">
        <p className="text-[length:var(--fs-md)] font-medium text-[var(--c-text)]">
          {[patient.status, patient.cycle].filter(Boolean).join(' · ')}
        </p>
        <p className="mt-1 text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]">
          此处只呈现病历中的当前状态，不代表治疗建议
        </p>
      </div>

      {patient.medications.length > 0 && (
        <div>
          <p className="text-[length:var(--fs-sm)] font-medium text-[var(--c-accent)] mb-1.5">当前用药</p>
          <div className="flex flex-wrap gap-1.5">
            {patient.medications.map((m) => (
              <span
                key={m}
                className="inline-flex items-center px-2.5 py-1 rounded-full bg-[var(--c-accent)]/6 border border-[var(--c-accent)]/15 text-[length:var(--fs-sm)] text-[var(--c-accent)] font-medium"
              >
                {m}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

/** 治疗史（完整时间轴，含详情） */
function TreatmentTimeline({ patient }: { patient: PatientProfile }) {
  if (patient.timeline.length === 0) {
    return <EmptyHint text="暂无治疗史" />
  }

  const typeStyle = (et?: string): { dot: string; label: string } => {
    switch (et) {
      /* 红 = 关键事件（侵入/主动治疗） */
      case 'surgery':
        return { dot: 'bg-[var(--c-danger)] border-[var(--c-danger)]', label: '手术' }
      case 'treatment_record':
        return { dot: 'bg-[var(--c-danger)] border-[var(--c-danger)]', label: '治疗' }
      /* 蓝 = 诊断评估 */
      case 'imaging':
        return { dot: 'bg-[var(--c-accent)] border-[var(--c-accent)]', label: '影像' }
      case 'pathology':
      case 'pathology_supplement':
        return { dot: 'bg-[var(--c-accent)] border-[var(--c-accent)]', label: '病理' }
      case 'gene_test':
        return { dot: 'bg-[var(--c-accent)] border-[var(--c-accent)]', label: '基因' }
      case 'lab_report':
        return { dot: 'bg-[var(--c-accent)] border-[var(--c-accent)]', label: '检验' }
      case 'diagnosis_cert':
        return { dot: 'bg-[var(--c-accent)] border-[var(--c-accent)]', label: '诊断' }
      /* 灰 = 常规 */
      case 'discharge':
      case 'admission':
        return { dot: 'bg-[var(--c-text-tertiary)] border-[var(--c-text-tertiary)]', label: '住院' }
      case 'progress_note':
        return { dot: 'bg-[var(--c-text-tertiary)] border-[var(--c-text-tertiary)]', label: '病程' }
      default:
        return { dot: 'bg-[var(--c-surface-2)]', label: '' }
    }
  }

  return (
    <div className="space-y-0">
      {patient.timeline.map((t, i) => {
        const isCurrent = i === 0
        const isLast = i === patient.timeline.length - 1
        const style = typeStyle(t.eventType)
        const dotCls = isCurrent && !t.eventType
          ? 'bg-[var(--c-accent)] border-[var(--c-accent)]'
          : style.dot

        return (
          <div key={'t' + i + '-' + t.date} className="grid grid-cols-[14px_1fr] gap-x-2.5">
            {/* 左列：圆点 + 虚线段（不贯穿圆点） */}
            <div className="flex flex-col items-center">
              <span
                className={'h-2.5 w-2.5 rounded-full border-2 shrink-0 mt-1 ' + dotCls}
              />
              {!isLast && (
                <div className="w-px flex-1 border-l border-dashed border-[var(--c-border)]" />
              )}
            </div>
            {/* 右列：日期 + 标签 + 内容 */}
            <div className="pb-3">
              <div className="flex items-baseline gap-2">
                <span className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)] shrink-0 tabular-nums">
                  {t.date}
                </span>
                {style.label && (
                  <span className="text-[length:var(--fs-xs)] text-[var(--c-accent)] bg-[var(--c-accent)]/8 rounded px-1 py-0.5 shrink-0">
                    {style.label}
                  </span>
                )}
              </div>
              <p className={'mt-0.5 text-[length:var(--fs-md)] ' + (isCurrent ? 'text-[var(--c-text)] font-semibold' : 'text-[var(--c-text)]')}>
                {t.event}
              </p>
              {t.detail && t.detail !== t.event && (
                <p className="mt-0.5 text-[length:var(--fs-xs)] text-[var(--c-text-secondary)] leading-relaxed">
                  {t.detail}
                </p>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

/** 实验室指标详情行（卡片式 + 异常值语义色） */
function LabDetailRow({ lab }: { lab: { item: string; valueText: string; flag: string; refText: string } }) {
  const isAbnormal = lab.flag === 'H' || lab.flag === 'L'
  const valueColor = isAbnormal ? (lab.flag === 'H' ? 'text-[var(--c-danger)]' : 'text-[var(--c-warning)]') : 'text-[var(--c-text)]'
  const valueSize = isAbnormal ? 'text-[length:var(--fs-xl)]' : 'text-[length:var(--fs-md)]'
  const valueWeight = isAbnormal ? 'font-bold' : 'font-medium'
  const barColor = lab.flag === 'H' ? 'bg-[var(--c-danger)]' : lab.flag === 'L' ? 'bg-[var(--c-warning)]' : ''
  return (
    <div className={`flex items-baseline gap-3 py-2.5 px-3 -mx-3 rounded-md ${isAbnormal ? 'bg-[var(--c-danger)]/5' : ''}`}>
      {isAbnormal && (
        <div className={`w-[3px] shrink-0 self-stretch rounded-full ${barColor}`} />
      )}
      <span className="text-[length:var(--fs-sm)] text-[var(--c-accent)] font-medium w-[80px] min-w-0 shrink-0">{lab.item}</span>
      <span className={`${valueSize} ${valueWeight} ${valueColor}`}>
        {lab.valueText}
        {lab.flag === 'H' && <span className="ml-0.5 text-[var(--c-danger)]">↑</span>}
        {lab.flag === 'L' && <span className="ml-0.5 text-[var(--c-warning)]">↓</span>}
      </span>
      {lab.refText && (
        <span className="ml-auto text-[length:var(--fs-xs)] text-[var(--c-text-secondary)] shrink-0">{lab.refText}</span>
      )}
    </div>
  )
}
