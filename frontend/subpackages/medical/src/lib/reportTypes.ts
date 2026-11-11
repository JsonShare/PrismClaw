/**
 * 报告类型 →（医学用途大类, 中文显示名）
 *
 * 取值来源为两个已核实权威源的并集，不是推测：
 *   1) 后端契约枚举 ReportType（models/enums.py，17 个值）
 *   2) 数据库实际出现的取值（pc_medical_patient_reports 去重，12 个值）
 * 两者已分叉：库里有 lab_report / diagnosis_cert / progress_note /
 * pathology_supplement / treatment_record / gene_test / surgery 不在枚举内。
 *
 * 未命中 → 进「其他」并显示原始值，与 CancerDAO 原文 FAQ
 * 「为什么有些文件显示在其他中」一致，不强制归类、不伪造中文名。
 *
 * 结构映射，不做字符串模糊匹配：命中即归类。
 */

export interface ReportTypeMeta {
  /** 医学用途大类 key */
  cat: string
  /** 面向医生展示的中文名 */
  label: string
}

export const OTHER_CATEGORY = 'other'

export const REPORT_TYPE_MAP: Record<string, ReportTypeMeta> = {
  // ── 病理与叙事文书 ──
  pathology: { cat: 'narrative', label: '病理报告' },
  pathology_supplement: { cat: 'narrative', label: '病理补充报告' },
  outpatient: { cat: 'narrative', label: '门诊病历' },
  inpatient: { cat: 'narrative', label: '住院病历' },
  discharge: { cat: 'narrative', label: '出院记录' },
  admission: { cat: 'narrative', label: '入院记录' },
  transfer: { cat: 'narrative', label: '转院记录' },
  emergency: { cat: 'narrative', label: '急诊记录' },
  consultation: { cat: 'narrative', label: '会诊记录' },
  progress_note: { cat: 'narrative', label: '病程记录' },
  diagnosis_cert: { cat: 'narrative', label: '诊断证明' },

  // ── 影像 ──
  imaging: { cat: 'imaging', label: '影像报告' },
  imaging_report: { cat: 'imaging', label: '影像报告' },

  // ── 分子与组学 ──
  gene_test: { cat: 'genomic', label: '基因检测' },

  // ── 检验 ──
  lab: { cat: 'lab', label: '检验报告' },
  lab_report: { cat: 'lab', label: '检验报告' },

  // ── 治疗记录 ──
  surgical: { cat: 'treatment', label: '手术记录' },
  surgery: { cat: 'treatment', label: '手术记录' },
  treatment_record: { cat: 'treatment', label: '治疗记录' },
  prescription: { cat: 'treatment', label: '处方' },
  nursing: { cat: 'treatment', label: '护理记录' },
  anesthesia: { cat: 'treatment', label: '麻醉记录' },
  blood_transfusion: { cat: 'treatment', label: '输血记录' },

  // ── 其他 ──
  other: { cat: OTHER_CATEGORY, label: '其他' },
}

export function resolveReportType(reportType: string): ReportTypeMeta {
  return REPORT_TYPE_MAP[reportType] ?? { cat: OTHER_CATEGORY, label: reportType || '未分类' }
}
