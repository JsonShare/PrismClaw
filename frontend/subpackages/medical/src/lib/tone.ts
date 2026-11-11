/**
 * 医疗域 · 语义色 tone
 *
 * Tone 是「语义角色」而非具体颜色，渲染层通过 .mw-tone-* / .mw-pill-* 落到主题 token，
 * 因此全部颜色随 data-theme 明暗翻转。
 *
 * 约定：数据层（data/）只声明 tone 名，不持有 Tailwind 类或十六进制值。
 *
 * 使用约束：tone 必须通过 Pill 的 tone 属性传入。切勿往 className 追加 `mw-pill-*` ——
 * Pill 默认带 `mw-tone-neutral`，而该规则在 medical.css 中声明更靠后，会覆盖后追加的 pill 类。
 */

import type {
  RiskLevel,
} from '../types/view'

/** 语义色角色 */
export type Tone = 'accent' | 'success' | 'warning' | 'danger' | 'purple' | 'neutral'

/** 风险分层 → tone */
export const RISK_TONE: Record<RiskLevel, Tone> = {
  高危: 'danger',
  中危: 'warning',
  低危: 'success',
}

/** 报告/病历解析状态 → tone */
export const REPORT_STATE_TONE: Record<string, Tone> = {
  已解析: 'success',
  解析中: 'accent',
}

