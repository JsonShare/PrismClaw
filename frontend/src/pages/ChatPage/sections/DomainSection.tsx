import { Suspense } from 'react'
import { getRegisteredDomains } from '../../../domains/registry'
import { Loader2 } from 'lucide-react'

interface DomainSectionProps {
  domainKey: string
}

function DomainLoadingFallback() {
  return (
    <div className="flex-1 flex items-center justify-center">
      <Loader2 size={24} className="animate-spin text-[var(--c-text-secondary)]" />
    </div>
  )
}

export function DomainSection({ domainKey }: DomainSectionProps) {
  const entry = getRegisteredDomains().find((d) => d.manifest.key === domainKey)
  if (!entry) return null
  const WorkbenchPage = entry.WorkbenchPage
  return (
    <div className="flex-1 min-w-0 min-h-0 flex flex-col relative app-panel overflow-hidden">
      <Suspense fallback={<DomainLoadingFallback />}>
        <WorkbenchPage />
      </Suspense>
    </div>
  )
}
