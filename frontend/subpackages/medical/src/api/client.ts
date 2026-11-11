/**
 * 医疗域 API 调用封装（合约 §3.2 全 12 端点）
 *
 * 后端直出 camelCase 视图模型（D6），前端无需 snake_case↔camelCase 映射层。
 * 保留超时 / AbortController / 请求去重机制。
 */

import type {
  Paged,
  PatientProfile,
  PatientStats,
  PatientReport,
  RecordsQueueResponse,
  UploadResponse,
  LiteratureEntry,
  LiteratureFacets,
  GuidelineEntry,
  GuidelineFacets,
  DrugEntry,
  DrugFacets,
  DrugOption,
  ConsultationEntry,
  BriefListItem,
  BriefDetail,
  CreatePatientPayload,
} from '../types/view'

// ── 通用请求封装 ─────────────────────────────────────────────

/** 请求超时时间 (ms) */
const REQUEST_TIMEOUT = 15_000

/** 从 localStorage 读取核心 JWT token（与 AuthContext 共享同一存储键） */
function getAuthToken(): string | null {
  try {
    const raw = localStorage.getItem('prismclaw.auth.v1')
    if (!raw) return null
    const parsed = JSON.parse(raw) as { token?: unknown }
    return typeof parsed.token === 'string' ? parsed.token : null
  } catch {
    return null
  }
}

/** 进行中的请求去重映射 (key = method+url+body, value = promise) */
const inflightMap = new Map<string, Promise<unknown>>()

/** 生成请求去重 key */
function inflightKey(method: string, url: string, body?: string): string {
  return `${method}:${url}:${body ?? ''}`
}

/** 通用 JSON 请求（带超时、AbortController、去重） */
async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const method = options?.method ?? 'GET'
  const body = typeof options?.body === 'string' ? options.body : undefined
  const key = inflightKey(method, url, body)

  const existing = inflightMap.get(key)
  if (existing) return existing as Promise<T>

  const promise = _doRequest<T>(url, options)
  inflightMap.set(key, promise)

  try {
    return await promise
  } finally {
    inflightMap.delete(key)
  }
}

async function _doRequest<T>(url: string, options?: RequestInit): Promise<T> {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT)

  try {
    const isMultipart = options?.body instanceof FormData
    const headers: Record<string, string> = isMultipart
      ? {}
      : { 'Content-Type': 'application/json' }

    // 注入 Authorization 头（后端已全量要求 Depends(get_current_user)）
    const token = getAuthToken()
    if (token) headers['Authorization'] = `Bearer ${token}`

    const res = await fetch(url, {
      headers,
      ...options,
      signal: controller.signal,
    })
    if (!res.ok) {
      throw new Error(`请求失败: ${res.status} ${res.statusText}`)
    }
    return (await res.json()) as T
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new Error('请求超时，请稍后重试')
    }
    throw err
  } finally {
    clearTimeout(timeoutId)
  }
}

// ── A. 患者总览页（4 端点）────────────────────────────────────

/** 患者分页列表 */
export async function listPatients(params?: {
  keyword?: string
  gender?: string
  risk?: string
  page?: number
  pageSize?: number
}): Promise<Paged<PatientProfile>> {
  const qs = new URLSearchParams()
  if (params?.keyword) qs.set('keyword', params.keyword)
  if (params?.gender) qs.set('gender', params.gender)
  if (params?.risk) qs.set('risk', params.risk)
  if (params?.page) qs.set('page', String(params.page))
  if (params?.pageSize) qs.set('pageSize', String(params.pageSize))
  return request(`/api/medical/patients/?${qs}`)
}

/** 统计条四项指标 */
export async function getPatientStats(): Promise<PatientStats> {
  return request('/api/medical/patients/stats')
}

/** 患者详情（含 timeline） */
export async function getPatient(patientId: string): Promise<PatientProfile> {
  return request(`/api/medical/patients/${patientId}`)
}

/** 患者报告解析卡列表（分页） */
export async function listPatientReports(patientId: string, params?: {
  page?: number
  pageSize?: number
}): Promise<{ items: PatientReport[]; total: number }> {
  const qs = new URLSearchParams()
  if (params?.page) qs.set('page', String(params.page))
  if (params?.pageSize) qs.set('pageSize', String(params.pageSize))
  const query = qs.toString()
  return request(`/api/medical/patients/${patientId}/reports${query ? `?${query}` : ''}`)
}

/** 新建患者（姓名必填；证件类型 / 证件信息可选，后端脱敏入库） */
export async function createPatient(payload: CreatePatientPayload): Promise<PatientProfile> {
  return request('/api/medical/patients/', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

/**
 * 医生修正报告内容（AI 摘要 / AI 建议 / 报告原文）。
 * 仅传要改的字段，后端按白名单落库，返回更新后的完整视图模型。
 */
export async function correctReport(patientId: string, reportId: string, patch: {
  narrative?: PatientReport['narrative']
  summary?: string
  rawText?: string
  hospitalName?: string
  doctorName?: string
  department?: string
  /** YYYY-MM-DD；非法日期后端直接丢弃该字段 */
  visitDate?: string
}): Promise<PatientReport> {
  return request(`/api/medical/patients/${patientId}/reports/${reportId}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
}

/**
 * 取报告附件的原始文件（PDF / 图片），返回 object URL 供预览。
 * 走 fetch + Bearer 是因为 <img>/<iframe> 无法自定义请求头；
 * 调用方负责卸载时 URL.revokeObjectURL。
 */
export async function fetchReportAttachmentUrl(reportId: string, index: number): Promise<string> {
  const token = getAuthToken()
  const res = await fetch(`/api/medical/records/${reportId}/attachments/${index}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!res.ok) throw new Error(`附件加载失败: ${res.status}`)
  return URL.createObjectURL(await res.blob())
}

// ── B. 智能病历页（3 端点）────────────────────────────────────

/** 病历分页列表 / 报告检索（关键词 + 类型 + 状态过滤） */
export async function listRecords(params?: {
  keyword?: string
  reportType?: string
  status?: string
  page?: number
  pageSize?: number
}): Promise<Paged<PatientReport>> {
  const qs = new URLSearchParams()
  if (params?.keyword) qs.set('keyword', params.keyword)
  if (params?.reportType) qs.set('reportType', params.reportType)
  if (params?.status) qs.set('status', params.status)
  if (params?.page) qs.set('page', String(params.page))
  if (params?.pageSize) qs.set('pageSize', String(params.pageSize))
  return request(`/api/medical/records/?${qs}`)
}

/** 整理队列 + 页脚汇总 */
export async function getRecordsQueue(): Promise<RecordsQueueResponse> {
  return request('/api/medical/records/queue')
}

/** 「待整理」队列：尚未归属到任何患者的文档 */
export async function listInbox(params?: {
  page?: number
  pageSize?: number
}): Promise<Paged<PatientReport>> {
  const qs = new URLSearchParams()
  if (params?.page) qs.set('page', String(params.page))
  if (params?.pageSize) qs.set('pageSize', String(params.pageSize))
  return request(`/api/medical/records/inbox?${qs}`)
}

/** 把文档指派给患者（AI 识别不出归属时的人工兜底） */
export async function assignReport(reportId: string, patientId: string): Promise<PatientReport> {
  return request(`/api/medical/records/${reportId}/assign`, {
    method: 'POST',
    body: JSON.stringify({ patientId }),
  })
}

/** 上传文件
 *
 * patientId 给定 = 页内上传：归属由此确定，后端不再用解析结果改写；
 * 报告内容里没识别出姓名时也直接归到这个患者名下。
 */
export async function uploadRecords(files: File[], patientId?: string): Promise<UploadResponse> {
  const formData = new FormData()
  if (patientId) formData.append('patientId', patientId)
  files.forEach((f) => formData.append('files', f))
  return request('/api/medical/records/upload', {
    method: 'POST',
    body: formData,
  })
}

// ── C. 知识库三页（4 端点）────────────────────────────────────

/** 文献列表 + facets */
export async function listLiterature(params?: {
  keyword?: string
  category?: string
  year?: string
  page?: number
  pageSize?: number
}): Promise<Paged<LiteratureEntry> & { facets: LiteratureFacets }> {
  const qs = new URLSearchParams()
  if (params?.keyword) qs.set('keyword', params.keyword)
  if (params?.category) qs.set('category', params.category)
  if (params?.year) qs.set('year', params.year)
  if (params?.page) qs.set('page', String(params.page))
  if (params?.pageSize) qs.set('pageSize', String(params.pageSize))
  return request(`/api/medical/kb/literature?${qs}`)
}

/** 指南列表 + facets */
export async function listGuidelines(params?: {
  keyword?: string
  category?: string
  sort?: string
  page?: number
  pageSize?: number
}): Promise<Paged<GuidelineEntry> & { facets: GuidelineFacets }> {
  const qs = new URLSearchParams()
  if (params?.keyword) qs.set('keyword', params.keyword)
  if (params?.category) qs.set('category', params.category)
  if (params?.sort) qs.set('sort', params.sort)
  if (params?.page) qs.set('page', String(params.page))
  if (params?.pageSize) qs.set('pageSize', String(params.pageSize))
  return request(`/api/medical/kb/guidelines?${qs}`)
}

/** 药品列表 + facets */
export async function listDrugs(params?: {
  keyword?: string
  category?: string
  essentialOnly?: boolean
  page?: number
  pageSize?: number
}): Promise<Paged<DrugEntry> & { facets: DrugFacets }> {
  const qs = new URLSearchParams()
  if (params?.keyword) qs.set('keyword', params.keyword)
  if (params?.category) qs.set('category', params.category)
  if (params?.essentialOnly) qs.set('essentialOnly', 'true')
  if (params?.page) qs.set('page', String(params.page))
  if (params?.pageSize) qs.set('pageSize', String(params.pageSize))
  return request(`/api/medical/kb/drugs?${qs}`)
}

/** 药品轻量选项（相互作用核查下拉用） */
export async function listDrugOptions(): Promise<DrugOption[]> {
  return request('/api/medical/kb/drugs/options')
}

// ── D. 咨询会话（3 端点）────────────────────────────────────

/** 创建咨询会话（校验档案归属，幂等） */
export async function createConsultation(params: {
  sessionId: string
  patientId: string
  title?: string
}): Promise<ConsultationEntry> {
  return request('/api/medical/consultations/', {
    method: 'POST',
    body: JSON.stringify(params),
  })
}

/** 查询咨询列表（按医生 ID，可选患者过滤） */
export async function listConsultations(params?: {
  patientId?: string
}): Promise<ConsultationEntry[]> {
  const qs = new URLSearchParams()
  if (params?.patientId) qs.set('patientId', params.patientId)
  return request(`/api/medical/consultations/?${qs}`)
}

/** 按 session_id 查询咨询 */
export async function getConsultation(sessionId: string): Promise<ConsultationEntry> {
  return request(`/api/medical/consultations/${encodeURIComponent(sessionId)}`)
}

// ── E. 研究简报（5 端点）────────────────────────────────────

/** 简报分页列表 */
export async function listBriefs(params?: {
  patientId?: string
  status?: string
  page?: number
  pageSize?: number
}): Promise<Paged<BriefListItem>> {
  const qs = new URLSearchParams()
  if (params?.patientId) qs.set('patientId', params.patientId)
  if (params?.status) qs.set('status', params.status)
  if (params?.page) qs.set('page', String(params.page))
  if (params?.pageSize) qs.set('pageSize', String(params.pageSize))
  const query = qs.toString()
  return request(`/api/medical/briefs/${query ? `?${query}` : ''}`)
}

/** 简报详情 */
export async function getBrief(briefId: string): Promise<BriefDetail> {
  return request(`/api/medical/briefs/${briefId}`)
}

/** 创建简报（触发后台分析） */
export async function createBrief(params: {
  patientId: string
  reportIds?: string[]
}): Promise<BriefListItem> {
  return request('/api/medical/briefs/', {
    method: 'POST',
    body: JSON.stringify(params),
  })
}

/** 重新分析（版本递增） */
export async function reanalyzeBrief(briefId: string): Promise<BriefListItem> {
  return request(`/api/medical/briefs/${briefId}/reanalyze`, {
    method: 'POST',
  })
}

/** 删除简报（逻辑删除） */
export async function deleteBrief(briefId: string): Promise<void> {
  await request(`/api/medical/briefs/${briefId}`, { method: 'DELETE' })
}
