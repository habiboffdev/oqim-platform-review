import { useState } from 'react'
import { useLocation, useNavigate, useParams, useSearch } from '@tanstack/react-router'
import {
  ChatCircle,
  Circle,
  Columns,
  MagnifyingGlass,
  NotePencil,
  Tray,
  WarningCircle,
} from '@phosphor-icons/react'
import { ConversationList } from '@/components/blocks/conversation-list'
import { ChatViewer } from '@/components/blocks/chat/chat-viewer'
import { ComposeBox } from '@/components/blocks/chat/compose-box'
import { SellerAgentSurface } from '@/components/blocks/seller-agent/seller-agent-surface'
import { SellerAgentReplyList } from '@/components/blocks/conversations/seller-agent-reply-list'
import { KanbanBoard } from '@/components/blocks/pipeline/kanban-board'
import { useConversation, useInfiniteConversations } from '@/hooks/use-conversations'
import { useSellerAgentReplyInbox } from '@/hooks/use-seller-agent-reply-inbox'
import { usePipeline } from '@/hooks/use-pipeline'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { cn, formatRelativeTime } from '@/lib/utils'
import { uz } from '@/lib/uz'
import { activeConversationIdFromRoute } from '@/lib/active-conversation-route'
import type { Conversation, CrmPipelineProjection } from '@/lib/types'

type ConversationMode = 'pipeline' | 'conversations' | 'replies'
type InboxView = 'all' | 'unread' | 'ready'

const modeTabs: {
  key: ConversationMode
  label: string
  description: string
  icon: typeof Columns
}[] = [
  {
    key: 'pipeline',
    label: uz.workspaceUi.conversations.pipeline,
    description: uz.workspaceUi.conversations.pipelineDescription,
    icon: Columns,
  },
  {
    key: 'conversations',
    label: uz.workspaceUi.conversations.conversations,
    description: uz.workspaceUi.conversations.conversationsDescription,
    icon: ChatCircle,
  },
  {
    key: 'replies',
    label: uz.workspaceUi.conversations.replies,
    description: uz.workspaceUi.conversations.replyDescription,
    icon: NotePencil,
  },
]

function normalizeMode(value: unknown): ConversationMode {
  if (value === 'pipeline' || value === 'replies' || value === 'conversations') return value
  return 'conversations'
}

function uniqueConversationsById(conversations: Conversation[]) {
  const seen = new Set<number>()
  return conversations.filter((conversation) => {
    if (seen.has(conversation.id)) return false
    seen.add(conversation.id)
    return true
  })
}

function initials(name: string) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join('') || 'O'
}

function stageLabel(conversation: Conversation | null | undefined) {
  const stage = conversation?.crm_stage?.stage
  if (!stage) return "Noma'lum"
  return (uz.pipeline.stages as Record<string, string>)[stage] || stage
}

function contactLabel(conversation: Conversation | null | undefined) {
  const contactType = conversation?.contact_type
  if (!contactType) return channelLabel(conversation?.channel)
  return (uz.customer.types as Record<string, string>)[contactType] || contactType
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

function pipelineSummary(pipeline: CrmPipelineProjection) {
  const cards = pipeline.stages.flatMap((stage) => stage.cards)
  const understood = cards.filter((card) => (
    card.stage.source !== 'defaulted'
    || card.stage.stage !== 'new'
    || typeof card.stage.confidence === 'number'
  )).length
  const reviewNeeded = cards.filter((card) => (
    card.stage.stage === 'manual_review'
    || card.needs_attention
    || (typeof card.stage.confidence === 'number' && card.stage.confidence < 0.65)
  )).length

  return {
    understood,
    reviewNeeded,
    total: pipeline.total,
    recentWindow: Math.min(pipeline.total, 50),
  }
}

function ThreadHeader({ conversation }: { conversation: Conversation }) {
  const crmStage = conversation.crm_stage

  return (
    <header className="flex h-16 shrink-0 items-center justify-between gap-4 border-b border-border/60 bg-background px-5">
      <div className="flex min-w-0 items-center gap-3">
        <Avatar size="lg">
          <AvatarFallback>{initials(conversation.customer_name)}</AvatarFallback>
        </Avatar>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h1 className="truncate text-sm font-semibold tracking-tight">
              {conversation.customer_name}
            </h1>
            {conversation.needs_attention && (
              <WarningCircle className="size-4 shrink-0 text-warning-foreground" weight="thin" />
            )}
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span>{contactLabel(conversation)}</span>
            <Circle className="size-2" weight="fill" />
            <span>{stageLabel(conversation)}</span>
            {conversation.last_message_at && (
              <>
                <Circle className="size-2" weight="fill" />
                <span>{formatRelativeTime(conversation.last_message_at)}</span>
              </>
            )}
          </div>
        </div>
      </div>
      <div className="hidden items-center gap-2 lg:flex">
        {crmStage?.confidence != null && (
          <Badge variant="outline" className="font-normal">
            {Math.round(crmStage.confidence * 100)}% ishonch
          </Badge>
        )}
        {conversation.has_pending_reply && (
          <Badge variant="info">{uz.conversations.replyIndicator}</Badge>
        )}
      </div>
    </header>
  )
}

function EmptyThread() {
  return (
    <div className="flex h-full items-center justify-center px-6">
      <div className="max-w-sm text-center">
        <div className="mx-auto flex size-10 items-center justify-center rounded-lg border border-border/70 bg-background">
          <ChatCircle className="size-5" weight="thin" />
        </div>
        <h2 className="mt-4 text-sm font-semibold tracking-tight">
          {uz.conversations.selectChat}
        </h2>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">
          Suhbat tanlang. O‘rta panelda yozishma, o‘ng tomonda esa AI sotuvchi qarori ko‘rinadi.
        </p>
      </div>
    </div>
  )
}

function PipelineWorkspace({
  pipeline,
  isLoading,
  isError,
  onSelectConversation,
}: {
  pipeline: CrmPipelineProjection | undefined
  isLoading: boolean
  isError: boolean
  onSelectConversation: (conversationId: number) => void
}) {
  if (isLoading) {
    return (
      <div className="space-y-4 p-6">
        <Skeleton className="h-9 w-56" />
        <div className="grid grid-cols-3 gap-3">
          {Array.from({ length: 6 }).map((_, index) => (
            <Skeleton key={index} className="h-72 rounded-xl" />
          ))}
        </div>
      </div>
    )
  }

  if (isError || !pipeline) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <div className="max-w-sm rounded-xl border border-border/60 bg-background p-5 text-center">
          <WarningCircle className="mx-auto size-6 text-warning-foreground" weight="thin" />
          <h2 className="mt-3 text-sm font-semibold">Mijoz bosqichlari yuklanmadi</h2>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            Bosqichlar tayyor bo‘lishi uchun suhbatlar va mijoz holati sinxron bo‘lishi kerak.
          </p>
        </div>
      </div>
    )
  }
  const activeColumns = pipeline.stages.filter((stage) => stage.count > 0)
  const onlyNewStage = activeColumns.length === 1 && activeColumns[0]?.stage === 'new'
  const summary = pipelineSummary(pipeline)

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex h-16 shrink-0 items-center justify-between gap-4 border-b border-border/60 px-5">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground">
            Mijozlar yo‘li
          </p>
          <h1 className="mt-1 text-lg font-semibold tracking-tight">Mijoz bosqichlari</h1>
        </div>
        <Badge variant="outline" className="rounded-md">
          {pipeline.total} suhbat
        </Badge>
      </header>
      <section className="border-b border-border/60 bg-foreground/[0.015] px-5 py-4">
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.5fr)_minmax(420px,1fr)] xl:items-start">
          <div className="min-w-0">
            <p className="text-sm font-semibold tracking-tight">{uz.pipeline.intelligenceTitle}</p>
            <p className="mt-1 max-w-3xl text-sm leading-6 text-muted-foreground">
              {onlyNewStage ? uz.pipeline.allNewLearning : uz.pipeline.intelligenceDescription}
            </p>
            <p className="mt-2 text-xs leading-5 text-muted-foreground">
              {uz.pipeline.latestFocus}
            </p>
          </div>
          <div className="grid min-w-0 grid-cols-3 overflow-hidden rounded-lg border border-border/60 bg-background text-sm">
            <div className="min-w-0 p-3">
              <p className="truncate text-[11px] text-muted-foreground">Tushunildi</p>
              <p className="mt-1 truncate font-medium">
                {uz.pipeline.classifiedCount(summary.understood, summary.total)}
              </p>
            </div>
            <div className="min-w-0 border-l border-border/60 p-3">
              <p className="truncate text-[11px] text-muted-foreground">Ko‘rib chiqish</p>
              <p className="mt-1 truncate font-medium">{uz.pipeline.reviewCount(summary.reviewNeeded)}</p>
            </div>
            <div className="min-w-0 border-l border-border/60 p-3">
              <p className="truncate text-[11px] text-muted-foreground">Hozir</p>
              <p className="mt-1 truncate font-medium">{summary.recentWindow} mijoz</p>
            </div>
          </div>
        </div>
      </section>
      <div className="min-h-0 flex-1 overflow-hidden py-4">
        <KanbanBoard pipeline={pipeline} onSelectConversation={onSelectConversation} />
      </div>
    </div>
  )
}

export function ConversationsPage() {
  const [search, setSearch] = useState('')
  const [view, setView] = useState<InboxView>('all')
  const searchParams = useSearch({ strict: false }) as { mode?: string }
  const mode = normalizeMode(searchParams.mode)
  const navigate = useNavigate()
  const params = useParams({ strict: false }) as { conversationId?: string }
  const location = useLocation()
  const activeConversationId = activeConversationIdFromRoute({
    pathname: location.pathname,
    param: params.conversationId,
  }) ?? null

  const {
    data,
    isLoading,
    error,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
  } = useInfiniteConversations()
  const {
    data: activeConversationDetail,
    error: activeConversationError,
  } = useConversation(activeConversationId ?? undefined)
  const replies = useSellerAgentReplyInbox()
  const pipeline = usePipeline()
  const conversations = uniqueConversationsById(data?.pages.flatMap((page) => page.items) ?? [])

  const activeConvoFromList = conversations.find((conversation) => conversation.id === activeConversationId)
  const activeConvo = activeConversationError
    ? null
    : activeConversationDetail ?? activeConvoFromList
  const activeConversationKey = activeConvo?.id

  const filtered = conversations.filter((conversation) => {
    const matchesSearch = !search || conversation.customer_name.toLowerCase().includes(search.toLowerCase())
    const matchesView =
      view === 'all'
      || (view === 'unread' && conversation.unread_count > 0)
      || (view === 'ready' && conversation.has_pending_reply)
    return matchesSearch && matchesView
  })

  const inboxViews: { key: InboxView; label: string }[] = [
    { key: 'all', label: uz.common.all },
    { key: 'unread', label: 'O‘qilmagan' },
    { key: 'ready', label: 'Javob tayyor' },
  ]

  const setMode = (nextMode: ConversationMode) => {
    navigate({
      to: '/conversations',
      search: nextMode === 'conversations' ? {} : { mode: nextMode },
      replace: true,
    })
  }

  const openConversation = (conversationId: number) => {
    navigate({
      to: '/conversations/$conversationId',
      params: { conversationId: String(conversationId) },
    })
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-background text-foreground">
      <header className="shrink-0 border-b border-border/60 bg-background/95 px-5 py-4">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <div className="flex size-8 shrink-0 items-center justify-center rounded-md border border-border/70 bg-background">
                <Tray className="size-4" weight="thin" />
              </div>
              <div className="min-w-0">
                <p className="font-mono text-[10px] uppercase tracking-[0.3em] text-muted-foreground">
                  {uz.workspaceUi.conversations.eyebrow}
                </p>
                <h1 className="truncate font-heading text-2xl">{uz.workspaceUi.conversations.title}</h1>
              </div>
              <Badge variant="outline" className="ml-1 rounded-md">
                {mode === 'pipeline'
                  ? pipeline.data?.total ?? 0
                  : mode === 'replies'
                    ? replies.data?.length ?? 0
                    : filtered.length}
              </Badge>
            </div>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
              {modeTabs.find((tab) => tab.key === mode)?.description}
            </p>
          </div>

          <div className="flex flex-col gap-3 xl:min-w-[560px]">
            <div className="grid grid-cols-3 gap-1 rounded-lg border border-border/60 bg-background p-1">
              {modeTabs.map((tab) => {
                const active = mode === tab.key
                return (
                  <button
                    key={tab.key}
                    type="button"
                    onClick={() => setMode(tab.key)}
                    className={cn(
                      'flex min-w-0 items-center justify-center gap-2 rounded-md px-3 py-2 text-center text-sm transition-colors',
                      active
                        ? 'bg-foreground text-background'
                        : 'text-muted-foreground hover:bg-foreground/[0.04] hover:text-foreground',
                    )}
                  >
                    <tab.icon className="size-4 shrink-0" weight="thin" />
                    <span className="truncate font-medium">{tab.label}</span>
                  </button>
                )
              })}
            </div>

            {(mode === 'conversations' || mode === 'replies') && (
              <div className="flex flex-col gap-2 sm:flex-row">
                <label className="flex h-9 min-w-0 flex-1 items-center gap-2 rounded-md border border-border/60 bg-background px-2.5 text-muted-foreground">
                  <MagnifyingGlass className="size-4 shrink-0" weight="thin" />
                  <input
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                    placeholder={mode === 'replies' ? uz.replies.search : uz.conversations.search}
                    className="min-w-0 flex-1 bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
                  />
                </label>

                {mode === 'conversations' && (
                  <div className="flex shrink-0 items-center gap-1">
                    {inboxViews.map((item) => (
                      <button
                        key={item.key}
                        type="button"
                        onClick={() => setView(item.key)}
                        className={cn(
                          'h-9 rounded-md px-2.5 text-xs font-medium transition-colors',
                          view === item.key
                            ? 'bg-foreground text-background'
                            : 'border border-border/60 text-muted-foreground hover:bg-muted hover:text-foreground',
                        )}
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-hidden">
        {mode === 'conversations' && (
          <div className="grid h-full min-h-0 grid-cols-1 md:grid-cols-[340px_minmax(0,1fr)] xl:grid-cols-[340px_minmax(0,1fr)_360px]">
            <aside className="hidden min-h-0 flex-col border-r border-border/60 bg-foreground/[0.02] md:flex">
              {activeConvo && (
                <div className="border-b border-border/60 px-4 py-3">
                  <div className="rounded-md border border-border/60 bg-background px-3 py-2">
                    <p className="text-[11px] text-muted-foreground">{uz.workspaceUi.conversations.activeConversation}</p>
                    <p className="mt-0.5 truncate text-sm font-medium">{activeConvo.customer_name}</p>
                  </div>
                </div>
              )}

              <div className="min-h-0 flex-1 overflow-hidden">
                <ConversationList
                  conversations={filtered}
                  activeId={activeConversationId ?? undefined}
                  isLoading={isLoading}
                  error={error}
                  hasNextPage={hasNextPage}
                  isFetchingNextPage={isFetchingNextPage}
                  fetchNextPage={fetchNextPage}
                  onConversationClick={() => {}}
                />
              </div>

              <div className="flex h-10 shrink-0 items-center justify-between border-t border-border/60 px-4 text-xs text-muted-foreground">
                <span>{uz.workspaceUi.conversations.loadedCount(conversations.length)}</span>
                <span>{uz.workspaceUi.conversations.updating}</span>
              </div>
            </aside>

            <section className="flex min-w-0 flex-col overflow-hidden">
              {activeConvo ? (
                <ThreadHeader conversation={activeConvo} />
              ) : (
                <div className="h-16 shrink-0 border-b border-border/60 bg-background" />
              )}
              <div className="min-h-0 flex-1 overflow-hidden">
                {activeConvo ? (
                  <ChatViewer conversationId={activeConversationKey} />
                ) : (
                  <EmptyThread />
                )}
              </div>
              {activeConversationKey && (
                <ComposeBox conversationId={activeConversationKey} />
              )}
            </section>

            <aside className="hidden w-[392px] shrink-0 overflow-hidden border-l border-border/60 bg-foreground/[0.015] xl:block">
              <div className="flex h-16 items-center justify-between border-b border-border/60 px-4">
                <div>
                  <p className="text-[11px] font-medium text-muted-foreground">{uz.workspaceUi.conversations.sellerAgent}</p>
                  <h2 className="text-sm font-semibold tracking-tight">{uz.workspaceUi.conversations.decisionPanelTitle}</h2>
                </div>
                {activeConvo?.next_best_action && (
                  <Badge variant={activeConvo.next_best_action.ready ? 'outline' : 'secondary'}>
                    {activeConvo.next_best_action.ready ? 'Tayyor' : 'Tekshiruv'}
                  </Badge>
                )}
              </div>
              <SellerAgentSurface conversation={activeConvo} />
            </aside>
          </div>
        )}

        {mode === 'pipeline' && (
          <PipelineWorkspace
            pipeline={pipeline.data}
            isLoading={pipeline.isLoading}
            isError={pipeline.isError}
            onSelectConversation={openConversation}
          />
        )}

        {mode === 'replies' && (
          <SellerAgentReplyList search={search} />
        )}
      </div>
    </div>
  )
}
