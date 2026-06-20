import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { uz } from '@/lib/uz'
import { PageIcon, RefreshIcon, SourceIcon } from '@/components/icons/doc-icons'
import {
  sellerSafeSourceTitle,
  sourceLearningReasonLabel,
  sourceLearningStatusLabel,
} from './copy'
import type {
  OnboardingDocumentSection,
  OnboardingSourceLearningProjection,
} from '@/lib/types'

interface SourceMirrorRailProps {
  sourceLearning: OnboardingSourceLearningProjection | undefined
  selectedSection: OnboardingDocumentSection | null
  onRetry?: () => void
}

export function SourceMirrorRail({
  sourceLearning,
  selectedSection,
  onRetry,
}: SourceMirrorRailProps) {
  const sources = sourceLearning?.sources ?? []
  const conflictCount =
    (sourceLearning?.summary.conflict ?? 0) + (sourceLearning?.summary.needs_review ?? 0)

  return (
    <div className="flex h-full min-h-0 flex-col gap-5 overflow-y-auto rounded-lg border border-border bg-card/40 p-4">
      <RailSection title={uz.onboarding.documents.railReading}>
        {sources.length === 0 ? (
          <RailEmpty>{uz.onboarding.documents.railReadingEmpty}</RailEmpty>
        ) : (
          <ul className="grid gap-2">
            {sources.slice(0, 6).map((source) => (
              <li
                key={source.source_ref}
                className="rounded-md border border-border/70 px-2.5 py-2"
              >
                <div className="flex items-center gap-2">
                  <SourceIcon className="size-3.5 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
                    {sellerSafeSourceTitle(source.label, source.kind)}
                  </span>
                </div>
                <div className="mt-1 flex items-center justify-between gap-2 text-xs text-muted-foreground">
                  <span>{sourceLearningStatusLabel(source.status)}</span>
                  {source.source_unit_count > 0 ? (
                    <span className="tabular-nums">
                      {uz.onboarding.documents.railEvidenceCount(source.source_unit_count)}
                    </span>
                  ) : null}
                </div>
                {source.retryable && onRetry ? (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="mt-1 h-7 px-1.5 text-xs text-muted-foreground hover:text-foreground"
                    onClick={onRetry}
                  >
                    <RefreshIcon className="size-3.5" />
                    {uz.onboarding.retry}
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </RailSection>

      <RailSection title={uz.onboarding.documents.railEvidence}>
        {selectedSection ? (
          selectedSection.evidence_count > 0 ? (
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge variant="outline" className="gap-1">
                <PageIcon className="size-3" />
                {uz.onboarding.documents.railEvidenceCount(selectedSection.evidence_count)}
              </Badge>
            </div>
          ) : (
            <RailEmpty>{uz.onboarding.documents.railEvidenceEmpty}</RailEmpty>
          )
        ) : (
          <RailEmpty>{uz.onboarding.documents.railEvidenceHint}</RailEmpty>
        )}
      </RailSection>

      <RailSection
        title={uz.onboarding.documents.railConflicts}
        badge={conflictCount > 0 ? conflictCount : undefined}
      >
        {conflictCount === 0 ? (
          <RailEmpty>{uz.onboarding.documents.railConflictsEmpty}</RailEmpty>
        ) : (
          <ul className="grid gap-1.5">
            {sources
              .filter((source) => source.degraded_reasons.length > 0)
              .slice(0, 4)
              .map((source) => (
                <li key={`conflict-${source.source_ref}`} className="text-sm text-muted-foreground">
                  · {sourceLearningReasonLabel(source.degraded_reasons[0])}
                </li>
              ))}
          </ul>
        )}
      </RailSection>
    </div>
  )
}

function RailSection({
  title,
  badge,
  children,
}: {
  title: string
  badge?: number
  children: React.ReactNode
}) {
  return (
    <section className="grid gap-2">
      <div className="flex items-center gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {title}
        </h3>
        {badge !== undefined ? (
          <Badge variant="outline" className={cn('tabular-nums')}>
            {badge}
          </Badge>
        ) : null}
      </div>
      {children}
    </section>
  )
}

function RailEmpty({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-muted-foreground/70">{children}</p>
}
