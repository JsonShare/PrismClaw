/**
 * 患者管理页 · 统计条 + 花名册
 *
 * 点击整行进入患者详情（独立视图，非弹层），路由由外层 onOpenPatient 驱动。
 * 数据来自 listPatients + getPatientStats 两个异步接口。
 */

import { useCallback, useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { PagePager, Panel } from '../components'
import { listPatients, getPatientStats } from '../api/client'
import { PatientFilterBar } from '../features/patients/PatientFilterBar'
import { PatientStatsRow } from '../features/patients/PatientStatsRow'
import { PatientTable } from '../features/patients/PatientTable'
import type { PatientProfile, PatientStats } from '../types/view'

const PAGE_SIZE = 8

const DEFAULT_STATS: PatientStats = {
  archivedPatients: 0,
  archivedMonthlyDelta: 0,
  highRisk: 0,
  reportsToday: 0,
  reportsParsed: 0,
  reportsParsing: 0,
}

export function PatientsPage({ onOpenPatient }: { onOpenPatient: (patientId: string) => void }) {
  const [keyword, setKeyword] = useState('')
  const [gender, setGender] = useState('全部')
  const [risk, setRisk] = useState('全部')
  const [page, setPage] = useState(1)

  const [rows, setRows] = useState<PatientProfile[]>([])
  const [total, setTotal] = useState(0)
  const [stats, setStats] = useState<PatientStats>(DEFAULT_STATS)
  const [loading, setLoading] = useState(true)

  const fetchPatients = useCallback(async () => {
    setLoading(true)
    try {
      const genderParam = gender === '全部' ? undefined : gender
      const riskParam = risk === '全部' ? undefined : risk
      const [paged, s] = await Promise.all([
        listPatients({ keyword: keyword.trim() || undefined, gender: genderParam, risk: riskParam, page, pageSize: PAGE_SIZE }),
        getPatientStats(),
      ])
      setRows(paged.items)
      setTotal(paged.total)
      setStats(s)
    } finally {
      setLoading(false)
    }
  }, [keyword, gender, risk, page])

  useEffect(() => { void fetchPatients() }, [fetchPatients])

  const dirty = keyword !== '' || gender !== '全部' || risk !== '全部'

  const reset = () => {
    setKeyword('')
    setGender('全部')
    setRisk('全部')
    setPage(1)
  }

  return (
    <div>
      <PatientStatsRow stats={stats} />

      <Panel className="p-4 mt-4">
        <div className="flex items-center justify-between gap-3 mb-3 flex-wrap">
          <h2 className="text-[length:var(--fs-lg)] font-bold mw-strong">患者列表</h2>
          <span className="text-[length:var(--fs-sm)] mw-faint">
            共 <span className="mw-num font-semibold mw-strong">{total}</span> 名患者
          </span>
        </div>

        <PatientFilterBar
          keyword={keyword}
          onKeywordChange={(v) => {
            setKeyword(v)
            setPage(1)
          }}
          gender={gender}
          onGenderChange={(v) => {
            setGender(v)
            setPage(1)
          }}
          risk={risk}
          onRiskChange={(v) => {
            setRisk(v)
            setPage(1)
          }}
          onReset={reset}
          dirty={dirty}
        />

        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-5 w-5 animate-spin text-[var(--c-accent)]" />
          </div>
        ) : (
          <PatientTable rows={rows} onOpen={onOpenPatient} />
        )}

        <PagePager page={page} total={total} pageSize={PAGE_SIZE} onChange={setPage} />
      </Panel>
    </div>
  )
}
