/**
 * 医疗域 · 纯函数工具层
 *
 * 只放与渲染无关的计算：数值解析、趋势构造、派生文案、分页切片、页码序列。
 * 页面与 feature 组件一律从这里取，避免同一份派生逻辑散落多处。
 */

import type { DrugCategory, InteractionResult, PatientProfile, PatientReport, NarrativeSection } from '../types/view'

/* ════════════════════════ 数值与趋势 ════════════════════════ */

/** 从结果串中抽取首个数值（"45.2 ng/mL" → 45.2），无数值返回 null */
export function parseVal(raw: string): number | null {
  const m = String(raw).match(/([\d.]+)/)
  return m ? Number.parseFloat(m[1]) : null
}

/** 剥掉数值前缀，只留单位（"45.2 ng/mL" → "ng/mL"） */
export function unitOf(raw: string): string {
  return String(raw).replace(/^[\d.]+\s*/, '')
}

/* ════════════════════════ 派生文案 ════════════════════════ */

/** 风险预警条目（AI 识别 · 需医生确认处置） */
export function buildWarnings(p: PatientProfile): string[] {
  const out: string[] = []
  if (p.age >= 65) out.push(`${p.age} 岁高龄，关注治疗耐受性与基础病管理`)
  p.labs
    .filter((l) => l.flag === 'H' || l.flag === 'L')
    .slice(0, 2)
    .forEach((l) => out.push(`${l.item} 异常（${l.valueText}），建议复查监测`))
  if (p.allergies.length) out.push(`过敏史：${p.allergies.join('、')}，医嘱开立需核对`)
  if (p.history.some((h) => h.includes('高血压'))) out.push('合并高血压，注意方案相关心血管毒性')
  return out
}

/** 展开 narrative（兼容数组 / { sections } / 空值三种形态，绝不返回 undefined） */
export function flattenNarrative(narrative: PatientReport['narrative']): NarrativeSection[] {
  if (Array.isArray(narrative)) return narrative
  return narrative?.sections ?? []
}

/* ════════════════════════ 时间轴分组 ════════════════════════ */

/**
 * 就诊日期拆成「年 / 月日」：时间轴按年分段后，行内只留 06-18，视觉噪音减半。
 * 解析不出（空值、自由文本）时 year 为空、md 原样返回，调用方退化成单行日期。
 */
export function splitVisitDate(raw: string): { year: string; md: string } {
  const text = String(raw ?? '').trim()
  const m = text.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})/)
  if (!m) return { year: '', md: text || '—' }
  return { year: m[1], md: `${m[2].padStart(2, '0')}-${m[3].padStart(2, '0')}` }
}

/**
 * 按年份分组（年份倒序，无日期的垫底）。组内保持接口给出的顺序 ——
 * 后端已按就诊日期倒序，前端不再二次排序，避免与分页边界打架。
 */
export function groupReportsByYear<T extends { visitDate: string; date: string }>(
  items: T[],
): Array<{ year: string; items: T[] }> {
  const buckets = new Map<string, T[]>()
  for (const it of items) {
    const { year } = splitVisitDate(it.visitDate || it.date)
    const key = year || '未知'
    const arr = buckets.get(key)
    if (arr) arr.push(it)
    else buckets.set(key, [it])
  }
  return [...buckets]
    .map(([year, list]) => ({ year, items: list }))
    .sort((a, b) => (a.year === '未知' ? 1 : b.year === '未知' ? -1 : b.year.localeCompare(a.year)))
}

/* ════════════════════════ 药品相互作用核查 ════════════════════════ */

/** 按两种药品的分类组合给出核查提示（窄切口工具，输出行动建议而非结论） */
export function checkInteraction(
  aName: string,
  bName: string,
  drugs: Array<{ name: string; cat: DrugCategory }>,
): InteractionResult | null {
  if (!aName || !bName) return null
  if (aName === bName) return { level: 'warn', text: '为同一种药物，请选择两种不同药物' }

  const a = drugs.find((d) => d.name === aName)
  const b = drugs.find((d) => d.name === bName)
  if (!a || !b) return null

  const pair = [a.cat, b.cat]
  const nChemo = pair.filter((c) => c === '化疗药物').length
  const nImmuno = pair.filter((c) => c === '免疫治疗').length

  if (nChemo === 2) return { level: 'high', text: '化疗×化疗：骨髓抑制叠加，需监测血象并考虑减量' }
  if (nImmuno === 1 && nChemo === 1) return { level: 'warn', text: '免疫×化疗：注意免疫相关不良反应与化疗毒性的鉴别' }
  if (nImmuno === 2) return { level: 'warn', text: '免疫×免疫：双免方案需重点关注免疫相关不良反应' }
  return { level: 'ok', text: '未检索到明确相互作用，仍建议临床药师复核' }
}

/* ════════════════════════ 分页 ════════════════════════ */

/**
 * 页码序列：最多 7 个元素，超出用 '…' 压缩（1 … 4 5 6 … 1000）
 */
export function buildPageNumbers(cur: number, totalPages: number): Array<number | '…'> {
  if (totalPages <= 7) return Array.from({ length: totalPages }, (_, i) => i + 1)
  const out: Array<number | '…'> = [1]
  const start = Math.max(2, cur - 1)
  const end = Math.min(totalPages - 1, cur + 1)
  if (start > 2) out.push('…')
  for (let i = start; i <= end; i += 1) out.push(i)
  if (end < totalPages - 1) out.push('…')
  out.push(totalPages)
  return out
}
