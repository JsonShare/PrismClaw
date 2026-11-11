/**
 * 报告行状态标 —— 「这份报告能不能直接用」
 *
 * 医生在时间轴上扫一眼就要判断：哪些已就绪、哪些还在跑、哪些必须回头看原文。
 * 判定只用后端已给出的字段（status / matchReason / errorMessage），不新造置信度：
 * 语义色是「要人注意」，不是装饰，故同一行只显示优先级最高的一个标。
 *
 * 优先级：解析失败 > 归属需核验 > 解析中。
 * 注：bound_anonymous 是「页内上传 + 文内没识别到姓名」的正常结果（归属由医生选定），
 * 不是异常，故意不给标 —— 见 MATCH_REASON_TEXT。
 */

import type { PatientReport } from '../types/view'

export type FlagTone = 'danger' | 'warn' | 'info'

export interface ReportFlag {
  tone: FlagTone
  /** 行内短标 */
  text: string
  /** 悬停说明（title），讲清「为什么需要人看」 */
  hint: string
}

/** 归属判定原因 → 需要人工核验的说明（仅这些原因值得打断医生） */
const REVIEW_HINT: Record<string, string> = {
  ambiguous: '存在多名同名患者，归属待人工确认',
  no_match: '档案中没有同名患者，归属待人工确认',
  no_name: '未识别到患者姓名，归属待人工确认',
  desensitized: '姓名已脱敏（先生/女士），无法自动核对',
  bound_conflict: '报告内姓名与所选患者不一致，建议核对原件',
}

export function reportFlag(r: PatientReport): ReportFlag | null {
  if (r.status === '失败') {
    return {
      tone: 'danger',
      text: '解析失败',
      hint: r.errorMessage || '解析失败，可在详情里重新解析',
    }
  }
  const hint = REVIEW_HINT[r.matchReason ?? '']
  if (hint) return { tone: 'warn', text: '需核验', hint }
  // 用 state 而不是 status 判「是否解析完」：state 由后端归一化过
  // （status === '已解析' ? '已解析' : '解析中'），缺 status 的老数据也能落到解析中。
  if (r.state !== '已解析') {
    return {
      tone: 'info',
      text: r.status === '排队中' ? '排队中' : '解析中',
      hint: r.status && r.status !== '排队中' ? `正在${r.status}` : '已进入解析队列',
    }
  }
  return null
}
