import { http } from './request'

/** 专家目录条目（`GET /api/experts` 的轻量子集） */
export interface ExpertItem {
  id: string
  kind: 'expert' | 'team'
  /** 展示名：优先中文，缺省回落 id */
  label: string
  profession: string
  description: string
}

/** 会话级专家状态（`/api/sessions/{id}/expert` 的响应） */
export interface ExpertState {
  expert_id: string | null
  source: string | null
  suppressed: boolean
  selected_skill_ids: string[]
}

interface RawLocalizedText {
  zh?: unknown
  en?: unknown
}

interface RawExpert {
  id?: unknown
  expertType?: unknown
  displayName?: unknown
  profession?: unknown
  description?: unknown
}

function localized(value: unknown): string {
  if (typeof value === 'string') return value
  if (!value || typeof value !== 'object') return ''
  const text = value as RawLocalizedText
  if (typeof text.zh === 'string' && text.zh) return text.zh
  if (typeof text.en === 'string' && text.en) return text.en
  return ''
}

function normalize(raw: RawExpert): ExpertItem | null {
  if (typeof raw.id !== 'string' || !raw.id) return null
  return {
    id: raw.id,
    kind: raw.expertType === 'team' ? 'team' : 'expert',
    label: localized(raw.displayName) || raw.id,
    profession: localized(raw.profession),
    description: localized(raw.description),
  }
}

export async function listExperts(): Promise<ExpertItem[]> {
  const data = await http.get<RawExpert[]>('/api/experts')
  if (!Array.isArray(data)) return []
  return data
    .map(normalize)
    .filter((item): item is ExpertItem => item !== null)
}

export function selectExpert(sessionId: string, expertId: string): Promise<ExpertState> {
  return http.post<ExpertState>(
    `/api/sessions/${encodeURIComponent(sessionId)}/expert`,
    { expert_id: expertId },
  )
}

export function cancelExpert(sessionId: string): Promise<ExpertState> {
  return http.delete<ExpertState>(`/api/sessions/${encodeURIComponent(sessionId)}/expert`)
}
