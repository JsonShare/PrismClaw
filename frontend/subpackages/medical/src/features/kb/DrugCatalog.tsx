/**
 * 医学知识库 · 药品目录（设计稿三栏：一级分类 / 二级分类 / 药物组）
 *
 * 数据一次取全（后端上限 500 条），在内存里组树；检索时第三栏切换为扁平搜索结果。
 * 点击药名展开说明书要点，保留原有详情能力。
 */

import { useEffect, useMemo, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { listDrugs } from '../../api/client'
import type { DrugEntry } from '../../types/view'
import { unique } from './kb-utils'

const KINDS = ['西药', '中成药'] as const
type DrugKind = (typeof KINDS)[number]

function DrugDetail({ drug }: { drug: DrugEntry }) {
  const hasDetail =
    drug.dosage || drug.indications.length > 0 || drug.sideEffects.length > 0 || drug.mechanism
  if (!hasDetail) {
    return <p className="mb-2 text-[length:var(--fs-xs)] mw-faint">暂无说明书详情</p>
  }
  return (
    <div className="mb-2 space-y-1 rounded-lg bg-[var(--c-surface-2)] p-3 text-[length:var(--fs-xs)] leading-relaxed mw-muted">
      {drug.generic && (
        <p>
          <span className="mw-faint">通用名　</span>
          {drug.generic}
        </p>
      )}
      {drug.dosage && (
        <p>
          <span className="mw-faint">用法用量　</span>
          {drug.dosage}
        </p>
      )}
      {drug.indications.length > 0 && (
        <p>
          <span className="mw-faint">适应症　</span>
          {drug.indications.join('、')}
        </p>
      )}
      {drug.sideEffects.length > 0 && (
        <p>
          <span className="mw-faint">主要不良反应　</span>
          {drug.sideEffects.join('、')}
        </p>
      )}
      {drug.contraindications.length > 0 && (
        <p>
          <span className="mw-faint">禁忌　</span>
          {drug.contraindications.join('、')}
        </p>
      )}
    </div>
  )
}

export function DrugCatalog({ keyword }: { keyword: string }) {
  const [items, setItems] = useState<DrugEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [kind, setKind] = useState<DrugKind>('西药')
  const [l1, setL1] = useState('')
  const [l2, setL2] = useState('')
  const [openId, setOpenId] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    listDrugs({ pageSize: 500 })
      .then((res) => {
        if (alive) setItems(res.items)
      })
      .catch(() => {
        if (alive) setItems([])
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [])

  const kindItems = useMemo(
    () => items.filter((d) => (d.kind || '西药') === kind),
    [items, kind],
  )

  const l1List = useMemo(() => unique(kindItems.map((d) => d.atcL1).filter(Boolean)), [kindItems])
  const curL1 = l1List.includes(l1) ? l1 : (l1List[0] ?? '')

  const l2List = useMemo(
    () => unique(kindItems.filter((d) => d.atcL1 === curL1).map((d) => d.atcL2).filter(Boolean)),
    [kindItems, curL1],
  )
  const curL2 = l2List.includes(l2) ? l2 : (l2List[0] ?? '')

  const groups = useMemo(() => {
    const map = new Map<string, DrugEntry[]>()
    for (const d of kindItems) {
      if (d.atcL1 !== curL1 || d.atcL2 !== curL2) continue
      const key = d.atcGroup || d.atcL2
      const list = map.get(key)
      if (list) list.push(d)
      else map.set(key, [d])
    }
    return Array.from(map, ([name, drugs]) => ({
      name,
      desc: drugs[0]?.atcGroupDesc ?? '',
      drugs,
    }))
  }, [kindItems, curL1, curL2])

  const q = keyword.trim()
  const searchRows = useMemo(
    () => (q ? kindItems.filter((d) => d.name.includes(q)) : null),
    [kindItems, q],
  )

  const switchKind = (next: DrugKind) => {
    if (next === kind) return
    setKind(next)
    setL1('')
    setL2('')
    setOpenId(null)
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="h-5 w-5 animate-spin text-[var(--c-accent)]" />
      </div>
    )
  }

  return (
    <section>
      <div className="flex items-center gap-8 border-b border-[var(--c-border)]">
        {KINDS.map((k) => {
          const on = k === kind
          return (
            <button
              key={k}
              type="button"
              onClick={() => switchKind(k)}
              className={`relative cursor-pointer pb-3 text-[length:var(--fs-md)] transition-colors ${
                on ? 'font-semibold mw-strong' : 'mw-muted hover:text-[var(--c-text)]'
              }`}
            >
              {k}
              {on && (
                <span className="absolute -bottom-px left-1/2 h-0.5 w-6 -translate-x-1/2 rounded-full bg-[var(--c-success)]" />
              )}
            </button>
          )
        })}
      </div>

      <div className="mt-6 grid grid-cols-[200px_220px_minmax(0,1fr)]">
        {/* 一级分类 */}
        <div className="border-r border-[var(--c-border)] pr-5">
          <div className="flex flex-col gap-1">
            {l1List.map((name) => {
              const on = name === curL1
              return (
                <button
                  key={name}
                  type="button"
                  onClick={() => {
                    setL1(name)
                    setL2('')
                    setOpenId(null)
                  }}
                  className={`cursor-pointer py-2 text-left text-[length:var(--fs-sm)] transition-colors ${
                    on
                      ? 'font-semibold text-[var(--c-success)]'
                      : 'mw-muted hover:text-[var(--c-text)]'
                  }`}
                >
                  {name}
                </button>
              )
            })}
          </div>
        </div>

        {/* 二级分类 */}
        <div className="border-r border-[var(--c-border)] px-5">
          <p className="mb-3 text-[length:var(--fs-sm)] font-semibold text-[var(--c-success)]">{curL1}</p>
          <div className="flex flex-col gap-1">
            {l2List.map((name) => {
              const on = name === curL2
              return (
                <button
                  key={name}
                  type="button"
                  onClick={() => {
                    setL2(name)
                    setOpenId(null)
                  }}
                  className={`cursor-pointer py-2 text-left text-[length:var(--fs-sm)] transition-colors ${
                    on ? 'font-semibold mw-strong' : 'mw-muted hover:text-[var(--c-text)]'
                  }`}
                >
                  {name}
                </button>
              )
            })}
          </div>
        </div>

        {/* 药物组 / 搜索结果 */}
        <div className="pl-6">
          {searchRows ? (
            <>
              <p className="text-[length:var(--fs-sm)] font-semibold mw-strong">
                搜索结果（{searchRows.length}）
              </p>
              {searchRows.length === 0 ? (
                <p className="py-10 text-[length:var(--fs-sm)] mw-faint">未找到相关药品</p>
              ) : (
                <div className="mt-2">
                  {searchRows.map((d) => (
                    <div key={d.id}>
                      <button
                        type="button"
                        onClick={() => setOpenId(openId === d.id ? null : d.id)}
                        className="w-full cursor-pointer py-2 text-left text-[length:var(--fs-sm)] mw-strong transition-colors hover:text-[var(--c-success)]"
                      >
                        {d.name}
                        <span className="ml-2 text-[length:var(--fs-xs)] mw-faint">{d.atcL2}</span>
                      </button>
                      {openId === d.id && <DrugDetail drug={d} />}
                    </div>
                  ))}
                </div>
              )}
            </>
          ) : (
            <>
              <p className="text-[length:var(--fs-md)] font-semibold mw-strong">{curL2}</p>
              {groups.length === 0 && (
                <p className="py-10 text-[length:var(--fs-sm)] mw-faint">该分类下暂无药品</p>
              )}
              {groups.map((group) => (
                <div key={group.name} className="mt-4 first:mt-3">
                  {group.desc && (
                    <p className="text-[length:var(--fs-xs)] mw-faint">{group.desc}</p>
                  )}
                  <div className="mt-1">
                    {group.drugs.map((d) => (
                      <div key={d.id}>
                        <button
                          type="button"
                          onClick={() => setOpenId(openId === d.id ? null : d.id)}
                          className="w-full cursor-pointer py-2 text-left text-[length:var(--fs-sm)] mw-strong transition-colors hover:text-[var(--c-success)]"
                        >
                          {d.name}
                        </button>
                        {openId === d.id && <DrugDetail drug={d} />}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      </div>
    </section>
  )
}
