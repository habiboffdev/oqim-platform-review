// eslint-disable-next-line no-restricted-imports -- IntersectionObserver requires useEffect with deps
import { useRef, useEffect } from 'react'
import { Link } from '@tanstack/react-router'
import {
  ChatCircleText,
  Circle,
  Sparkle,
  WarningCircle,
} from '@phosphor-icons/react'
import { GREEN_CONFIDENCE_THRESHOLD, YELLOW_CONFIDENCE_THRESHOLD } from '@/lib/constants'
import { cn, formatRelativeTime, truncate } from '@/lib/utils'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { uz } from '@/lib/uz'
import type { Conversation } from '@/lib/types'

interface ConversationListProps {
  conversations: Conversation[]
  activeId?: number
  isLoading: boolean
  error: Error | null
  hasNextPage?: boolean
  isFetchingNextPage?: boolean
  fetchNextPage?: () => void
  onConversationClick?: (id: number) => void
  ghostCount?: number
  onGhostViewAll?: () => void
}

function useScrollSentinel(
  fetchNextPage: (() => void) | undefined,
  hasNextPage: boolean | undefined,
  isFetchingNextPage: boolean | undefined,
  rootRef: React.RefObject<HTMLDivElement | null>,
) {
  const sentinelRef = useRef<HTMLDivElement>(null)

  // eslint-disable-next-line react-hooks/exhaustive-deps -- IntersectionObserver lifecycle needs these deps
  useEffect(() => {
    const el = sentinelRef.current
    const root = rootRef.current
    if (!el || !fetchNextPage || !root) return

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && hasNextPage && !isFetchingNextPage) {
          fetchNextPage()
        }
      },
      { root, rootMargin: '200px 0px' },
    )

    observer.observe(el)
    return () => observer.disconnect()
  }, [fetchNextPage, hasNextPage, isFetchingNextPage, rootRef])

  return sentinelRef
}

function initials(name: string) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join('') || 'O'
}

function confidenceClass(confidence: number) {
  if (confidence >= GREEN_CONFIDENCE_THRESHOLD) return 'bg-confidence-green'
  if (confidence >= YELLOW_CONFIDENCE_THRESHOLD) return 'bg-confidence-yellow'
  return 'bg-confidence-red'
}

function channelLabel(channel: string | null | undefined) {
  const normalized = (channel || '').trim().toLowerCase()
  if (normalized === 'telegram_dm' || normalized === 'telegram' || normalized === 'dm') {
    return 'Telegram'
  }
  if (normalized === 'instagram_dm' || normalized === 'instagram') return 'Instagram'
  if (normalized === 'whatsapp_dm' || normalized === 'whatsapp') return 'WhatsApp'
  return normalized || 'Telegram'
}

function LoadingRows() {
  return (
    <div className="divide-y divide-border/50">
      {Array.from({ length: 7 }).map((_, i) => (
        <div key={i} className="flex gap-3 px-4 py-3">
          <Skeleton className="size-9 shrink-0 rounded-full" />
          <div className="min-w-0 flex-1 space-y-2">
            <div className="flex items-center justify-between gap-3">
              <Skeleton className="h-3 w-28" />
              <Skeleton className="h-3 w-10" />
            </div>
            <Skeleton className={cn('h-3', i % 2 === 0 ? 'w-48' : 'w-36')} />
          </div>
        </div>
      ))}
    </div>
  )
}

function InboxZero() {
  return (
    <div className="flex h-full items-center justify-center px-6">
      <div className="max-w-xs text-center">
        <div className="mx-auto flex size-10 items-center justify-center rounded-lg border border-border/70 bg-background">
          <ChatCircleText className="size-5" weight="thin" />
        </div>
        <h2 className="mt-4 text-sm font-semibold tracking-tight">
          {uz.conversations.empty}
        </h2>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">
          {uz.conversations.emptyDescription}
        </p>
      </div>
    </div>
  )
}

function ConversationRow({
  conversation,
  active,
  onClick,
}: {
  conversation: Conversation
  active: boolean
  onClick?: (id: number) => void
}) {
  const replyConfidence = conversation.latest_reply_confidence
  const preview = conversation.last_message_text
    ? truncate(conversation.last_message_text, 72)
    : uz.conversations.noMessages
  const crmStage = conversation.crm_stage?.stage
  const stageLabel = crmStage
    ? (uz.pipeline.stages as Record<string, string>)[crmStage] || crmStage
    : null

  return (
    <Link
      to="/conversations/$conversationId"
      params={{ conversationId: String(conversation.id) }}
      onClick={() => onClick?.(conversation.id)}
      className={cn(
        'group flex gap-3 border-b border-border/50 px-4 py-3 transition-colors hover:bg-background',
        active && 'bg-background shadow-sm',
      )}
    >
      <div className="relative shrink-0">
        <Avatar size="lg">
          <AvatarFallback>{initials(conversation.customer_name)}</AvatarFallback>
        </Avatar>
        {conversation.unread_count > 0 && (
          <span className="absolute -right-0.5 -top-0.5 size-2.5 rounded-full border-2 border-background bg-foreground" />
        )}
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-sm font-medium">{conversation.customer_name}</span>
          <span className="shrink-0 text-xs text-muted-foreground">
            {formatRelativeTime(conversation.last_message_at)}
          </span>
        </div>

        <div className="mt-1 flex items-start justify-between gap-2">
          <p className="min-w-0 truncate text-xs leading-5 text-muted-foreground">
            {preview}
          </p>
          <div className="flex shrink-0 items-center gap-1.5">
            {replyConfidence != null && (
              <span
                className={cn('size-2 rounded-full', confidenceClass(replyConfidence))}
                aria-label={uz.conversations.confidenceLabel}
              />
            )}
            {conversation.has_pending_reply && (
              <Sparkle className="size-3.5 text-muted-foreground" weight="thin" />
            )}
            {conversation.unread_count > 0 && (
              <Badge className="h-5 min-w-5 justify-center px-1.5 text-[11px]">
                {conversation.unread_count}
              </Badge>
            )}
          </div>
        </div>

        <div className="mt-2 flex items-center gap-2 text-[11px] text-muted-foreground">
          <span>{channelLabel(conversation.channel)}</span>
          {stageLabel && (
            <>
              <Circle className="size-1 fill-current" weight="fill" />
              <span>{stageLabel}</span>
            </>
          )}
          {conversation.needs_attention && (
            <>
              <Circle className="size-1 fill-current" weight="fill" />
              <span className="inline-flex items-center gap-1 text-amber-700">
                <WarningCircle className="size-3" weight="thin" />
                E'tibor kerak
              </span>
            </>
          )}
        </div>
      </div>
    </Link>
  )
}

export function ConversationList({
  conversations,
  activeId,
  isLoading,
  error,
  hasNextPage,
  isFetchingNextPage,
  fetchNextPage,
  onConversationClick,
}: ConversationListProps) {
  const scrollRootRef = useRef<HTMLDivElement>(null)
  const sentinelRef = useScrollSentinel(fetchNextPage, hasNextPage, isFetchingNextPage, scrollRootRef)

  if (error) {
    return (
      <div className="flex-1 px-4 py-6">
        <Alert variant="destructive">
          <AlertTitle>{uz.common.error}</AlertTitle>
          <AlertDescription>
            Suhbatlar yuklanmadi. Oxirgi xabar va o‘qilmaganlar hozircha ko‘rinmaydi.
          </AlertDescription>
        </Alert>
      </div>
    )
  }

  if (isLoading) {
    return (
      <div className="h-full overflow-y-auto">
        <LoadingRows />
      </div>
    )
  }

  if (conversations.length === 0) {
    return <InboxZero />
  }

  return (
    <div ref={scrollRootRef} className="h-full min-h-0 flex-1 overflow-y-auto">
      <div className="divide-y divide-border/50">
        {conversations.map((conversation) => (
          <ConversationRow
            key={conversation.id}
            conversation={conversation}
            active={activeId === conversation.id}
            onClick={onConversationClick}
          />
        ))}
      </div>

      {hasNextPage && (
        <div ref={sentinelRef} className="px-4 py-3">
          {isFetchingNextPage && <LoadingRows />}
        </div>
      )}
    </div>
  )
}
