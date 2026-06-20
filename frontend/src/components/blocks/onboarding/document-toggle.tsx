import { cn } from '@/lib/utils'
import { uz } from '@/lib/uz'
import type { OnboardingDocumentsProjection } from '@/lib/types'

export type DocumentTab = 'business' | 'agent' | 'skill'

interface DocumentToggleProps {
  active: DocumentTab
  onChange: (tab: DocumentTab) => void
  projection: OnboardingDocumentsProjection | undefined
  // Live count of proposed skill candidates (DB truth). Overrides the projection's
  // Redis snapshot, which never decrements on review.
  skillCount?: number
}

interface TabSpec {
  key: DocumentTab
  label: string
  count: string
}

function docCount(block: { total: number; approved: number; proposed: number } | undefined): string {
  return uz.onboarding.documents.tabCount(
    (block?.approved ?? 0) + (block?.proposed ?? 0),
    block?.total ?? 0,
  )
}

function buildTabs(
  projection: OnboardingDocumentsProjection | undefined,
  skillCount: number | undefined,
): TabSpec[] {
  const docs = projection?.documents
  const skillTotal = skillCount ?? docs?.skill.candidates ?? 0
  return [
    { key: 'business', label: uz.onboarding.documents.tabBusiness, count: docCount(docs?.business) },
    { key: 'agent', label: uz.onboarding.documents.tabAgent, count: docCount(docs?.agent) },
    { key: 'skill', label: uz.onboarding.documents.tabSkill, count: String(skillTotal) },
  ]
}

export function DocumentToggle({ active, onChange, projection, skillCount }: DocumentToggleProps) {
  const tabs = buildTabs(projection, skillCount)

  return (
    <div className="flex items-center gap-1" role="tablist" aria-orientation="horizontal">
      {tabs.map((tab) => {
        const isActive = tab.key === active
        return (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={isActive}
            onClick={() => onChange(tab.key)}
            className={cn(
              'relative -mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-medium transition-colors',
              isActive
                ? 'border-foreground text-foreground'
                : 'border-transparent text-muted-foreground hover:text-foreground',
            )}
          >
            <span>{tab.label}</span>
            <span
              className={cn(
                'rounded-md px-1.5 py-0.5 text-xs tabular-nums',
                isActive ? 'bg-foreground text-background' : 'bg-muted text-muted-foreground',
              )}
            >
              {tab.count}
            </span>
          </button>
        )
      })}
    </div>
  )
}
