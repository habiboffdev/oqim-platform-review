import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { OnboardingLearnedReviewItem } from '@/lib/types'
import { uz } from '@/lib/uz'
import { learnedFactText, reviewEvidenceLabel, reviewEvidenceMeta, reviewFactTitle } from './copy'
import type { LearnedReviewActionInput } from './types'

export function FactReviewList({
  items,
  disabled,
  onReviewAction,
}: {
  items: OnboardingLearnedReviewItem[]
  disabled: boolean
  onReviewAction: (input: LearnedReviewActionInput) => void
}) {
  if (items.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
        Bu bo‘lim uchun hali manba qo‘shilmagan.
      </div>
    )
  }

  return (
    <div className="grid divide-y rounded-lg border border-border">
      {items.slice(0, 5).map((item) => (
        <div key={item.fact_id} className="grid gap-3 px-4 py-4">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <p className="font-medium">{reviewFactTitle(item)}</p>
              <p className="mt-1 line-clamp-2 text-sm leading-6 text-muted-foreground">
                {learnedFactText(item) || uz.onboarding.learnedReview.needsReview}
              </p>
            </div>
            <Badge variant="outline">{Math.round(item.confidence * 100)}%</Badge>
          </div>
          <EvidenceList item={item} />
          <div className="flex items-center gap-2">
            <Button
              type="button"
              size="xs"
              disabled={disabled}
              onClick={() => onReviewAction({
                action: 'approve',
                targetType: 'fact',
                targetRef: item.fact_id,
              })}
            >
              Tasdiqlash
            </Button>
            <Button
              type="button"
              size="xs"
              variant="ghost"
              disabled={disabled}
              onClick={() => onReviewAction({
                action: 'reject',
                targetType: 'fact',
                targetRef: item.fact_id,
              })}
            >
              Rad etish
            </Button>
          </div>
        </div>
      ))}
    </div>
  )
}

function EvidenceList({ item }: { item: OnboardingLearnedReviewItem }) {
  const evidence = item.source_evidence ?? []
  if (evidence.length === 0) {
    if (item.source_refs.length === 0) return null
    return (
      <div className="flex flex-wrap gap-1.5">
        <Badge variant="outline">{item.source_refs.length} dalil</Badge>
      </div>
    )
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {evidence.slice(0, 3).map((source) => (
        <Badge key={`${source.ref}:${source.unit_label ?? ''}`} variant="secondary" className="max-w-60 truncate">
          {reviewEvidenceLabel(source)}{reviewEvidenceMeta(source) ? ` · ${reviewEvidenceMeta(source)}` : ''}
        </Badge>
      ))}
      {evidence.length > 3 ? (
        <Badge variant="outline">+{evidence.length - 3}</Badge>
      ) : null}
    </div>
  )
}
