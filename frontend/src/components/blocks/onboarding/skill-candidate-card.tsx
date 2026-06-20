import { Button } from '@/components/ui/button'
import { uz } from '@/lib/uz'
import { CheckIcon, ProposeIcon, RejectIcon } from '@/components/icons/doc-icons'
import type { SkillCandidate } from '@/lib/types'

interface SkillCandidateCardProps {
  candidate: SkillCandidate
  onApprove: () => void
  onReject: () => void
}

// Quiet sibling of DocumentSectionCard: same calm card chrome + proposal chip +
// [Qabul]/[Rad et] row, but a labeled definition list instead of one body, because
// a skill has distinct trigger/action/example fields. Confidence orders the list
// server-side and stays hidden; the human trust signal is the evidence count.
export function SkillCandidateCard({ candidate, onApprove, onReject }: SkillCandidateCardProps) {
  const t = uz.onboarding.documents
  const evidenceCount = candidate.evidence_conv_ids?.length ?? 0

  return (
    <div className="rounded-lg border border-border bg-card px-4 py-3.5">
      <div className="flex items-start justify-between gap-3">
        <span className="text-sm font-semibold text-foreground">{candidate.name}</span>
        <span className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
          <ProposeIcon className="size-3.5" />
          {t.statusProposed}
        </span>
      </div>

      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5">
        <dt className="whitespace-nowrap text-sm text-muted-foreground">{t.skillWhenLabel}</dt>
        <dd className="text-sm text-foreground">{candidate.trigger}</dd>
        <dt className="whitespace-nowrap text-sm text-muted-foreground">{t.skillDoesLabel}</dt>
        <dd className="text-sm text-foreground">{candidate.action}</dd>
        {candidate.example_phrase ? (
          <>
            <dt className="whitespace-nowrap text-sm text-muted-foreground">{t.skillExampleLabel}</dt>
            <dd className="text-sm italic text-foreground">{`“${candidate.example_phrase}”`}</dd>
          </>
        ) : null}
      </dl>

      <div className="mt-3 flex items-center justify-between gap-3">
        <span className="text-xs text-muted-foreground">
          {evidenceCount > 0 ? t.skillEvidence(evidenceCount) : null}
        </span>
        <div className="flex items-center gap-2">
          <Button type="button" size="sm" className="h-8" onClick={onApprove}>
            <CheckIcon className="size-3.5" />
            {t.accept}
          </Button>
          <Button type="button" size="sm" variant="outline" className="h-8" onClick={onReject}>
            <RejectIcon className="size-3.5" />
            {t.reject}
          </Button>
        </div>
      </div>
    </div>
  )
}
