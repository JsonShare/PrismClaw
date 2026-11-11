/**
 * 患者详情页
 *
 * 由路由传入 patientId，异步加载患者数据，未匹配时给出明确的空态。
 */

import { useEffect, useState } from 'react'
import { FileText, Loader2 } from 'lucide-react'
import { Btn, EmptyState } from '../components'
import { getPatient } from '../api/client'
import { PatientDetailView } from '../features/patients/PatientDetailView'
import type { PatientProfile } from '../types/view'

interface PatientDetailPageProps {
  patientId: string
  onBack: () => void
}

export function PatientDetailPage({ patientId, onBack }: PatientDetailPageProps) {
  const [patient, setPatient] = useState<PatientProfile | null>(null)
  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setNotFound(false)
    getPatient(patientId)
      .then((p) => { if (!cancelled) setPatient(p) })
      .catch(() => { if (!cancelled) setNotFound(true) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [patientId])

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <Loader2 className="h-5 w-5 animate-spin text-[var(--c-accent)]" />
      </div>
    )
  }

  if (notFound || !patient) {
    return (
      <EmptyState
        icon={FileText}
        title="未找到该患者"
        hint="可能已被删除，或链接中的患者编号无效"
        className="min-h-[400px]"
        action={
          <Btn variant="dark" onClick={onBack}>
            返回患者管理
          </Btn>
        }
      />
    )
  }

  return <PatientDetailView patient={patient} onBack={onBack} />
}
