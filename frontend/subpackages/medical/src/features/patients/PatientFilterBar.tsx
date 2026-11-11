/**
 * 患者管理 · 多维筛选条
 *
 * 关键词 + 性别 + 风险三个维度。刻意不含「治疗状态」——本产品无 HIS 数据源，
 * 治疗状态来自人工录入，不作为筛选维度。
 */

import { RotateCcw } from 'lucide-react'
import { Btn, Panel, SearchInput, Select } from '../../components'

export const GENDER_OPTIONS = ['全部', '男', '女'] as const
export const RISK_OPTIONS = ['全部', '高危', '中危', '低危'] as const

interface PatientFilterBarProps {
  keyword: string
  onKeywordChange: (v: string) => void
  gender: string
  onGenderChange: (v: string) => void
  risk: string
  onRiskChange: (v: string) => void
  onReset: () => void
  /** 是否存在任一生效中的筛选条件（决定重置按钮是否显示） */
  dirty: boolean
}

export function PatientFilterBar({
  keyword,
  onKeywordChange,
  gender,
  onGenderChange,
  risk,
  onRiskChange,
  onReset,
  dirty,
}: PatientFilterBarProps) {
  return (
    <Panel className="px-3.5 py-3 mb-3 flex flex-wrap items-center gap-x-3 gap-y-2">
      {/* 关键词搜索：固定舒适宽度，不再拉伸全行 */}
      <SearchInput
        value={keyword}
        onChange={onKeywordChange}
        placeholder="搜索姓名 / 手机号 / 证件号 / 诊断..."
        className="w-64 max-w-full"
      />

      <span className="h-5 w-px bg-[var(--c-border)] hidden md:block" />

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 flex-1 min-w-0">
        <label className="flex items-center gap-1.5 cursor-pointer">
          <span className="text-[length:var(--fs-sm)] mw-faint shrink-0">性别</span>
          <Select value={gender} onChange={onGenderChange} options={GENDER_OPTIONS} />
        </label>
        <label className="flex items-center gap-1.5 cursor-pointer">
          <span className="text-[length:var(--fs-sm)] mw-faint shrink-0">风险</span>
          <Select value={risk} onChange={onRiskChange} options={RISK_OPTIONS} />
        </label>
      </div>

      {dirty && (
        <Btn size="sm" onClick={onReset}>
          <RotateCcw className="h-3 w-3" />
          重置
        </Btn>
      )}
    </Panel>
  )
}
