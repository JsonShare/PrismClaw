/**
 * 医疗域 · UI 视图模型（camelCase，后端直出）
 *
 * 与后端 routes/* 的 _patient_to_vm / _report_to_vm 输出完全对齐。
 * 后端直出 camelCase 视图模型（D6），前端无需 snake_case↔camelCase 映射层。
 */

// ── 通用 ─────────────────────────────────────────────────────

/** 分页包装（合约 §3.1） */
export interface Paged<T> {
  items: T[]
  total: number
  page: number
  pageSize: number
}

// ── 枚举/值对象 ─────────────────────────────────────────────

/** 风险分层 */
export type RiskLevel = '高危' | '中危' | '低危'

/** 生命体征 */
export interface VitalSigns {
  hr: number
  bp: string
  temp: number
  spo2: number
}

/** 检验项（六键，合约 §2.2.1） */
export interface LabItem {
  item: string
  valueText: string
  valueNum: number | null
  unit: string
  refText: string
  flag: string
}

/** 诊疗时间线节点（患者档案） */
export interface PatientTimelineNode {
  /** YYYY-MM-DD 日期 */
  date: string
  event: string
  /** 详细描述（如摘要、疗效评价等） */
  detail?: string
  /** 事件类型：imaging / pathology / discharge / gene_test / lab_report / treatment_record / … */
  eventType?: string
}

/** 叙事段（合约 §2.2.2） */
export interface NarrativeSection {
  heading: string
  body: string
}

// ── 患者 ─────────────────────────────────────────────────────

/** 患者档案 */
export interface PatientProfile {
  id: string
  name: string
  /** 该患者名下的临床文档份数（列表/详情均由后端统计） */
  reportCount: number
  age: number
  gender: '男' | '女'
  idType: string
  idNumber: string
  phone: string
  department: string
  diagnosis: string
  stage: string
  status: string
  cycle: string
  risk: RiskLevel
  vitals: VitalSigns
  /** 标志物名 → 结果串 */
  biomarkers: Record<string, string>
  allergies: string[]
  history: string[]
  medications: string[]
  labs: LabItem[]
  timeline: PatientTimelineNode[]
}

/** 统计条指标（合约 §3.2-A /stats） */
export interface PatientStats {
  archivedPatients: number
  archivedMonthlyDelta: number
  highRisk: number
  reportsToday: number
  reportsParsed: number
  reportsParsing: number
}

/** 新建患者载荷（POST /api/medical/patients/） */
export interface CreatePatientPayload {
  /** 姓名（必填） */
  name: string
  /** 证件类型：身份证 / 护照 / 港澳通行证 / 台湾通行证 / 军官证 / 其他 */
  idType?: string
  /** 证件信息（原值，后端脱敏后入库） */
  idNumber?: string
}

// ── 报告/病历 ───────────────────────────────────────────────

  /** 报告解析状态 */
  export type ReportParseState = '已解析' | '解析中'

  /** 数据来源：HIS 系统同步（结构化）或人工上传（文件/图片）。未接入 HIS 时恒缺省 → 视为上传。 */
  export type ReportSource = 'his' | 'upload'

/** 患者详情页报告项（统一临床文档表，合约 §3.2-A /{patientId}/reports） */
export interface PatientReport {
  id: string
  /** 所属患者 ID（后端 join 后直出） */
  patientId: string
  /** 所属患者姓名（后端 join 后直出，供列表显示） */
  patientName: string
  reportType: string
  name: string
  date: string
  state: ReportParseState
  /** 图标键：image/text/audio */
  icon: 'image' | 'text' | 'audio'
  fileName: string
  filePath: string
  mimeType: string
  attachments: Array<{ path: string; mime: string; page_no: number }>
  hospitalName: string
  doctorName: string
  status: string
  progress: number
  /** 解析失败原因；「明确失败」要让用户看得见，否则只能干瞪眼 */
  errorMessage: string
  /** 归属判定结果：exact / ambiguous / no_match / no_name ... */
  matchReason: string
  /** 数据来源。缺省表示人工上传；HIS 接入后由后端标注 'his'。 */
  source?: ReportSource
  /** HIS 最近同步时间（ISO 字符串），仅 source==='his' 时有值。 */
  syncedAt?: string
  /**
   * 歧义归属（同名多人）时后端实时算出的同名候选患者，供指派抽屉点选。
   * 仅 list_inbox 在 matchReason == 'ambiguous' 时填充；其余场景为空数组。
   */
  candidates?: Array<{ id: string; name: string; gender?: string; age?: number }>
  rawText: string
  visitDate: string
  department: string
  diagnosis: string
  narrative: NarrativeSection[] | { sections: NarrativeSection[] }
  labs: LabItem[]
  medications: string[]
  tags: string[]
  summary: string
}

/** 整理队列任务（合约 §3.2-B /queue） */
export interface QueueTask {
  id: string
  file: string
  type: string
  stage: string
  progress: number
  elapsed: string
}

/** 队列汇总 */
export interface QueueSummary {
  doneToday: number
  failed: number
  retried: number
  /** 已解析但未归属到任何患者的文档数 */
  unassigned: number
}

/** 队列响应 */
export interface RecordsQueueResponse {
  tasks: QueueTask[]
  summary: QueueSummary
}

/** 上传响应（合约 §3.2-B /upload） */
export interface UploadResponse {
  reportIds: string[]
}

// ── 知识库 ───────────────────────────────────────────────────

/** facet 计数 */
export interface FacetCount {
  name: string
  count: number
}

/** 文献 facets */
export interface LiteratureFacets {
  categories: FacetCount[]
  years: FacetCount[]
}

/** 指南 facets */
export interface GuidelineFacets {
  categories: FacetCount[]
}

/** 药品 facets */
export interface DrugFacets {
  categories: FacetCount[]
  essentialCount: number
}

/** 文献条目 */
export interface LiteratureEntry {
  id: string
  title: string
  authors: string
  journal: string
  year: number
  category: string
  abstract: string
  keywords: string[]
  citations: number
  doi: string
}

/** 指南发布机构分类 */
export type GuidelineCategory = 'NCCN指南' | 'CSCO指南' | 'ESMO指南'

/** 指南条目 */
export interface GuidelineEntry {
  id: string
  title: string
  org: string
  year: number
  cat: GuidelineCategory
  ver: string
  abstract: string
  /** 核心推荐条目 */
  points: string[]
}

/** 药品分类 */
export type DrugCategory = '化疗药物' | '靶向药物' | '免疫治疗' | '内分泌治疗' | '骨改良药物'

/** 药品条目 */
export interface DrugEntry {
  id: string
  name: string
  generic: string
  cat: DrugCategory
  dosage: string
  indications: string[]
  mechanism: string
  sideEffects: string[]
  contraindications: string[]
  interactions: string[]
  /** 妊娠分级 */
  pregnancy: string
  /** 是否国家基药 */
  essential: boolean
}

/** 药品轻量选项（相互作用核查下拉用） */
export interface DrugOption {
  id: string
  name: string
  cat: DrugCategory
}

// ── 相互作用核查 ────────────────────────────────────────────

/** 相互作用核查等级 */
export type InteractionLevel = 'high' | 'warn' | 'ok'

/** 相互作用核查结果 */
export interface InteractionResult {
  level: InteractionLevel
  text: string
}

// ── 咨询会话 ────────────────────────────────────────────────

/** 咨询会话登记行（后端直出 camelCase） */
export interface ConsultationEntry {
  id: string
  sessionId: string
  patientId: string
  doctorId: string
  title: string
  createdAt: string
}

// ── 研究简报 ─────────────────────────────────────────────────

/** 简报状态 */
export type BriefStatus = '分析中' | '已通过' | '待修正' | '已废弃'

/** 引文条目 */
export interface CitationItem {
  citationId: string
  sourceType: 'report_section' | 'kb_literature' | 'kb_guideline' | 'kb_drug' | 'speculative'
  sourceId: string
  quotedText: string
  confidence: 'high' | 'medium' | 'low'
}

/** 简报段落 */
export interface BriefSection {
  id: string
  title: string
  content: string
  isSpeculative: boolean
  citations: string[]  // citation_id[]
}

/** 审计违规 */
export interface AuditViolation {
  rule: string
  citationId: string
  detail: string
}

/** 简报列表项（GET /api/medical/briefs/） */
export interface BriefListItem {
  id: string
  patientId: string
  reportIds: string[]
  status: BriefStatus
  version: number
  startedAt: string
  finishedAt: string
  createAt: string
}

/** 简报详情（GET /api/medical/briefs/{id}） */
export interface BriefDetail extends BriefListItem {
  directorPlan: Record<string, unknown>
  sections: BriefSection[]
  citations: CitationItem[]
  auditResult: {
    passed: boolean
    violations?: AuditViolation[]
  }
  errorMessage: string
}
