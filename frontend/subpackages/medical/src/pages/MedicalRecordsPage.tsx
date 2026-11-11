/**
 * 智能病历页 —— 设计稿 v3 的实装
 *
 * 结构 / 类名对应那份 v3 设计稿，不在实现层改版式：
 *   .top-panel / .page-header → .main-cols（col-browse 患者列表 / col-detail 单页文档）两区
 *   注：原「患者列 + 报告列 + 详情列」三平行栏已收敛为两区（左=患者列表导航，右=单页）。
 *   **注**：原 v3 顶部「四步使用引导栏」已移除（静态常驻、占高、信息密度低）。
 * **注意**：HIS 多来源版设计原型现存于 docs/prototype/smart-records-v4-clean.html，
 * 可作为版式对照；但本页面实现 + styles/v3-records.css 才是唯一权威来源。
 *
 * 右栏形态（本轮重构）：**患者档案页**，四段纵向结构
 *   ① .pt-hero   患者身份（姓名 / 风险 / 年龄性别 / 诊断·分期·科室）+ 查看档案 / 上传报告
 *   ② .pt-toolbar 来源分段 + 检索 + 类型 / 状态筛选
 *   ③ .pt-year   按年分段的报告时间轴；每行 = 日期 · 类型 · 标题 · 医院/科室/医师 · 核心诊断
 *   ④ 行内标：解析失败 / 需核验 / 解析中（lib/review.ts 判定，语义色不滥用）
 *   点击任意行 → ReportDetailModal（详情与修正编辑态都收敛在弹窗里）。
 *
 * 左栏：患者列表纯文字 —— 姓名 + 风险 + 诊断；首字头像与「N 份」已移除。
 *       顶部加 .browse-head（标题 + 总数），与右栏 .pt-hero 同高，两栏分隔线落在同一水平线上。
 *
 * 上传有两个入口，共用 UploadModal：
 *   · 页头「上传病历」= 全局上传，归属交给 AI 识别，识别不出进「待指派」；
 *   · 患者页「上传报告」= 页内上传，带 patientId，归属就此确定（解析只做对账）。
 *
 * 数据来自真实接口（listPatients / listPatientReports / listRecords / listInbox / assignReport）。
 */

import { useCallback, useEffect, useState } from 'react'
import {
  listPatients,
  listPatientReports,
  listRecords,
  listInbox,
  assignReport,
} from '../api/client'
import { UploadModal } from '../features/records/UploadModal'
import { AssignDrawer } from '../features/records/AssignDrawer'
import { InboxSlideOut } from '../features/records/InboxSlideOut'
import { ReportDetailModal } from '../features/records/ReportDetailModal'
import { groupReportsByYear, splitVisitDate } from '../lib/format'
import { resolveReportType } from '../lib/reportTypes'
import { reportFlag } from '../lib/review'
import { reportSource, SOURCE_META } from '../lib/source'
import type { PatientProfile, PatientReport } from '../types/view'

const PAGE_SIZE = 10

/** 风险分层 → 设计稿 pill 类名 */
const RISK_CLASS: Record<string, string> = { 高危: 'high', 中危: 'mid', 低危: 'low' }

/** 报告检索类型筛选项（Phase 1 常用类型子集；value = report_type 原始值） */
const REPORT_TYPE_FILTERS: { value: string; label: string }[] = [
  { value: 'outpatient', label: '门诊病历' },
  { value: 'inpatient', label: '住院病历' },
  { value: 'imaging', label: '影像报告' },
  { value: 'lab', label: '检验报告' },
  { value: 'pathology', label: '病理报告' },
  { value: 'gene_test', label: '基因检测' },
  { value: 'surgery', label: '手术记录' },
  { value: 'other', label: '其他' },
]

/** 患者身份行的事实串：年龄 / 性别 / 诊断 / 分期 / 科室 / 治疗状态，空值自动去掉 */
function patientFacts(p: PatientProfile): string[] {
  return [
    p.age ? `${p.age} 岁` : '',
    p.gender || '',
    p.diagnosis || '',
    p.stage || '',
    p.department || '',
    p.status || '',
  ].filter(Boolean)
}

/** 报告行的机构行：医院 · 科室 · 医师（都没有则不渲染这一行） */
function reportPlace(r: PatientReport): string[] {
  return [r.hospitalName, r.department, r.doctorName].filter(Boolean)
}

export function MedicalRecordsPage({
  onOpenPatient,
}: {
  onOpenPatient: (patientId: string) => void
}) {
  // ── 患者列表（一栏）
  const [patients, setPatients] = useState<PatientProfile[]>([])
  const [patientTotal, setPatientTotal] = useState(0)
  const [patientPage, setPatientPage] = useState(1)
  const [patientSearch, setPatientSearch] = useState('')
  const [patientsLoading, setPatientsLoading] = useState(true)
  const [selectedPatient, setSelectedPatient] = useState<PatientProfile | null>(null)

  // ── 报告列表（右栏患者页的时间轴）
  const [reports, setReports] = useState<PatientReport[]>([])
  const [reportTotal, setReportTotal] = useState(0)
  const [reportPage, setReportPage] = useState(1)
  const [reportsLoading, setReportsLoading] = useState(false)
  /** 当前弹窗查看的报告；null = 弹窗关闭 */
  const [detailReport, setDetailReport] = useState<PatientReport | null>(null)

  // ── 报告检索（Phase 1：关键词 + 类型 + 状态）
  const [reportSearch, setReportSearch] = useState('')
  const [reportTypeFilter, setReportTypeFilter] = useState('')
  const [reportStatusFilter, setReportStatusFilter] = useState('')
  const searchActive = Boolean(reportSearch.trim() || reportTypeFilter || reportStatusFilter)

  // ── 待整理（AI 未识别归属的文档）
  const [inbox, setInbox] = useState<PatientReport[]>([])
  // 侧窗改按需展开：常驻 360px 会把两栏挤瘪。展开态才占宽度。
  const [inboxOpen, setInboxOpen] = useState(false)
  const hasInbox = inbox.length > 0
  // 指派抽屉（替代旧的「整列翻可指派态」模式）
  const [assignOpen, setAssignOpen] = useState(false)
  const [assignTarget, setAssignTarget] = useState<PatientReport | null>(null)
  const [assignMode, setAssignMode] = useState<'assign' | 'move'>('assign')
  const [assignCandidates, setAssignCandidates] = useState<PatientReport['candidates']>(undefined)

  // ── 上传：uploadFor 为 null = 全局上传；非空 = 页内上传（归属锁定到该患者）
  const [uploadOpen, setUploadOpen] = useState(false)
  const [uploadFor, setUploadFor] = useState<PatientProfile | null>(null)
  const openUpload = (target?: PatientProfile) => {
    setUploadFor(target ?? null)
    setUploadOpen(true)
  }

  // ── HIS 多来源：来源筛选（数据驱动：未接入 HIS 时筛选自动隐藏）
  const [sourceFilter, setSourceFilter] = useState<'all' | 'his' | 'upload'>('all')

  // ── 拉取
  const fetchPatients = useCallback(async () => {
    setPatientsLoading(true)
    try {
      const paged = await listPatients({
        keyword: patientSearch.trim() || undefined,
        page: patientPage,
        pageSize: PAGE_SIZE,
      })
      setPatients(paged.items)
      setPatientTotal(paged.total)
    } finally {
      setPatientsLoading(false)
    }
  }, [patientSearch, patientPage])

  const fetchInbox = useCallback(async () => {
    const paged = await listInbox({ page: 1, pageSize: PAGE_SIZE })
    setInbox(paged.items)
    return paged.items
  }, [])

  const fetchReports = useCallback(async () => {
    setReportsLoading(true)
    try {
      // 检索模式：跨患者全局搜索（关键词 + 类型 + 状态）
      if (searchActive) {
        const res = await listRecords({
          keyword: reportSearch.trim() || undefined,
          reportType: reportTypeFilter || undefined,
          status: reportStatusFilter || undefined,
          page: reportPage,
          pageSize: PAGE_SIZE,
        })
        setReports(res.items)
        setReportTotal(res.total)
        // 详情改为点击弹窗：不再自动选中首条（否则一进列表就自动弹窗）。
        setDetailReport((prev) =>
          prev && res.items.some((r) => r.id === prev.id) ? prev : null,
        )
        return
      }
      // 浏览模式：选中患者的报告
      if (!selectedPatient) {
        setReports([])
        setReportTotal(0)
        setDetailReport(null)
        return
      }
      const res = await listPatientReports(selectedPatient.id, {
        page: reportPage,
        pageSize: PAGE_SIZE,
      })
      setReports(res.items)
      setReportTotal(res.total)
      setDetailReport((prev) =>
        prev && res.items.some((r) => r.id === prev.id) ? prev : null,
      )
    } finally {
      setReportsLoading(false)
    }
  }, [searchActive, reportSearch, reportTypeFilter, reportStatusFilter, selectedPatient, reportPage])

  useEffect(() => { void fetchPatients() }, [fetchPatients])
  useEffect(() => { void fetchReports() }, [fetchReports])
  useEffect(() => { void fetchInbox() }, [fetchInbox])

  const refreshAll = useCallback(async () => {
    await fetchInbox()
    await fetchPatients()
  }, [fetchInbox, fetchPatients])

  /** 弹窗内修正保存成功：同步列表里那一条，避免列表仍是保存前的值 */
  const handleSaved = useCallback((updated: PatientReport) => {
    setReports((prev) => prev.map((r) => (r.id === updated.id ? updated : r)))
    setDetailReport(updated)
  }, [])

  // 指派抽屉的确认回调：真实写库交回 assignReport，失败抛给抽屉展示
  const confirmAssign = async (patientId: string) => {
    if (!assignTarget) return
    await assignReport(assignTarget.id, patientId)
    await refreshAll()
    // 归档 / 移出目标正是当前查看的患者时，报告列表也得跟着变，
    // 否则会出现「N 份、列表 N-1 条」的不一致
    if (selectedPatient?.id === patientId) await fetchReports()
  }

  const openAssign = (
    report: PatientReport,
    mode: 'assign' | 'move',
    candidates?: PatientReport['candidates'],
  ) => {
    setAssignTarget(report)
    setAssignMode(mode)
    setAssignCandidates(mode === 'assign' ? candidates : undefined)
    setAssignOpen(true)
  }

  // ── 派生
  const patientPages = Math.max(1, Math.ceil(patientTotal / PAGE_SIZE))
  const reportPages = Math.max(1, Math.ceil(reportTotal / PAGE_SIZE))

  const visibleReports =
    sourceFilter === 'all' ? reports : reports.filter((r) => reportSource(r) === sourceFilter)
  const hasHis = reports.some((r) => reportSource(r) === 'his')
  const hisSyncedAt = hasHis
    ? reports
        .filter((r) => reportSource(r) === 'his' && r.syncedAt)
        .map((r) => r.syncedAt as string)
        .sort()
        .slice(-1)[0] ?? null
    : null
  const reportGroups = groupReportsByYear(visibleReports)
  const inReportView = Boolean(selectedPatient || searchActive)
  /** 身份带事实串：字段可能大面积缺失（新建档案 / HIS 半结构化），空数组就不渲染 */
  const heroFacts = selectedPatient ? patientFacts(selectedPatient) : []

  return (
    <div className={`app mw-v3 h-full${inboxOpen ? ' inbox-shown' : ''}`} style={{ display: 'flex', flexDirection: 'column' }}>
      {/* ── 顶部面板 ── */}
      <div className="top-panel">
        <div className="page-header">
          <div className="header-icon">病</div>
          <div>
            <div className="header-title">智能病历</div>
            <div className="header-subtitle">AI 驱动的临床病历管理</div>
          </div>
          {/* 右侧操作组：HIS 状态 / 上传 / 待指派。待指派放这里（页头），不再浮在某个患者的页面上 */}
          <div className="header-actions">
            {hasHis && (
              <span className="his-sync-chip">
                <span className="pulse" />
                HIS 同步 {hisSyncedAt ?? '实时'}
              </span>
            )}
            <button className="upload-btn-solid" onClick={() => openUpload()}>
              <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M8 3v10" />
                <path d="M3 8h10" />
              </svg>
              上传病历
            </button>
            {hasInbox && (
              <button className="inbox-trigger" onClick={() => setInboxOpen(true)}>
                待指派 {inbox.length}
              </button>
            )}
          </div>
        </div>
      </div>

      {/* ── 两区：左=患者列表，右=患者档案页 ── */}
      <div className="main-cols">
        {/* 左：患者列表（纯文字：姓名 + 风险 + 诊断） */}
        <div className="col-browse">
          <div className="browse-top">
            {/* 与右栏 .pt-hero 同高：两栏分隔线落在同一水平线上 */}
            <div className="browse-head">
              <span className="browse-title">患者</span>
              <span className="browse-count">{patientTotal}</span>
            </div>
            <div className="search-box">
              <span className="search-icon">
                <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                  <circle cx="6.5" cy="6.5" r="5" />
                  <path d="M10.5 10.5L14 14" />
                </svg>
              </span>
              <input
                placeholder="搜索患者姓名、诊断..."
                value={patientSearch}
                onChange={(e) => { setPatientSearch(e.target.value); setPatientPage(1) }}
              />
            </div>
          </div>

          <div className="browse-tree">
            {patients.map((p) => (
              <div
                key={p.id}
                className={`patient-node${selectedPatient?.id === p.id ? ' active' : ''}`}
                onClick={() => { setSelectedPatient(p); setReportPage(1) }}
              >
                <div className="patient-info">
                  <div className="patient-name-row">
                    <span className="patient-name">{p.name}</span>
                    <span className={`risk-pill ${RISK_CLASS[p.risk] ?? 'low'}`}>{p.risk}</span>
                  </div>
                  <div className="patient-diagnosis">{p.diagnosis}</div>
                </div>
              </div>
            ))}
            {patientsLoading && <div className="patient-diagnosis">加载中…</div>}
            {!patientsLoading && patients.length === 0 && (
              <div className="patient-diagnosis">暂无患者</div>
            )}
          </div>
          {patientPages > 1 && (
          <div className="pager">
            <button
              className={`pager-btn${patientPage > 1 ? '' : ' disabled'}`}
              onClick={() => setPatientPage((n) => Math.max(1, n - 1))}
            >
              ‹
            </button>
            <span>{patientPage} / {patientPages}</span>
            <button
              className={`pager-btn${patientPage < patientPages ? '' : ' disabled'}`}
              onClick={() => setPatientPage((n) => Math.min(patientPages, n + 1))}
            >
              ›
            </button>
          </div>
          )}
        </div>

        {/* 右：患者档案页 */}
        <div className="col-detail">
          <div className="detail-scroll">
            {inReportView ? (
              <>
                {/* ① 患者身份带：谁 · 什么病 · 能做什么 */}
                <div className="pt-hero">
                  <div className="pt-hero-main">
                    <div className="pt-name-row">
                      <h2 className="pt-name">
                        {searchActive ? '报告检索' : selectedPatient?.name}
                      </h2>
                      {!searchActive && selectedPatient && RISK_CLASS[selectedPatient.risk] && (
                        <span className={`risk-pill ${RISK_CLASS[selectedPatient.risk]}`}>
                          {selectedPatient.risk}
                        </span>
                      )}
                      {!searchActive && heroFacts.length > 0 && (
                        <span className="pt-facts">{heroFacts.join(' · ')}</span>
                      )}
                      {searchActive && <span className="pt-facts">共 {reportTotal} 份匹配</span>}
                    </div>
                    <div className="pt-sub">
                      {searchActive ? (
                        '跨患者检索，点击结果查看详情'
                      ) : (
                        <>
                          <span className="pt-count">{reportTotal} 份报告</span>
                          {selectedPatient && selectedPatient.cycle && (
                            <span className="pt-sub-item">当前周期 {selectedPatient.cycle}</span>
                          )}
                        </>
                      )}
                    </div>
                  </div>
                  {!searchActive && selectedPatient && (
                    <div className="pt-hero-actions">
                      <button
                        className="pt-btn pt-btn-ghost"
                        onClick={() => onOpenPatient(selectedPatient.id)}
                      >
                        查看档案
                      </button>
                      <button
                        className="pt-btn pt-btn-primary"
                        onClick={() => openUpload(selectedPatient)}
                      >
                        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                          <path d="M8 3v10" />
                          <path d="M3 8h10" />
                        </svg>
                        上传报告
                      </button>
                    </div>
                  )}
                </div>

                {/* ② 工具条：来源分段 + 检索 + 类型 / 状态 */}
                <div className="pt-toolbar">
                  {hasHis && (
                    <div className="source-tab-group">
                      {(['all', 'his', 'upload'] as const).map((k) => (
                        <button
                          key={k}
                          type="button"
                          className={`source-tab${sourceFilter === k ? ' on' : ''}`}
                          onClick={() => setSourceFilter(k)}
                        >
                          {k === 'all' ? '全部' : SOURCE_META[k].label}
                        </button>
                      ))}
                    </div>
                  )}
                  <input
                    className="report-search-input"
                    placeholder="搜索报告"
                    title="可搜索：患者 / 诊断 / 摘要 / 文件名"
                    aria-label="搜索报告，可搜索患者、诊断、摘要、文件名"
                    value={reportSearch}
                    onChange={(e) => { setReportSearch(e.target.value); setReportPage(1) }}
                  />
                  <select
                    className="report-filter"
                    value={reportTypeFilter}
                    onChange={(e) => { setReportTypeFilter(e.target.value); setReportPage(1) }}
                  >
                    <option value="">全部类型</option>
                    {REPORT_TYPE_FILTERS.map((t) => (
                      <option key={t.value} value={t.value}>{t.label}</option>
                    ))}
                  </select>
                  <select
                    className="report-filter"
                    value={reportStatusFilter}
                    onChange={(e) => { setReportStatusFilter(e.target.value); setReportPage(1) }}
                  >
                    <option value="">全部状态</option>
                    <option value="已解析">已解析</option>
                    <option value="解析中">解析中</option>
                    <option value="失败">失败</option>
                  </select>
                  {searchActive && (
                    <button
                      type="button"
                      className="report-search-clear"
                      onClick={() => {
                        setReportSearch('')
                        setReportTypeFilter('')
                        setReportStatusFilter('')
                        setReportPage(1)
                      }}
                    >
                      清除
                    </button>
                  )}
                </div>

                {/* ③ 时间轴：按年分段，每行一份报告，点击弹窗看详情 */}
                <div className="pt-timeline">
                  {reportsLoading && <p className="pt-hint">加载中…</p>}
                  {!reportsLoading && visibleReports.length === 0 && (
                    <div className="pt-blank">
                      <p className="pt-blank-title">还没有报告</p>
                      <p className="pt-blank-hint">
                        {selectedPatient && !searchActive
                          ? '上传该患者的检查、病理或门诊记录，解析后按时间自动排序。'
                          : '换个关键词，或清除筛选条件再试。'}
                      </p>
                      {selectedPatient && !searchActive && (
                        <button className="pt-btn pt-btn-primary" onClick={() => openUpload(selectedPatient)}>
                          上传报告
                        </button>
                      )}
                    </div>
                  )}
                  {!reportsLoading && reportGroups.map((g) => (
                    <section className="pt-year" key={g.year}>
                      <div className="pt-year-head">
                        <span className="pt-year-label">{g.year}</span>
                        <span className="pt-year-rule" />
                      </div>
                      {g.items.map((r) => {
                        const flag = reportFlag(r)
                        const sm = SOURCE_META[reportSource(r)]
                        const { md } = splitVisitDate(r.visitDate || r.date)
                        const place = reportPlace(r)
                        const typeLabel = resolveReportType(r.reportType).label
                        return (
                          <div className="ptl-row" key={r.id}>
                            <span className="ptl-date">{md}</span>
                            <span className="ptl-rail" aria-hidden>
                              <span className={`ptl-dot ${sm.cls}`} />
                            </span>
                            <button
                              type="button"
                              className="ptl-card"
                              onClick={() => setDetailReport(r)}
                              title={`${typeLabel}｜${r.name}｜来源：${sm.label}${flag ? `｜${flag.hint}` : ''}`}
                            >
                              <span className="ptl-l1">
                                <span className="ptl-type">{typeLabel}</span>
                                <span className="ptl-title">{r.name || typeLabel}</span>
                                {searchActive && r.patientName && (
                                  <span className="ptl-pt">{r.patientName}</span>
                                )}
                                {flag && (
                                  <span className={`ptl-flag ${flag.tone}`} title={flag.hint}>
                                    {flag.text}
                                  </span>
                                )}
                              </span>
                              {place.length > 0 && (
                                <span className="ptl-l2">{place.join(' · ')}</span>
                              )}
                              {r.diagnosis && (
                                <span className="ptl-l3">
                                  <span className="ptl-l3-key">诊断</span>
                                  <span className="ptl-l3-text">{r.diagnosis}</span>
                                </span>
                              )}
                            </button>
                            <span className="ptl-chev" aria-hidden>›</span>
                          </div>
                        )
                      })}
                    </section>
                  ))}
                  {reportPages > 1 && (
                    <div className="doc-dir-pager">
                      <button
                        className={`pager-btn${reportPage > 1 ? '' : ' disabled'}`}
                        onClick={() => setReportPage((n) => Math.max(1, n - 1))}
                      >
                        ‹
                      </button>
                      <span>{reportPage} / {reportPages}</span>
                      <button
                        className={`pager-btn${reportPage < reportPages ? '' : ' disabled'}`}
                        onClick={() => setReportPage((n) => Math.min(reportPages, n + 1))}
                      >
                        ›
                      </button>
                    </div>
                  )}
                </div>
              </>
            ) : (
              <div className="pt-intro">
                <div className="pt-intro-mark">病</div>
                <h2 className="pt-intro-title">选择一位患者开始</h2>
                <p className="pt-intro-text">
                  左栏是患者列表。选中一位患者，右侧会按年份展开他的检查、病理与门诊记录。
                </p>
                <button className="pt-btn pt-btn-primary" onClick={() => openUpload()}>
                  上传病历
                </button>
                <p className="pt-intro-note">不指定患者也能上传，系统会识别报告里的姓名再归位。</p>
              </div>
            )}
          </div>
        </div>
      </div>

      <UploadModal
        open={uploadOpen}
        patient={uploadFor ? { id: uploadFor.id, name: uploadFor.name } : null}
        onClose={() => setUploadOpen(false)}
        onUploaded={() => { void refreshAll(); void fetchReports() }}
      />

      {/* 详情弹窗：点列表任意一行打开；关闭入口 = 右上角 X / 点遮罩 / Esc */}
      {detailReport && (
        <ReportDetailModal
          report={detailReport}
          onClose={() => setDetailReport(null)}
          onSaved={handleSaved}
        />
      )}

      {/* 待指派触发在页头「上传病历」右侧；此处只保留侧窗本体 */}
      <InboxSlideOut
        open={inboxOpen}
        onClose={() => setInboxOpen(false)}
        reports={inbox}
        onPick={(r) => openAssign(r, 'assign', r.candidates)}
      />

      {assignOpen && assignTarget && (
        <AssignDrawer
          open={assignOpen}
          report={assignTarget}
          candidates={assignCandidates}
          mode={assignMode}
          currentPatientId={assignTarget.patientId || undefined}
          onConfirm={confirmAssign}
          onClose={() => setAssignOpen(false)}
        />
      )}
    </div>
  )
}
