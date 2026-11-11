/**
 * 法律域 · 工作台入口（骨架）
 *
 * 契约：domains.yaml 的 lazy_import 指向 `@domains/legal/LegalWorkbenchPage`，
 * lazy_export 为 `LegalWorkbenchPage` —— 本文件必须保持在分包根目录，不可移入 src/。
 *
 * 当前为最小骨架：仅渲染占位内容，证明域已接入并可见。
 * 后续接入案件管理 / 案件跟踪 / 文书起草时，替换为真实的 LegalWorkbenchApp。
 */

export function LegalWorkbenchPage() {
  return (
    <div className="flex-1 min-h-0 flex flex-col items-center justify-center gap-3 px-6 text-center">
      <h1 className="text-[length:var(--fs-xl,20px)] font-semibold text-[var(--c-text)]">
        智能法律工作台
      </h1>
      <p className="text-[length:var(--fs-sm,13px)] text-[var(--c-text-secondary)]">
        合同审查 · 合规检查
      </p>
      <p className="mt-2 text-[length:var(--fs-xs,12px)] text-[var(--c-text-secondary)]">
        模块骨架已接入，功能建设中
      </p>
    </div>
  )
}

export default LegalWorkbenchPage
