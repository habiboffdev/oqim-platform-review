import { Circle, WarningCircle } from '@phosphor-icons/react'
import { Avatar } from '@/components/primitives/avatar'
import { formatRelativeTime, truncate } from '@/lib/utils'
import { uz } from '@/lib/uz'
import type { CrmPipelineCard } from '@/lib/types'

interface KanbanCardProps {
  card: CrmPipelineCard
  onSelectConversation?: (conversationId: number) => void
}

export function KanbanCard({ card, onSelectConversation }: KanbanCardProps) {
  const hasPendingReply = card.has_pending_reply ?? false
  const needsFollowUp = card.needs_attention
  const stageMeta = stageMetaText(card)
  const products = card.stage.products_interested.slice(0, 2)
  const lastIntent = normalizeIntentLabel(card.stage.last_intent)

  return (
    <div
      onClick={() => onSelectConversation?.(card.conversation_id)}
      className="cursor-pointer rounded-lg border border-border/70 bg-background p-3 transition-colors hover:bg-foreground/[0.03] active:scale-[0.99]"
    >
      {/* Header: avatar + name + indicators */}
      <div className="flex items-start gap-2.5">
        <Avatar name={card.customer_name || '?'} size="sm" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-sm font-medium">
              {card.customer_name || `#${card.conversation_id}`}
            </span>
            {hasPendingReply && (
              <Circle data-testid="pending-reply-indicator" size={7} weight="thin" className="shrink-0 text-info" />
            )}
            {needsFollowUp && (
              <WarningCircle data-testid="follow-up-indicator" size={13} weight="thin" className="shrink-0 text-warning" />
            )}
          </div>
          <p className="mt-0.5 text-[11px] text-muted-foreground line-clamp-1">
            {card.last_message_text
              ? truncate(card.last_message_text, 60)
              : uz.conversations.noMessages}
          </p>
          {stageMeta && (
            <p className="mt-1 text-[10px] text-muted-foreground/80">
              {stageMeta}
            </p>
          )}
          {lastIntent && (
            <p className="mt-1 text-[10px] font-medium text-foreground/75">
              {uz.pipeline.lastIntent(lastIntent)}
            </p>
          )}
        </div>
      </div>

      {/* Footer: time + badges */}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <span className="text-[10px] text-muted-foreground">
          {card.last_message_at ? formatRelativeTime(card.last_message_at) : '—'}
        </span>
        {products.map((product) => (
          <span key={product} className="rounded-md border border-border/70 px-1.5 py-0.5 text-[9px] font-medium text-muted-foreground">
            {truncate(product, 20)}
          </span>
        ))}
        {hasPendingReply && (
          <span className="rounded-md bg-blue-50 px-1.5 py-0.5 text-[9px] font-medium text-blue-700 ring-1 ring-inset ring-blue-100">
            {uz.pipeline.hasReply}
          </span>
        )}
        {needsFollowUp && (
          <span className="rounded-md bg-amber-50 px-1.5 py-0.5 text-[9px] font-medium text-amber-700 ring-1 ring-inset ring-amber-200">
            {uz.pipeline.needsFollowup}
          </span>
        )}
        {card.stage.urgency && !needsFollowUp && (
          <span className="rounded-md bg-amber-50 px-1.5 py-0.5 text-[9px] font-medium text-amber-700 ring-1 ring-inset ring-amber-200">
            {uz.pipeline.urgent}
          </span>
        )}
        {card.unread_count > 0 && (
          <span className="rounded-md bg-primary px-1.5 py-0.5 text-[9px] font-medium text-background">
            {card.unread_count}
          </span>
        )}
      </div>
    </div>
  )
}

function stageMetaText(card: CrmPipelineCard) {
  const stageSource = card.stage.field_provenance?.pipeline_stage || card.stage.source
  const confidenceLabel = typeof card.stage.confidence === 'number'
    ? `${Math.round(card.stage.confidence * 100)}% ishonch`
    : ''
  if (stageSource === 'defaulted') {
    return [uz.pipeline.defaultedStage, confidenceLabel].filter(Boolean).join(' · ')
  }
  if (stageSource === 'crm_state' && !confidenceLabel) return ''
  const sourceLabel = uz.pipeline.stageBy[stageSource] || uz.pipeline.stageBy.defaulted
  return [sourceLabel, confidenceLabel].filter(Boolean).join(' · ')
}

function normalizeIntentLabel(intent: string | null | undefined) {
  const value = String(intent || '').trim()
  if (!value) return ''
  return value.replace(/[_-]+/g, ' ')
}
