import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Markdown } from '@/components/primitives/markdown'
import { cn } from '@/lib/utils'
import { uz } from '@/lib/uz'
import { CheckIcon, EditIcon, ProposeIcon, RejectIcon } from '@/components/icons/doc-icons'
import type { OnboardingDocumentSection } from '@/lib/types'

export type SectionDisplayStatus =
  | 'pending'
  | 'generating'
  | 'proposed'
  | 'approved'
  | 'skipped'

interface DocumentSectionCardProps {
  section: OnboardingDocumentSection
  displayStatus: SectionDisplayStatus
  selected: boolean
  onSelect: () => void
  onAccept: () => void
  onReject: () => void
  onEdit: () => void
}

export function DocumentSectionCard({
  section,
  displayStatus,
  selected,
  onSelect,
  onAccept,
  onReject,
  onEdit,
}: DocumentSectionCardProps) {
  if (displayStatus === 'skipped') {
    return (
      <div className="rounded-lg border border-dashed border-border/70 px-4 py-2.5 text-sm text-muted-foreground">
        <span className="font-medium text-foreground/70">{section.title}</span>
        <span className="ml-2">{uz.onboarding.documents.statusSkipped}</span>
      </div>
    )
  }

  if (displayStatus === 'pending') {
    return (
      <div className="rounded-lg border border-dashed border-border/70 px-4 py-3 text-sm text-muted-foreground/70">
        <div className="flex items-center justify-between gap-3">
          <span>{section.title}</span>
          <span className="text-xs">{uz.onboarding.documents.statusPending}</span>
        </div>
      </div>
    )
  }

  if (displayStatus === 'generating') {
    return (
      <div className="rounded-lg border border-border bg-card px-4 py-3.5">
        <div className="flex items-center justify-between gap-3">
          <span className="text-sm font-semibold text-foreground">{section.title}</span>
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span className="size-1.5 animate-pulse rounded-full bg-foreground/40" />
            {uz.onboarding.documents.statusGenerating}
          </span>
        </div>
        <div className="mt-3 grid gap-2">
          <Skeleton className="h-3 w-full animate-pulse rounded-md" />
          <Skeleton className="h-3 w-[88%] animate-pulse rounded-md" />
          <Skeleton className="h-3 w-[64%] animate-pulse rounded-md" />
        </div>
      </div>
    )
  }

  const isProposed = displayStatus === 'proposed'

  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        'w-full rounded-lg border bg-card px-4 py-3.5 text-left transition-colors',
        selected ? 'border-foreground' : 'border-border hover:border-foreground/40',
      )}
    >
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm font-semibold text-foreground">{section.title}</span>
        <span className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
          {isProposed ? (
            <ProposeIcon className="size-3.5" />
          ) : (
            <CheckIcon className="size-3.5 text-foreground" />
          )}
          {isProposed
            ? uz.onboarding.documents.statusProposed
            : uz.onboarding.documents.statusApproved}
        </span>
      </div>

      {section.body ? <Markdown content={section.body} className="mt-2" /> : null}
      {section.evidence_count > 0 ? (
        <p className="mt-1.5 text-xs text-muted-foreground">
          {uz.onboarding.documents.railEvidenceCount(section.evidence_count)}
        </p>
      ) : null}

      <div className="mt-3 flex items-center gap-2">
        {isProposed ? (
          <>
            <Button
              type="button"
              size="sm"
              className="h-8"
              onClick={(event) => {
                event.stopPropagation()
                onAccept()
              }}
            >
              <CheckIcon className="size-3.5" />
              {uz.onboarding.documents.accept}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-8"
              onClick={(event) => {
                event.stopPropagation()
                onReject()
              }}
            >
              <RejectIcon className="size-3.5" />
              {uz.onboarding.documents.reject}
            </Button>
          </>
        ) : null}
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-8 text-muted-foreground hover:text-foreground"
          onClick={(event) => {
            event.stopPropagation()
            onEdit()
          }}
        >
          <EditIcon className="size-3.5" />
          {uz.onboarding.documents.edit}
        </Button>
      </div>
    </button>
  )
}
