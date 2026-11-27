import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type CSSProperties,
} from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronDown } from 'lucide-react'
import {
  getDomainEntry,
  getDomainNavItems,
  getDomainOrder,
  getRegistryVersion,
  subscribeRegistryVersion,
  type DomainNavItem,
} from '../../domains/registry'
import { useUiStore } from '../../stores/ui'
import iconWorkbench from '../../images/other_icon/workbench.png'
import iconClose from '../../images/other_icon/close.png'

/**
 * 侧栏顶部的工作台切换器。
 * 切换的只是「业务域」上下文，不增删侧栏菜单；选择结果只缓存到本机（localStorage）。
 */

/** 下拉里的一条：域 key / 域名 / 首字 / 菜单概览 */
interface WorkbenchOption {
  key: string
  label: string
  initial: string
  desc: string
}

/**
 * 首字配色板：域名第一个字的码点取模，同一个字永远同一色。
 * 新增业务域不用改代码，颜色自动固定下来；底色用同一色的 14% 透明，
 * 这样明暗主题都成立，不需要为两套主题各维护一组底色。
 */
const TONES = ['#4C5BD4', '#1F8A70', '#B4761A', '#B4476B', '#7A4FBF', '#2F6FBF']

function toneOf(text: string): string {
  const codePoint = text.codePointAt(0) ?? 0
  return TONES[codePoint % TONES.length]
}

/** 首字徽标的样式：色字 + 同色淡底 */
function toneStyle(tone: string): CSSProperties {
  return { color: tone, background: `color-mix(in srgb, ${tone} 14%, transparent)` }
}

/** 单色 png 用 mask 渲染，颜色跟随 currentColor（明暗主题通用） */
function maskStyle(url: string): CSSProperties {
  return {
    WebkitMaskImage: `url("${url}")`,
    maskImage: `url("${url}")`,
    WebkitMaskRepeat: 'no-repeat',
    maskRepeat: 'no-repeat',
    WebkitMaskPosition: 'center',
    maskPosition: 'center',
    WebkitMaskSize: 'contain',
    maskSize: 'contain',
  }
}

/** 菜单概览：直接拼该域的菜单名称，改 domains.yaml 重建即变 */
function describeDomain(items: DomainNavItem[]): string {
  return items.map((i) => i.label).join(' · ')
}

export function WorkbenchSwitcher() {
  // 域是异步注册的（启动后拉 /api/domains/manifests），必须订阅版本号
  useSyncExternalStore(subscribeRegistryVersion, getRegistryVersion)
  const activeDomain = useUiStore((s) => s.activeDomain)
  const setActiveDomain = useUiStore((s) => s.setActiveDomain)

  const [open, setOpen] = useState(false)
  /** 弹层用 fixed 挂在 body 上（侧栏 overflow:hidden 会裁掉），位置由胶囊实时测量 */
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null)
  const pillRef = useRef<HTMLDivElement>(null)
  const popupRef = useRef<HTMLDivElement>(null)

  const syncPosition = useCallback(() => {
    const el = pillRef.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    // 侧栏收起时胶囊宽度归零，顺带收起弹层，避免浮在空白处
    if (rect.width < 1) {
      setOpen(false)
      return
    }
    const left = Math.round(rect.left)
    const top = Math.round(rect.bottom + 6)
    setPos((prev) => (prev && prev.left === left && prev.top === top ? prev : { left, top }))
  }, [])

  useLayoutEffect(() => {
    if (!open) return
    syncPosition()
    window.addEventListener('resize', syncPosition)
    window.addEventListener('scroll', syncPosition, true)
    const ro = new ResizeObserver(syncPosition)
    if (pillRef.current) ro.observe(pillRef.current)
    return () => {
      window.removeEventListener('resize', syncPosition)
      window.removeEventListener('scroll', syncPosition, true)
      ro.disconnect()
    }
  }, [open, syncPosition])

  useEffect(() => {
    if (!open) return
    function onDoc(event: MouseEvent) {
      const target = event.target
      if (!(target instanceof Node)) return
      // 弹层在 portal 里，不在胶囊的 DOM 子树内，两处都要判断
      if (pillRef.current?.contains(target)) return
      if (popupRef.current?.contains(target)) return
      setOpen(false)
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  // 顺序与侧栏一致（取构建期生成表键序），只保留后端已启用且前端已编译的域
  const options: WorkbenchOption[] = []
  for (const key of getDomainOrder()) {
    const entry = getDomainEntry(key)
    if (!entry) continue
    const items = getDomainNavItems(key)
    options.push({
      key,
      label: entry.manifest.label,
      initial: entry.manifest.label.trim().slice(0, 1),
      desc: describeDomain(items),
    })
  }

  // 缓存里的域被裁剪/禁用后不再有效，视为未选择（不清缓存，域恢复后仍可还原）
  const current = options.find((o) => o.key === activeDomain) ?? null

  if (options.length === 0) return null

  return (
    <>
      <div
        ref={pillRef}
        className={`inline-flex items-center h-8 rounded-full border transition-colors duration-150 ${
          open
            ? 'bg-[color-mix(in_srgb,var(--c-text)_9%,var(--c-surface-2))] border-[var(--c-border-strong)]'
            : 'bg-[color-mix(in_srgb,var(--c-text)_6%,var(--c-surface-2))] border-[var(--c-border)] hover:bg-[color-mix(in_srgb,var(--c-text)_9%,var(--c-surface-2))] hover:border-[var(--c-border-strong)]'
        }`}
      >
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-haspopup="listbox"
          aria-expanded={open}
          title="切换工作台"
          className="inline-flex items-center gap-1 h-full pl-[8px] pr-[6px] rounded-full cursor-pointer"
        >
          {/* 未选择时显示工作台图标；选中后只显示名称，不带图标 */}
          {!current && (
            <span className="w-[18px] h-[18px] rounded-full grid place-items-center shrink-0 bg-[var(--c-surface)] text-[var(--c-text-secondary)]">
              <span className="w-3.5 h-3.5 bg-current" style={maskStyle(iconWorkbench)} aria-hidden="true" />
            </span>
          )}
          <span
            className={`max-w-[5em] truncate text-[length:var(--fs-sm)] font-medium ${
              current ? 'text-[var(--c-text)]' : 'text-[var(--c-text-secondary)]'
            }`}
          >
            {current ? current.label : '工作台'}
          </span>
          {/* 选中后由圆点接管右侧，箭头让位 */}
          {!current && (
            <ChevronDown
              size={12}
              aria-hidden="true"
              className={`shrink-0 text-[var(--c-text-tertiary)] transition-transform duration-150 ${open ? 'rotate-180' : ''}`}
            />
          )}
        </button>

        {current && (
          <>
            <span aria-hidden="true" className="w-px h-3.5 mx-[2px] shrink-0 bg-[var(--c-border)]" />
            <button
              type="button"
              onClick={() => setActiveDomain(null)}
              title="取消选择"
              aria-label="取消选择"
              className="w-[18px] h-[18px] mr-[2px] shrink-0 rounded-full grid place-items-center hover:bg-[color-mix(in_srgb,var(--c-text)_10%,transparent)] transition-colors cursor-pointer"
            >
              <img src={iconClose} alt="" className="w-[18px] h-[18px] object-contain" draggable={false} />
            </button>
          </>
        )}
      </div>

      {open &&
        pos &&
        createPortal(
          <div
            ref={popupRef}
            role="listbox"
            aria-label="切换工作台"
            style={{ left: pos.left, top: pos.top }}
            className="overlay-panel fixed z-[60] w-[272px] rounded-[var(--radius-card)] p-1.5"
          >
            <div className="px-2 pt-1 pb-1 text-[length:var(--fs-xs)] text-[var(--c-text-tertiary)]">切换工作台</div>
            {options.map((option) => {
              const selected = option.key === activeDomain
              return (
                <button
                  key={option.key}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  onClick={() => {
                    setActiveDomain(option.key)
                    setOpen(false)
                  }}
                  className={`w-full flex items-center gap-2.5 rounded-[var(--radius-control)] px-2 py-2 text-left transition-colors duration-150 cursor-pointer ${
                    selected ? 'bg-[var(--c-select-bg)]' : 'hover:bg-[var(--c-hover-bg)]'
                  }`}
                >
                  <span
                    className="w-[30px] h-[30px] rounded-full grid place-items-center shrink-0 text-[length:var(--fs-md)] font-semibold leading-none"
                    style={toneStyle(toneOf(option.initial))}
                    aria-hidden="true"
                  >
                    {option.initial}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[length:var(--fs-sm)] font-medium text-[var(--c-text)]">
                      {option.label}
                    </span>
                    {option.desc && (
                      <span className="block truncate mt-px text-[length:var(--fs-xs)] text-[var(--c-text-tertiary)]">
                        {option.desc}
                      </span>
                    )}
                  </span>
                  <Check
                    size={16}
                    aria-hidden="true"
                    className={`shrink-0 text-[var(--c-accent)] ${selected ? '' : 'opacity-0'}`}
                  />
                </button>
              )
            })}
          </div>,
          document.body,
        )}
    </>
  )
}
