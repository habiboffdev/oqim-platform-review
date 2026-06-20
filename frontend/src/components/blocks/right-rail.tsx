import { type ChangeEvent, useMemo, useState } from 'react'
import { ArrowsClockwise, Check, PaperPlaneTilt, Warning } from '@phosphor-icons/react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { useActionRuntimeInbox, useRecentAgentRuns } from '@/hooks/use-action-runtime'
import { useBICommandMutation } from '@/hooks/use-bi-promoter'
import { uz } from '@/lib/uz'
import { cn } from '@/lib/utils'
import type { AgentRunEvent, AgentRunTimeline, CommercialActionProposal } from '@/lib/types'

type RailTab = 'runs' | 'needsApproval' | 'proposed' | 'done' | 'summary'
type BICommandMode = 'create_agent' | 'create_owner_task'
type RightRailVariant = 'desktop' | 'sheet'

const TAB_ORDER: RailTab[] = ['runs', 'needsApproval', 'proposed', 'done', 'summary']

const NEEDS_APPROVAL_STATES = new Set(['waiting_approval', 'blocked'])
const PROPOSED_STATES = new Set(['proposed'])
const DONE_STATES = new Set(['executed', 'approved'])
function bucketProposals(items: CommercialActionProposal[]) {
  const needsApproval: CommercialActionProposal[] = []
  const proposed: CommercialActionProposal[] = []
  const done: CommercialActionProposal[] = []

  for (const proposal of items) {
    if (NEEDS_APPROVAL_STATES.has(proposal.lifecycle_state)) {
      needsApproval.push(proposal)
      continue
    }
    if (PROPOSED_STATES.has(proposal.lifecycle_state)) {
      proposed.push(proposal)
      continue
    }
    if (DONE_STATES.has(proposal.lifecycle_state)) {
      done.push(proposal)
    }
  }
  return { needsApproval, proposed, done }
}

export function RightRail({
  variant = 'desktop',
  onCommandSubmitted,
}: {
  variant?: RightRailVariant
  onCommandSubmitted?: () => void
}) {
  const [activeTab, setActiveTab] = useState<RailTab>('runs')
  const [commandMode, setCommandMode] = useState<BICommandMode>('create_agent')
  const [agentName, setAgentName] = useState('')
  const [biDraft, setBiDraft] = useState('')
  const inbox = useActionRuntimeInbox()
  const recentRuns = useRecentAgentRuns()
  const biCommand = useBICommandMutation()

  const buckets = useMemo(() => bucketProposals(inbox.data?.items ?? []), [inbox.data?.items])
  const runCount = useMemo(
    () => countVisibleRunTimelines(recentRuns.data?.timelines ?? []),
    [recentRuns.data?.timelines],
  )
  const counts: Record<RailTab, number> = {
    runs: runCount,
    needsApproval: buckets.needsApproval.length,
    proposed: buckets.proposed.length,
    done: buckets.done.length,
    summary: 0,
  }
  const inferredAgentName = useMemo(() => deriveAgentName(biDraft), [biDraft])
  const resolvedAgentName = agentName.trim() || inferredAgentName
  const canSendBICommand = biDraft.trim().length >= 8
    && !biCommand.isPending
    && (commandMode !== 'create_agent' || resolvedAgentName.length >= 2)

  async function submitBICommand() {
    if (!canSendBICommand) return
    const commandText = biDraft.trim()
    if (commandMode === 'create_agent') {
      await biCommand.mutateAsync({
        command_kind: 'create_agent',
        agent_name: resolvedAgentName,
        command_text: commandText,
        permission_mode: 'ask_always',
        brain_scopes: ['knowledge', 'rules', 'voice', 'examples'],
        tool_scopes: ['telegram.read_messages'],
        trigger_sources: [],
        correlation_id: `ui:right_rail:bi_command:${Date.now()}`,
      })
      setActiveTab('needsApproval')
    } else {
      await biCommand.mutateAsync({
        command_kind: 'create_owner_task',
        command_text: commandText,
        task_title: deriveTaskTitle(commandText),
        task_detail: commandText,
        task_kind: deriveTaskKind(commandText),
        customer_label: 'Biznes',
        correlation_id: `ui:right_rail:bi_task:${Date.now()}`,
      })
      setActiveTab('proposed')
    }
    setAgentName('')
    setBiDraft('')
    onCommandSubmitted?.()
  }

  return (
    <aside
      className={cn(
        'shrink-0 flex-col border-border/60 bg-foreground/[0.015]',
        variant === 'desktop'
          ? 'hidden h-svh w-[320px] border-l lg:flex'
          : 'flex h-full min-h-0 w-full border-l',
      )}
    >
      <div className="flex items-center justify-between border-b border-border/60 px-3.5 py-3">
        <div className="min-w-0">
          <div className="font-mono text-[9px] uppercase tracking-[0.22em] text-muted-foreground/70">
            OQIM
          </div>
          <div className="truncate text-sm font-medium">{uz.workspaceUi.rightRail.title}</div>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label="Refresh"
          onClick={() => {
            void inbox.refetch()
            void recentRuns.refetch()
          }}
        >
          <ArrowsClockwise
            className={cn(
              'size-3.5 opacity-60',
              (inbox.isFetching || recentRuns.isFetching) && 'animate-spin',
            )}
            weight="thin"
          />
        </Button>
      </div>

      <nav className="flex shrink-0 gap-0.5 overflow-x-auto border-b border-border/60 px-2 py-1.5">
        {TAB_ORDER.map((tab) => {
          const active = activeTab === tab
          return (
            <button
              key={tab}
              type="button"
              onClick={() => setActiveTab(tab)}
              className={cn(
                'flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1 text-xs transition-colors',
                active
                  ? 'bg-foreground/[0.06] text-foreground'
                  : 'text-muted-foreground hover:bg-foreground/[0.03] hover:text-foreground',
              )}
            >
              <span>{uz.workspaceUi.rightRail.tabs[tab]}</span>
              {counts[tab] > 0 ? (
                <span className="rounded-full bg-foreground/10 px-1.5 font-mono text-[9px] text-foreground">
                  {counts[tab]}
                </span>
              ) : null}
            </button>
          )
        })}
      </nav>

      <div className="flex-1 overflow-y-auto px-2.5 py-3">
        {activeTab === 'runs' && (
          <AgentRunList
            timelines={recentRuns.data?.timelines ?? []}
            loading={recentRuns.isLoading}
            error={Boolean(recentRuns.error)}
          />
        )}
        {activeTab === 'needsApproval' && <ProposalList items={buckets.needsApproval} tab="needsApproval" />}
        {activeTab === 'proposed' && <ProposalList items={buckets.proposed} tab="proposed" />}
        {activeTab === 'done' && <ProposalList items={buckets.done} tab="done" />}
        {activeTab === 'summary' && <SummaryStub />}
      </div>

      <form
        className="shrink-0 border-t border-border/60 bg-background/90 px-2.5 py-2.5"
        onSubmit={(event) => {
          event.preventDefault()
          void submitBICommand()
        }}
      >
        <div className="mb-2 flex items-center justify-between gap-2">
          <span className="text-xs font-medium text-foreground">BI agent</span>
          {biCommand.isPending ? (
            <span className="text-[10px] text-muted-foreground">Taklif tayyorlanmoqda</span>
          ) : null}
        </div>
        <div className="mb-2 grid grid-cols-2 rounded-lg bg-muted p-0.5">
          {([
            ['create_agent', 'Agent'],
            ['create_owner_task', 'Vazifa'],
          ] as const).map(([mode, label]) => (
            <Button
              key={mode}
              type="button"
              variant={commandMode === mode ? 'secondary' : 'ghost'}
              size="sm"
              className={cn(
                'h-7 rounded-md text-xs shadow-none',
                commandMode !== mode && 'text-muted-foreground',
              )}
              onClick={() => setCommandMode(mode)}
            >
              {label}
            </Button>
          ))}
        </div>
        <div className="grid gap-2">
          {commandMode === 'create_agent' ? (
            <>
              <label className="sr-only" htmlFor="right-rail-agent-name">
                Agent nomi
              </label>
              <Input
                id="right-rail-agent-name"
                nativeInput
                size="sm"
                value={agentName}
                onChange={(event: ChangeEvent<HTMLInputElement>) => setAgentName(event.target.value)}
                placeholder={inferredAgentName ? `${inferredAgentName} (taklif)` : 'Agent nomi'}
              />
            </>
          ) : null}
          <label className="sr-only" htmlFor="right-rail-bi-command">
            BI topshiriq
          </label>
          <Textarea
            id="right-rail-bi-command"
            value={biDraft}
            onChange={(event) => setBiDraft(event.target.value)}
            placeholder={
              commandMode === 'create_agent'
                ? 'Masalan: Instagramdan kelgan savollar uchun alohida agent yarat'
                : 'Masalan: Ertaga soat 11:00 da uchrashuvni eslat'
            }
            className="min-h-20 resize-none rounded-lg text-sm"
          />
          <div className="flex items-center justify-between gap-2">
            <p className="line-clamp-2 text-[11px] leading-4 text-muted-foreground">
              {commandMode === 'create_agent'
                ? `Taklif Amallarga tushadi. Nom: ${resolvedAgentName || 'yozilgandan keyin aniqlanadi'}.`
                : 'Vazifa Amallarga tushadi; egasi tasdiqlab bajaradi.'}
            </p>
            <Button
              type="submit"
              size="sm"
              variant="default"
              disabled={!canSendBICommand}
              className="shrink-0"
            >
              <PaperPlaneTilt className="size-3.5" weight="thin" />
              {uz.workspaceUi.rightRail.bi.send}
            </Button>
          </div>
        </div>
      </form>
    </aside>
  )
}

function AgentRunList({
  timelines,
  loading,
  error,
}: {
  timelines: AgentRunTimeline[]
  loading: boolean
  error: boolean
}) {
  if (loading) {
    return (
      <div className="space-y-3 px-1">
        {[0, 1, 2].map((item) => (
          <div key={item} className="space-y-2 border-b border-border/60 pb-3">
            <div className="h-3 w-28 rounded-full bg-foreground/[0.06]" />
            <div className="h-2.5 w-48 rounded-full bg-foreground/[0.04]" />
          </div>
        ))}
      </div>
    )
  }
  if (error) {
    return (
      <div className="px-2 py-8 text-center text-xs text-muted-foreground/70">
        Jarayonni o‘qib bo‘lmadi. Yangilash mumkin.
      </div>
    )
  }

  const visible = timelines
    .map((timeline) => {
      const events = visibleRunEvents(timeline)
      return { timeline, events, latest: events.at(-1) }
    })
    .filter((item): item is { timeline: AgentRunTimeline; events: AgentRunEvent[]; latest: AgentRunEvent } =>
      Boolean(item.latest),
    )

  if (visible.length === 0) {
    return (
      <div className="px-2 py-8 text-center text-xs text-muted-foreground/70">
        {uz.workspaceUi.rightRail.empty.runs}
      </div>
    )
  }

  return (
    <div
      role="status"
      aria-live="polite"
      aria-label="Agent jarayoni"
      className="space-y-4"
    >
      {visible.map(({ timeline, events, latest }) => (
        <section key={timeline.run_id} className="border-b border-border/60 pb-4 last:border-b-0">
          <div className="mb-2 flex items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="truncate text-xs font-medium text-foreground">
                {agentKindLabel(timeline.run?.agent_kind)}
              </div>
              <div className="mt-0.5 truncate text-[11px] text-muted-foreground">
                {latest.owner_label}
              </div>
            </div>
            <span
              className={cn(
                'shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-medium',
                runStateClass(timeline.run?.state),
              )}
            >
              {runStateLabel(timeline.run?.state)}
            </span>
          </div>
          <ol className="space-y-2">
            {events.slice(-3).map((event) => (
              <li key={event.event_id} className="grid grid-cols-[10px_1fr] gap-2 text-xs">
                <span
                  className={cn(
                    'mt-1 size-1.5 rounded-full',
                    event.visibility === 'customer_action'
                      ? 'bg-amber-500'
                      : timeline.run?.state === 'running'
                        ? 'animate-pulse bg-emerald-500'
                        : 'bg-muted-foreground/40',
                  )}
                />
                <div className="min-w-0">
                  <div className="truncate font-medium text-foreground">{event.owner_label}</div>
                  {event.owner_detail ? (
                    <div className="mt-0.5 line-clamp-2 text-[11px] leading-4 text-muted-foreground">
                      {event.owner_detail}
                    </div>
                  ) : null}
                </div>
              </li>
            ))}
          </ol>
        </section>
      ))}
    </div>
  )
}

function ProposalList({ items, tab }: { items: CommercialActionProposal[]; tab: RailTab }) {
  if (items.length === 0) {
    return (
      <div className="px-2 py-8 text-center text-xs text-muted-foreground/70">
        {uz.workspaceUi.rightRail.empty[tab]}
      </div>
    )
  }
  return (
    <ul className="flex flex-col gap-1.5">
      {items.map((proposal) => (
        <li key={proposal.proposal_id}>
          <ProposalRow proposal={proposal} />
        </li>
      ))}
    </ul>
  )
}

function ProposalRow({ proposal }: { proposal: CommercialActionProposal }) {
  const riskBadge =
    proposal.risk_level === 'high' || proposal.risk_level === 'destructive'
      ? 'high'
      : proposal.risk_level === 'medium'
        ? 'medium'
        : 'low'
  return (
    <div className="rounded-md border border-border/60 bg-background/80 px-2.5 py-2 text-xs">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate font-medium text-foreground">
            {humanizeActionType(proposal.action_type)}
          </div>
          <div className="mt-0.5 truncate text-[11px] text-muted-foreground">
            {humanizeReason(proposal.reason_code, proposal.confidence)}
          </div>
        </div>
        <span
          className={cn(
            'shrink-0 rounded-full px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider',
            riskBadge === 'high'
              ? 'bg-foreground text-background'
              : riskBadge === 'medium'
                ? 'bg-foreground/15 text-foreground'
                : 'bg-foreground/[0.06] text-muted-foreground',
          )}
        >
          {riskBadge === 'high' ? (
            <Warning className="inline size-3 align-text-bottom" weight="thin" />
          ) : (
            <Check className="inline size-3 align-text-bottom" weight="thin" />
          )}
          <span className="ml-1">{riskLabel(proposal.risk_level)}</span>
        </span>
      </div>
    </div>
  )
}

function visibleRunEvents(timeline: AgentRunTimeline): AgentRunEvent[] {
  return timeline.events.filter(
    (event) =>
      event.visibility !== 'internal' &&
      (event.owner_label.trim().length > 0 || event.owner_detail.trim().length > 0),
  )
}

function countVisibleRunTimelines(timelines: AgentRunTimeline[]): number {
  return timelines.filter((timeline) => visibleRunEvents(timeline).length > 0).length
}

function agentKindLabel(kind: string | null | undefined): string {
  const known: Record<string, string> = {
    seller: 'Sotuvchi agent',
    support: 'Yordam agenti',
    catalog_update: 'Katalog agenti',
    follow_up: 'Qayta aloqa agenti',
    bi: 'BI agent',
  }
  return known[kind ?? ''] ?? 'Agent'
}

function runStateLabel(state: string | null | undefined): string {
  const known: Record<string, string> = {
    queued: 'Navbatda',
    running: 'Ishlayapti',
    waiting_tool: 'Kutyapti',
    waiting_approval: 'Ruxsat kutmoqda',
    completed: 'Tugadi',
    failed: 'Xato',
    cancelled: 'To‘xtadi',
  }
  return known[state ?? ''] ?? 'Jarayonda'
}

function runStateClass(state: string | null | undefined): string {
  if (state === 'running') return 'bg-emerald-50 text-emerald-700'
  if (state === 'waiting_approval') return 'bg-amber-50 text-amber-700'
  if (state === 'failed' || state === 'cancelled') return 'bg-red-50 text-red-700'
  if (state === 'completed') return 'bg-foreground/[0.06] text-muted-foreground'
  return 'bg-foreground/[0.05] text-foreground'
}

function SummaryStub() {
  return (
    <div className="px-2 py-8 text-center text-xs text-muted-foreground/70">
      {uz.workspaceUi.rightRail.empty.summary}
    </div>
  )
}

function humanizeActionType(actionType: string): string {
  const known: Record<string, string> = {
    send_reply: 'Javob yuborish',
    send_status_message: 'Holat xabari',
    edit_reply: 'Javobni tahrirlash',
    edit_sent_reply: 'Javobni tahrirlash',
    send_catalog_media: 'Katalog rasmini yuborish',
    schedule_sales_follow_up: 'Qayta yozish',
    promoter_outreach: 'Qayta jalb qilish',
    create_business_task: 'Vazifa yaratish',
    check_payment: 'To‘lovni tekshirish',
    review_payment_candidate: 'To‘lovni tekshirish',
    create_delivery_order: 'Yetkazishni boshlash',
    review_identity_merge: 'Mijozlarni birlashtirish',
    catalog_import: 'Katalogni yangilash',
    approve_catalog_import: 'Katalogni tasdiqlash',
    confirm_catalog_update: 'Katalogni yangilash',
    'catalog.update_product': 'Katalog mahsulotini yangilash',
    'agent.create_custom_package': 'Agent yaratish',
    'agent.update_tool_grant': 'Agent ruxsati',
    'agent.update_trigger': 'Agent triggeri',
    send_payment_link: 'To‘lov havolasi',
    create_calendar_event: 'Uchrashuv yaratish',
    compile_automation_rule: 'Qoida yaratish',
    seller_agent_missing_business_info: 'Bilim yetishmayapti',
    seller_agent_blocked_reply_review: 'Javob tekshiruvi',
  }
  return known[actionType] ?? 'Agent amali'
}

function humanizeReason(reasonCode: string, confidence: number): string {
  const known: Record<string, string> = {
    sales_followup: 'Qayta yozish kerak',
    customer_went_cold_after_price: 'Narxdan keyin suhbat to‘xtagan',
    payment_needs_review: 'To‘lov dalili tekshiruvda',
    seller_promised_invoice: 'Sotuvchi keyingi ishni va’da qilgan',
    custom_agent_requires_owner_approval: 'Agent faqat tasdiqdan keyin yaratiladi',
    status_message_duplicate_in_run: 'Holat xabari takrorlanmadi',
    agent_tool_grant_change_requires_owner_approval: 'Ruxsat faqat tasdiqdan keyin o‘zgaradi',
    agent_tool_grant_granted: 'Ruxsat yoqildi',
    agent_tool_grant_revoked: 'Ruxsat o‘chirildi',
    agent_trigger_change_requires_owner_approval: 'Trigger faqat tasdiqdan keyin o‘zgaradi',
    agent_trigger_upserted: 'Trigger yoqildi',
    agent_trigger_deactivated: 'Trigger to‘xtatildi',
  }
  return known[reasonCode] ?? `Ishonch ${Math.round(confidence * 100)}%`
}

function riskLabel(riskLevel: string): string {
  if (riskLevel === 'high' || riskLevel === 'destructive') return 'Yuqori'
  if (riskLevel === 'medium') return 'O‘rta'
  return 'Past'
}

function deriveAgentName(commandText: string): string {
  const normalized = commandText.trim()
  if (!normalized) return ''
  const lower = normalized.toLowerCase()
  if (lower.includes('instagram')) return 'Instagram agenti'
  if (lower.includes('katalog')) return 'Katalog agenti'
  if (lower.includes('uchrashuv') || lower.includes('meeting')) return 'Uchrashuv agenti'
  if (lower.includes('yetkaz') || lower.includes('dostav')) return 'Yetkazish agenti'
  if (lower.includes('follow') || lower.includes('qayta aloqa')) return 'Qayta aloqa agenti'
  if (lower.includes('to‘lov') || lower.includes("to'lov") || lower.includes('payment')) return 'To‘lov agenti'
  const firstWords = normalized
    .replace(/[.,!?;:]+/g, ' ')
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 3)
    .join(' ')
  return firstWords ? `${capitalizeFirst(firstWords)} agenti` : ''
}

function deriveTaskTitle(commandText: string): string {
  const text = commandText.replace(/\s+/g, ' ').trim()
  if (!text) return 'BI topshirig‘i'
  const sentence = text.split(/[.!?]/)[0]?.trim() || text
  return sentence.length > 96 ? `${sentence.slice(0, 93).trim()}...` : sentence
}

function deriveTaskKind(commandText: string) {
  const lower = commandText.toLowerCase()
  if (lower.includes('uchrash') || lower.includes('meeting')) return 'meeting'
  if (lower.includes('yetkaz') || lower.includes('dostav')) return 'delivery'
  if (lower.includes('ombor') || lower.includes('stock') || lower.includes('qoldiq')) return 'stock'
  if (lower.includes('qo‘ng‘iroq') || lower.includes("qo'ng'iroq") || lower.includes('call')) return 'call'
  if (lower.includes('to‘lov') || lower.includes("to'lov") || lower.includes('payment')) return 'payment'
  if (lower.includes('qayta') || lower.includes('follow')) return 'follow_up'
  return 'business'
}

function capitalizeFirst(value: string): string {
  return value ? `${value.charAt(0).toUpperCase()}${value.slice(1)}` : value
}
