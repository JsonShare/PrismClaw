import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, CheckCircle2, Info, X } from 'lucide-react'

export type ToastType = 'success' | 'error' | 'info'

interface ToastItem {
  id: number
  type: ToastType
  message: string
}

interface ToastContextValue {
  toast: (message: string, type?: ToastType) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used within <ToastProvider>')
  return ctx
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const idRef = useRef(0)
  const timerRef = useRef<Map<number, number>>(new Map())

  const remove = useCallback((id: number) => {
    setItems((s) => s.filter((t) => t.id !== id))
    const handle = timerRef.current.get(id)
    if (handle) {
      window.clearTimeout(handle)
      timerRef.current.delete(id)
    }
  }, [])

  const toast = useCallback(
    (message: string, type: ToastType = 'info') => {
      const id = ++idRef.current
      setItems((s) => [...s, { id, type, message }])
      const handle = window.setTimeout(() => remove(id), type === 'error' ? 4200 : 2600)
      timerRef.current.set(id, handle)
    },
    [remove],
  )

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      {typeof document !== 'undefined'
        ? createPortal(
            <div className="fixed top-4 left-1/2 -translate-x-1/2 z-[9999] flex flex-col items-center gap-2 pointer-events-none">
              {items.map((t) => (
                <ToastCard key={t.id} item={t} onClose={() => remove(t.id)} />
              ))}
            </div>,
            document.body,
          )
        : null}
    </ToastContext.Provider>
  )
}

function ToastCard({ item, onClose }: { item: ToastItem; onClose: () => void }) {
  const isError = item.type === 'error'
  const isSuccess = item.type === 'success'
  const Icon = isError ? AlertTriangle : isSuccess ? CheckCircle2 : Info
  const iconColor = isError
    ? 'text-[var(--c-danger)]'
    : isSuccess
      ? 'text-[var(--c-success)]'
      : 'text-[var(--c-accent)]'
  return (
    <div className="overlay-panel pointer-events-auto flex items-start gap-2.5 max-w-[340px] rounded-[var(--radius-md)] px-3.5 py-2.5 text-[length:var(--fs-sm)] text-[var(--c-text)]">
      <Icon size={16} className={`${iconColor} shrink-0 mt-0.5`} />
      <span className="flex-1 leading-snug">{item.message}</span>
      <button
        type="button"
        onClick={onClose}
        aria-label="关闭提示"
        className="btn btn-icon btn-ghost shrink-0"
      >
        <X size={14} />
      </button>
    </div>
  )
}
