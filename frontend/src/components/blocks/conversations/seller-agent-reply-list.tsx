import { useState } from 'react'
import { Link } from '@tanstack/react-router'
import { CheckCircle, Circle, PaperPlaneTilt, Tray, WarningCircle } from '@phosphor-icons/react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { ChatViewer } from '@/components/blocks/chat/chat-viewer'
import { SellerAgentReplyReviewCard } from '@/components/blocks/seller-agent/seller-agent-reply-review-card'
import { useSellerAgentReplyInbox } from '@/hooks/use-seller-agent-reply-inbox'
import { cn, formatRelativeTime, truncate } from '@/lib/utils'
import { uz } from '@/lib/uz'
import type { SellerAgentReply } from '@/lib/types'

interface SellerAgentReplyListProps {
  search: string
}

function replyText(reply: SellerAgentReply) {
  return reply.split_messages?.length
    ? reply.split_messages.join(' ')
    : reply.final_content || reply.draft_content
}

function learningNeedsAttention(reply: SellerAgentReply) {
  return reply.learning_runtime?.state === 'failed'
}

function learningStateLabel(state: string | undefined) {
  if (state === 'learned') return 'o‘rgangan'
  if (state === 'failed') return 'tekshirish kerak'
  if (state === 'queued') return 'navbatda'
  return 'hali yo‘q'
}

function nextActionLabel(action: string | undefined) {
  if (action === 'retry') return 'qayta urinish'
  if (action === 'wait') return 'kutish'
  if (!action) return 'keyingi ish yo‘q'
  return action.replaceAll('_', ' ')
}

function loadingState() {
  return (
    <div className="grid h-full min-h-0 grid-cols-1 lg:grid-cols-[320px_minmax(0,1fr)]">
      <div className="border-r border-border/60 bg-foreground/[0.02] p-3">
        <div className="mb-3 flex items-center justify-between">
          <Skeleton className="h-5 w-28" />
          <Skeleton className="h-5 w-10" />
        </div>
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, index) => (
            <Skeleton key={index} className="h-24 rounded-lg" />
          ))}
        </div>
      </div>
      <div className="p-6">
        <Skeleton className="h-5 w-44" />
        <Skeleton className="mt-4 h-72 rounded-lg" />
      </div>
    </div>
  )
}

function emptyState() {
  return (
    <div className="flex h-full items-center justify-center px-6">
      <div className="max-w-sm text-center">
        <div className="mx-auto flex size-10 items-center justify-center rounded-lg border border-border/70 bg-background">
          <Tray className="size-5" weight="thin" />
        </div>
        <h2 className="mt-4 text-sm font-semibold tracking-tight">{uz.replies.empty}</h2>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">
          {uz.replies.emptyDescription}
        </p>
      </div>
    </div>
  )
}

function ReplyRailRow({
  reply,
  selected,
  onSelect,
}: {
  reply: SellerAgentReply
  selected: boolean
  onSelect: () => void
}) {
  const runtime = reply.learning_runtime

  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        'w-full border-b border-border/50 px-4 py-3 text-left transition-colors hover:bg-background',
        selected && 'bg-background shadow-sm',
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            {learningNeedsAttention(reply) ? (
              <WarningCircle className="size-4 shrink-0 text-amber-700" weight="thin" />
            ) : (
              <Circle className="size-2 shrink-0 fill-current text-muted-foreground" weight="fill" />
            )}
            <span className="truncate text-sm font-medium">
              {reply.customer_name || `Suhbat #${reply.conversation_id}`}
            </span>
          </div>
          <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">
            {truncate(replyText(reply), 96)}
          </p>
        </div>
        <span className="shrink-0 text-[11px] text-muted-foreground">
          {formatRelativeTime(reply.created_at)}
        </span>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5 pl-6">
        <Badge variant="outline" className="font-normal">
          {Math.round(reply.confidence_score * 100)}%
        </Badge>
        <Badge variant={runtime?.state === 'learned' ? 'success' : runtime?.state === 'failed' ? 'warning' : 'secondary'}>
          {learningStateLabel(runtime?.state)}
        </Badge>
        {reply.status === 'delivery_failed' && (
          <Badge variant="error">yuborish xatosi</Badge>
        )}
        {reply.status === 'delivery_unknown' && (
          <Badge variant="warning">tasdiq kutilmoqda</Badge>
        )}
      </div>
    </button>
  )
}

function ApprovalTimeline({ reply }: { reply: SellerAgentReply }) {
  const runtime = reply.learning_runtime
  const events = [
    {
      label: 'Mijoz xabari javob taklifini yaratdi',
      detail: reply.trigger_message_text || 'Javob nima uchun chiqqani hali ko‘rsatilmagan',
      time: reply.created_at,
      tone: 'muted',
    },
    {
      label: 'AI javob taklifini tayyorladi',
      detail: `${Math.round(reply.confidence_score * 100)}% ishonch`,
      time: reply.created_at,
      tone: 'success',
    },
    {
      label: runtime?.state === 'learned' ? "O'rganish tasdiqlandi" : "O'rganish holati",
      detail: `${learningStateLabel(runtime?.state)} · ${nextActionLabel(runtime?.next_action)}`,
      time: reply.created_at,
      tone: runtime?.state === 'failed' ? 'warning' : runtime?.state === 'learned' ? 'success' : 'muted',
    },
  ]

  return (
    <div className="rounded-lg border border-border/60 bg-background/40 p-4">
      <div className="text-[11px] font-medium uppercase text-muted-foreground">
        Tasdiqlash tarixi
      </div>
      <ol className="relative mt-3">
        <span aria-hidden className="absolute bottom-1 left-[15px] top-1 w-px bg-border/50" />
        {events.map((event) => (
          <li key={event.label} className="relative grid grid-cols-[32px_minmax(0,1fr)] gap-3 py-2">
            <span
              className={cn(
                'z-10 grid size-8 place-items-center rounded-full bg-background ring-4 ring-background',
                event.tone === 'success'
                  ? 'text-emerald-600'
                  : event.tone === 'warning'
                    ? 'text-amber-700'
                    : 'text-muted-foreground',
              )}
            >
              {event.tone === 'success' ? (
                <CheckCircle className="size-4" weight="thin" />
              ) : event.tone === 'warning' ? (
                <WarningCircle className="size-4" weight="thin" />
              ) : (
                <Circle className="size-3" weight="fill" />
              )}
            </span>
            <div className="min-w-0">
              <div className="text-sm leading-5">{event.label}</div>
              <div className="mt-1 line-clamp-2 text-xs text-muted-foreground">{event.detail}</div>
              <div className="mt-1 text-[10px] text-muted-foreground">
                {formatRelativeTime(event.time)}
              </div>
            </div>
          </li>
        ))}
      </ol>
    </div>
  )
}

export function SellerAgentReplyList({ search }: SellerAgentReplyListProps) {
  const { data: replies, isLoading, isError, refetch } = useSellerAgentReplyInbox()
  const [selectedId, setSelectedId] = useState<number | null>(null)

  const filtered = replies
    ?.filter((reply) =>
      !search ||
      replyText(reply).toLowerCase().includes(search.toLowerCase()) ||
      reply.customer_name?.toLowerCase().includes(search.toLowerCase()) ||
      reply.trigger_message_text?.toLowerCase().includes(search.toLowerCase()),
    )
    .sort((a, b) => b.confidence_score - a.confidence_score)

  const selected = filtered?.find((reply) => reply.id === selectedId) ?? filtered?.[0] ?? null

  if (isLoading) return loadingState()

  if (isError) {
    return (
      <div className="p-6">
        <Alert variant="destructive">
          <AlertTitle>{uz.common.error}</AlertTitle>
          <AlertDescription>
            Javoblar hozir yuklanmadi. Bir ozdan keyin qayta urinib ko‘ring.
          </AlertDescription>
        </Alert>
        <Button size="sm" variant="outline" onClick={() => refetch()} className="mt-3">
          Qayta urinish
        </Button>
      </div>
    )
  }

  if (!filtered?.length) return emptyState()

  return (
    <div className="grid h-full min-h-0 grid-cols-1 bg-background lg:grid-cols-[320px_minmax(0,1fr)]">
      <aside className="flex min-h-0 flex-col border-r border-border/60 bg-foreground/[0.02]">
        <div className="border-b border-border/60 px-4 py-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-[11px] font-medium text-muted-foreground">Tasdiqlash navbati</p>
              <h2 className="text-lg font-semibold tracking-tight">{uz.replies.title}</h2>
            </div>
            <Badge variant="outline">{filtered.length}</Badge>
          </div>
          <div className="mt-3 rounded-md border border-border/60 bg-background px-3 py-2 text-xs text-muted-foreground">
            Tasdiqlangan va tahrirlangan javoblar keyingi takliflarni yaxshilaydi.
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {filtered.map((reply) => (
            <ReplyRailRow
              key={reply.id}
              reply={reply}
              selected={selected?.id === reply.id}
              onSelect={() => setSelectedId(reply.id)}
            />
          ))}
        </div>
      </aside>

      <section className="min-w-0 overflow-y-auto px-4 py-4 lg:px-6 lg:py-6">
        {selected && (
          <div className="grid w-full gap-5 2xl:grid-cols-[minmax(520px,1fr)_340px]">
            <div className="min-w-0 space-y-5">
              <SellerAgentReplyReviewCard reply={selected} selected />
              <div className="overflow-hidden rounded-lg border border-border/60">
                <div className="flex items-center justify-between border-b border-border/60 px-4 py-3">
                  <div>
                    <p className="text-[11px] font-medium text-muted-foreground">Suhbat konteksti</p>
                    <h3 className="text-sm font-semibold">{selected.customer_name || `Suhbat #${selected.conversation_id}`}</h3>
                  </div>
                  <Link
                    to="/conversations/$conversationId"
                    params={{ conversationId: String(selected.conversation_id) }}
                    className="inline-flex h-8 shrink-0 items-center justify-center gap-1 rounded-lg border border-border bg-input/30 px-3 text-sm font-medium transition-all hover:bg-input/50 hover:text-foreground"
                  >
                    <PaperPlaneTilt className="size-3.5" weight="thin" />
                    Chatga o'tish
                  </Link>
                </div>
                <div className="h-[420px]">
                  <ChatViewer conversationId={selected.conversation_id} />
                </div>
              </div>
            </div>
            <ApprovalTimeline reply={selected} />
          </div>
        )}
      </section>
    </div>
  )
}
