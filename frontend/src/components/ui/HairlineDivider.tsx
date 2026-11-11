/**
 * 全应用统一分割线：渐隐发丝线 + 细微高光（原 SidebarNav.DomainDivider 样式）。
 *
 * 两层结构：主线用 --c-border 且左右各 16% 渐隐，其上 1px 用 --c-surface 做
 * 高光，让线在玻璃/纯色背景上都有"刻进去"的细腻感。侧边栏分组、弹出菜单、
 * 选择面板、设置页分组一律用它，不再出现实线 border-t / 纯色 h-px 混用。
 *
 * 水平边距与纵向间距由调用方通过 className 传入（默认 mx-3 my-3，即原侧边栏
 * 分组分隔的间距）；在 flex 容器内请追加 shrink-0 防止被压缩。
 */
export function HairlineDivider({ className = 'mx-3 my-3' }: { className?: string }) {
  return (
    <div role="separator" className={`relative h-px ${className}`}>
      <div className="absolute inset-0 bg-[linear-gradient(90deg,transparent_0%,var(--c-border)_16%,var(--c-border)_84%,transparent_100%)]" />
      <div className="absolute inset-x-0 top-px h-px bg-[linear-gradient(90deg,transparent_0%,var(--c-surface)_16%,var(--c-surface)_84%,transparent_100%)] opacity-60" />
    </div>
  )
}
