/**
 * 报告数据来源判定
 *
 * HIS 接入前，所有报告均为「上传」来源；接入后，由后端在视图模型上标注
 * `source: 'his' | 'upload'`。本模块只做归一化，不假定任何来源一定存在，
 * 故未接入 HIS 时筛选/标识全部退化为「上传」，无需任何桩数据。
 */

import type { PatientReport } from '../types/view'

export type ReportSource = 'his' | 'upload'

/** 归一化来源：缺省 / 非法值一律按「上传」处理（手工上传是兜底来源） */
export function reportSource(r: PatientReport): ReportSource {
  return r.source === 'his' ? 'his' : 'upload'
}

export const SOURCE_META: Record<ReportSource, { label: string; cls: string }> = {
  his: { label: 'HIS', cls: 'src-his' },
  upload: { label: '上传', cls: 'src-up' },
}
