import { useMemo, useState } from 'react'
import {
  ArrowClockwise,
  Check,
  Funnel,
  ListChecks,
  PaperPlaneTilt,
  PencilSimpleLine,
  ShieldCheck,
  Warning,
  X,
} from '@phosphor-icons/react'
import { toast } from 'sonner'
import {
  useActionProposalTimeline,
  useActionRuntimeInbox,
  useApproveActionProposal,
  useEditActionProposalDraft,
  useExecuteActionProposal,
  useRejectActionProposal,
  useRequeueActionProposal,
} from '@/hooks/use-action-runtime'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { cn } from '@/lib/utils'
import type { AgentRunTimeline, CommercialActionProposal } from '@/lib/types'

const ACTION_VIEWS = [
  { id: 'needs_approval', label: 'Tasdiq kerak', states: ['proposed', 'waiting_approval'] },
  { id: 'scheduled', label: 'Rejada', states: ['approved', 'expired'] },
  { id: 'running', label: 'Ishlayapti', states: ['executing'] },
  { id: 'done', label: 'Tugadi', states: ['executed'] },
  { id: 'failed', label: 'Xato', states: ['blocked', 'failed'] },
  { id: 'rejected', label: 'Rad etilgan', states: ['rejected', 'cancelled'] },
] as const

const RISK_FILTERS = [
  { id: 'all', label: 'Hammasi' },
  { id: 'low', label: 'Past' },
  { id: 'medium', label: 'O‘rta' },
  { id: 'high', label: 'Yuqori' },
] as const

type ActionView = (typeof ACTION_VIEWS)[number]['id']
type RiskFilter = (typeof RISK_FILTERS)[number]['id']

export function ActionsPage() {
  const inbox = useActionRuntimeInbox()
  const approve = useApproveActionProposal()
  const editDraft = useEditActionProposalDraft()
  const execute = useExecuteActionProposal()
  const reject = useRejectActionProposal()
  const requeue = useRequeueActionProposal()
  const [activeView, setActiveView] = useState<ActionView>('needs_approval')
  const [riskFilter, setRiskFilter] = useState<RiskFilter>('all')
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const items = inbox.data?.items ?? []
  const counts = useMemo(() => countByView(items), [items])
  const filtered = useMemo(
    () => items.filter((proposal) => viewForProposal(proposal) === activeView)
      .filter((proposal) => riskMatches(proposal, riskFilter)),
    [activeView, items, riskFilter],
  )
  const selected = filtered.find((proposal) => proposal.proposal_id === selectedId) ?? filtered[0] ?? null
  const safeBatchItems = filtered.filter(isSafeApprovalCandidate)
  const batchVisible = activeView === 'needs_approval' && filtered.length > 0 && safeBatchItems.length === filtered.length
  const hasRiskyVisible = activeView === 'needs_approval' && filtered.some((proposal) => !isSafeRisk(proposal.risk_level))
  const busy = approve.isPending || editDraft.isPending || execute.isPending || reject.isPending || requeue.isPending

  async function approveAndExecute(proposal: CommercialActionProposal) {
    try {
      if (isNeedsApproval(proposal)) {
        await approve.mutateAsync(proposal.proposal_id)
      }
      await execute.mutateAsync(proposal.proposal_id)
      toast.success('Amal bajarildi.')
    } catch {
      toast.error('Amal holatini tekshirib bo‘lmadi. Qayta urinib ko‘ring.')
    }
  }

  async function approveSafeBatch() {
    let done = 0
    for (const proposal of safeBatchItems) {
      try {
        await approve.mutateAsync(proposal.proposal_id)
        await execute.mutateAsync(proposal.proposal_id)
        done += 1
      } catch {
        break
      }
    }
    if (done === safeBatchItems.length) {
      toast.success(`${done} ta xavfsiz amal bajarildi.`)
    } else {
      toast.error(`${done} ta amal bajarildi. Qolganlari tekshiruvda qoldi.`)
    }
  }

  async function rejectProposal(proposal: CommercialActionProposal) {
    try {
      await reject.mutateAsync({
        proposalId: proposal.proposal_id,
        reasonCode: 'owner_rejected_from_actions_page',
      })
      toast.success('Amal rad etildi.')
    } catch {
      toast.error('Rad etib bo‘lmadi. Qayta urinib ko‘ring.')
    }
  }

  async function saveDraft(proposal: CommercialActionProposal, draftText: string) {
    try {
      await editDraft.mutateAsync({
        proposalId: proposal.proposal_id,
        draftText,
      })
      toast.success('Javob matni saqlandi.')
    } catch {
      toast.error('Matnni saqlab bo‘lmadi. Qayta urinib ko‘ring.')
    }
  }

  async function retryProposal(proposal: CommercialActionProposal) {
    try {
      await requeue.mutateAsync(proposal.proposal_id)
      toast.success('Amal qayta navbatga qo‘yildi.')
    } catch {
      toast.error('Qayta navbatga qo‘yib bo‘lmadi.')
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-background text-foreground">
      <header className="shrink-0 border-b border-border/60 px-5 py-4 md:px-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2.5">
              <ListChecks className="size-4 opacity-70" weight="thin" />
              <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
                Agent ishlari
              </p>
            </div>
            <h1 className="mt-1 text-lg font-semibold tracking-tight">Amallar</h1>
            <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
              Agentlar taklif qilgan javob, yangilash va tekshiruvlar shu yerda tasdiqlanadi.
            </p>
          </div>
          {batchVisible ? (
            <Button
              type="button"
              size="sm"
              disabled={busy}
              loading={busy}
              onClick={() => void approveSafeBatch()}
            >
              <ShieldCheck className="size-4" weight="thin" />
              Xavfsizlarini tasdiqlab bajarish
            </Button>
          ) : null}
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <nav className="flex max-w-full gap-1 overflow-x-auto rounded-lg border border-border/60 bg-background p-1">
            {ACTION_VIEWS.map((view) => (
              <Button
                key={view.id}
                type="button"
                variant={activeView === view.id ? 'secondary' : 'ghost'}
                size="xs"
                className="shrink-0"
                onClick={() => setActiveView(view.id)}
              >
                <span>{view.label}</span>
                <Badge variant="outline" size="sm" className="h-4 min-w-4 rounded-full px-1">
                  {counts[view.id]}
                </Badge>
              </Button>
            ))}
          </nav>

          <div className="flex items-center gap-1 rounded-lg border border-border/60 bg-background p-1">
            <Funnel className="ml-1 size-3.5 text-muted-foreground" weight="thin" />
            {RISK_FILTERS.map((risk) => (
              <Button
                key={risk.id}
                type="button"
                variant={riskFilter === risk.id ? 'secondary' : 'ghost'}
                size="xs"
                onClick={() => setRiskFilter(risk.id)}
              >
                {risk.label}
              </Button>
            ))}
          </div>
        </div>
      </header>

      {hasRiskyVisible ? (
        <Alert className="mx-5 mt-4 shrink-0 md:mx-6">
          <Warning className="size-4" weight="thin" />
          <AlertTitle>Yuqori xavfli amal bor</AlertTitle>
          <AlertDescription>
            Bunday amallar bittalab ochib tekshiriladi. OQIM ularni ommaviy tasdiqlamaydi.
          </AlertDescription>
        </Alert>
      ) : null}

      <div className="grid min-h-0 flex-1 grid-cols-1 xl:grid-cols-[minmax(0,1fr)_360px]">
        <ScrollArea className="min-h-0 border-r-0 border-border/60 xl:border-r">
          <div className="px-5 py-4 md:px-6">
            {inbox.isLoading ? (
              <ActionsSkeleton />
            ) : inbox.error ? (
              <Alert variant="destructive">
                <AlertTitle>Amallar yuklanmadi</AlertTitle>
                <AlertDescription>
                  Runtime bilan aloqa uzildi. Sahifani yangilang yoki keyinroq qayta urinib ko‘ring.
                </AlertDescription>
              </Alert>
            ) : filtered.length === 0 ? (
              <Empty className="min-h-[420px] border border-dashed border-border/70">
                <EmptyHeader>
                  <EmptyMedia variant="icon">
                    <ListChecks className="size-5" weight="thin" />
                  </EmptyMedia>
                  <EmptyTitle>Hozircha amal yo‘q</EmptyTitle>
                  <EmptyDescription>
                    Agentlar yangi ish taklif qilganda, u shu yerda aniq mazmun va dalil bilan chiqadi.
                  </EmptyDescription>
                </EmptyHeader>
              </Empty>
            ) : (
              <ActionTable
                items={filtered}
                selectedId={selected?.proposal_id ?? null}
                onSelect={setSelectedId}
              />
            )}
          </div>
        </ScrollArea>

        <ActionDetail
          proposal={selected}
          allItems={items}
          busy={busy}
          editPending={editDraft.isPending}
          onApproveExecute={approveAndExecute}
          onEditDraft={saveDraft}
          onReject={rejectProposal}
          onRetry={retryProposal}
        />
      </div>
    </div>
  )
}

function ActionTable({
  items,
  selectedId,
  onSelect,
}: {
  items: CommercialActionProposal[]
  selectedId: string | null
  onSelect: (proposalId: string) => void
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Amal</TableHead>
          <TableHead>Mijoz</TableHead>
          <TableHead>Xavf</TableHead>
          <TableHead className="hidden 2xl:table-cell">Holat</TableHead>
          <TableHead className="hidden text-right 2xl:table-cell">Ishonch</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((proposal) => {
          const selected = selectedId === proposal.proposal_id
          return (
            <TableRow
              key={proposal.proposal_id}
              data-state={selected ? 'selected' : undefined}
              className="cursor-pointer"
              onClick={() => onSelect(proposal.proposal_id)}
            >
              <TableCell className="max-w-[360px] whitespace-normal py-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{actionLabel(proposal.action_type)}</p>
                  <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">
                    {proposalTitle(proposal)}
                  </p>
                </div>
              </TableCell>
              <TableCell className="max-w-[220px] whitespace-normal text-sm">
                {proposalCustomer(proposal)}
              </TableCell>
              <TableCell>
                <RiskBadge risk={proposal.risk_level} />
              </TableCell>
              <TableCell className="hidden 2xl:table-cell">
                <LifecycleBadge state={proposal.lifecycle_state} />
              </TableCell>
              <TableCell className="hidden text-right font-mono text-xs text-muted-foreground 2xl:table-cell">
                {Math.round(proposal.confidence * 100)}%
              </TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}

function ActionDetail({
  proposal,
  allItems,
  busy,
  editPending,
  onApproveExecute,
  onEditDraft,
  onReject,
  onRetry,
}: {
  proposal: CommercialActionProposal | null
  allItems: CommercialActionProposal[]
  busy: boolean
  editPending: boolean
  onApproveExecute: (proposal: CommercialActionProposal) => Promise<void>
  onEditDraft: (proposal: CommercialActionProposal, draftText: string) => Promise<void>
  onReject: (proposal: CommercialActionProposal) => Promise<void>
  onRetry: (proposal: CommercialActionProposal) => Promise<void>
}) {
  const timeline = useActionProposalTimeline(proposal?.proposal_id)

  if (!proposal) {
    return (
      <aside className="hidden min-h-0 xl:block">
        <Empty className="h-full">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <PaperPlaneTilt className="size-5" weight="thin" />
            </EmptyMedia>
            <EmptyTitle>Amal tanlang</EmptyTitle>
            <EmptyDescription>Dalil, xavf va bajariladigan ish shu yerda ko‘rinadi.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      </aside>
    )
  }

  const similar = allItems
    .filter((item) => item.proposal_id !== proposal.proposal_id)
    .filter((item) => item.action_type === proposal.action_type && item.lifecycle_state === 'executed')
    .slice(0, 3)
  const canApproveExecute = isNeedsApproval(proposal) || proposal.lifecycle_state === 'approved'
  const canRetry = proposal.lifecycle_state === 'failed' || proposal.lifecycle_state === 'blocked'
  const canReject = !['executed', 'rejected', 'cancelled'].includes(proposal.lifecycle_state)
  const draftText = replyPreview(proposal)
  const canEditDraft = Boolean(draftText) && isNeedsApproval(proposal)

  return (
    <aside className="min-h-0 border-t border-border/60 bg-foreground/[0.015] xl:border-t-0">
      <ScrollArea className="h-full">
        <div className="space-y-5 px-5 py-5">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
                Amal tafsiloti
              </p>
              <h2 className="mt-1 text-base font-semibold tracking-tight">{actionLabel(proposal.action_type)}</h2>
            </div>
            <RiskBadge risk={proposal.risk_level} />
          </div>

          <div className="flex flex-wrap gap-2">
            <LifecycleBadge state={proposal.lifecycle_state} />
            <Badge variant={proposal.requires_approval ? 'warning' : 'success'} className="rounded-md">
              {proposal.requires_approval ? 'Ruxsat kerak' : 'Avtomatik mumkin'}
            </Badge>
          </div>

          <Separator />

          <AgentProgressBlock
            timeline={timeline.data ?? null}
            loading={timeline.isLoading}
            error={Boolean(timeline.error)}
          />

          <Separator />

          <DetailBlock title="Nima bo‘ladi" value={actionOutcome(proposal.action_type)} />
          <DetailBlock title="Mijoz" value={proposalCustomer(proposal)} />
          <DetailBlock title="Mazmun" value={proposalTitle(proposal)} />
          <DetailBlock title="Sabab" value={reasonLabel(proposal.reason_code)} />
          {draftText ? (
            <DraftTextBlock
              key={proposal.proposal_id}
              proposal={proposal}
              value={draftText}
              editable={canEditDraft}
              pending={editPending}
              onSave={onEditDraft}
            />
          ) : null}

          <section>
            <h3 className="text-sm font-medium">Dalillar</h3>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {proposal.source_refs.length > 0 ? (
                proposal.source_refs.slice(0, 8).map((ref, index) => (
                  <Badge key={`${ref}-${index}`} variant="outline" className="rounded-md">
                    {evidenceLabel(ref)}
                  </Badge>
                ))
              ) : (
                <p className="text-sm text-muted-foreground">Dalil hali bog‘lanmagan.</p>
              )}
            </div>
          </section>

          <section>
            <h3 className="text-sm font-medium">O‘xshash bajarilgan ishlar</h3>
            <div className="mt-2 space-y-2">
              {similar.length > 0 ? (
                similar.map((item) => (
                  <div key={item.proposal_id} className="rounded-lg border border-border/70 bg-background px-3 py-2">
                    <p className="text-sm font-medium">{proposalCustomer(item)}</p>
                    <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">{proposalTitle(item)}</p>
                  </div>
                ))
              ) : (
                <p className="rounded-lg border border-dashed border-border/70 px-3 py-2 text-sm text-muted-foreground">
                  Hozircha o‘xshash bajarilgan amal topilmadi.
                </p>
              )}
            </div>
          </section>

          <Separator />

          <div className="flex flex-wrap gap-2">
            {canApproveExecute ? (
              <Button
                type="button"
                size="sm"
                disabled={busy}
                loading={busy}
                onClick={() => void onApproveExecute(proposal)}
              >
                <Check className="size-4" weight="thin" />
                {isNeedsApproval(proposal) ? 'Tasdiqlab bajarish' : 'Bajarish'}
              </Button>
            ) : null}
            {canRetry ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={busy}
                loading={busy}
                onClick={() => void onRetry(proposal)}
              >
                <ArrowClockwise className="size-4" weight="thin" />
                Qayta urinish
              </Button>
            ) : null}
            {canReject ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={busy}
                onClick={() => void onReject(proposal)}
              >
                <X className="size-4" weight="thin" />
                Rad etish
              </Button>
            ) : null}
          </div>
        </div>
      </ScrollArea>
    </aside>
  )
}

function ActionsSkeleton() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 7 }).map((_, index) => (
        <div key={index} className="grid grid-cols-[1fr_160px_90px_90px_70px] items-center gap-3 border-b border-border/60 py-3">
          <div className="space-y-2">
            <Skeleton className="h-4 w-40" />
            <Skeleton className="h-3 w-64" />
          </div>
          <Skeleton className="h-4 w-28" />
          <Skeleton className="h-5 w-14 rounded-md" />
          <Skeleton className="h-5 w-20 rounded-md" />
          <Skeleton className="h-4 w-10 justify-self-end" />
        </div>
      ))}
    </div>
  )
}

function DetailBlock({ title, value, preserve = false }: { title: string; value: string; preserve?: boolean }) {
  return (
    <section>
      <h3 className="text-sm font-medium">{title}</h3>
      <p className={cn('mt-1 text-sm leading-6 text-muted-foreground', preserve && 'whitespace-pre-wrap')}>
        {value}
      </p>
    </section>
  )
}

function AgentProgressBlock({
  timeline,
  loading,
  error,
}: {
  timeline: AgentRunTimeline | null
  loading: boolean
  error: boolean
}) {
  const visibleEvents = (timeline?.events ?? [])
    .filter((event) => event.visibility !== 'internal')
    .filter((event) => event.owner_label.trim() || event.owner_detail.trim())
    .slice(-6)

  return (
    <section>
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-medium">Jarayon</h3>
        {timeline?.run ? (
          <Badge variant="outline" className="rounded-md">
            {agentRunStateLabel(timeline.run.state)}
          </Badge>
        ) : null}
      </div>
      {loading ? (
        <div className="mt-3 space-y-2">
          <Skeleton className="h-4 w-44" />
          <Skeleton className="h-4 w-56" />
        </div>
      ) : error ? (
        <p className="mt-2 rounded-lg border border-dashed border-border/70 px-3 py-2 text-sm text-muted-foreground">
          Jarayon holatini hozircha ko‘rsatib bo‘lmadi.
        </p>
      ) : visibleEvents.length > 0 ? (
        <ol className="mt-3 space-y-3">
          {visibleEvents.map((event) => (
            <li key={event.event_id} className="grid grid-cols-[14px_1fr] gap-2">
              <span
                className={cn(
                  'mt-1 size-2 rounded-full',
                  event.visibility === 'customer_action' ? 'bg-blue-500' : 'bg-emerald-500',
                )}
              />
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-1.5">
                  <p className="text-sm font-medium leading-5">{progressLabel(event.owner_label)}</p>
                  {event.visibility === 'customer_action' ? (
                    <Badge variant="outline" className="rounded-md">
                      Mijozga ko‘rinadi
                    </Badge>
                  ) : null}
                </div>
                {event.owner_detail ? (
                  <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
                    {progressDetail(event.owner_detail)}
                  </p>
                ) : null}
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <p className="mt-2 rounded-lg border border-dashed border-border/70 px-3 py-2 text-sm text-muted-foreground">
          Bu amal uchun jarayon yozuvi hali yo‘q.
        </p>
      )}
    </section>
  )
}

function DraftTextBlock({
  proposal,
  value,
  editable,
  pending,
  onSave,
}: {
  proposal: CommercialActionProposal
  value: string
  editable: boolean
  pending: boolean
  onSave: (proposal: CommercialActionProposal, draftText: string) => Promise<void>
}) {
  const [editing, setEditing] = useState(false)
  const [draftText, setDraftText] = useState(value)
  const cleanDraft = draftText.trim()
  const changed = cleanDraft !== value.trim()
  const canSave = cleanDraft.length > 0 && changed && !pending

  async function saveDraft() {
    if (!canSave) return
    await onSave(proposal, cleanDraft)
    setEditing(false)
  }

  return (
    <section>
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-medium">Matn</h3>
        {editable && !editing ? (
          <Button type="button" size="xs" variant="ghost" onClick={() => setEditing(true)}>
            <PencilSimpleLine className="size-3.5" weight="thin" />
            Tahrirlash
          </Button>
        ) : null}
      </div>
      {editing ? (
        <div className="mt-2 space-y-2">
          <Label htmlFor={`action-draft-${proposal.proposal_id}`}>Javob matni</Label>
          <Textarea
            id={`action-draft-${proposal.proposal_id}`}
            value={draftText}
            onChange={(event) => setDraftText(event.currentTarget.value)}
            className="min-h-32"
          />
          <div className="flex flex-wrap gap-2">
            <Button type="button" size="sm" disabled={!canSave} loading={pending} onClick={() => void saveDraft()}>
              <Check className="size-4" weight="thin" />
              Saqlash
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={pending}
              onClick={() => {
                setDraftText(value)
                setEditing(false)
              }}
            >
              Bekor qilish
            </Button>
          </div>
        </div>
      ) : (
        <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-muted-foreground">
          {value}
        </p>
      )}
    </section>
  )
}

function countByView(items: CommercialActionProposal[]): Record<ActionView, number> {
  return ACTION_VIEWS.reduce((acc, view) => {
    acc[view.id] = items.filter((proposal) => viewForProposal(proposal) === view.id).length
    return acc
  }, {} as Record<ActionView, number>)
}

function viewForProposal(proposal: CommercialActionProposal): ActionView {
  const match = ACTION_VIEWS.find((view) => (view.states as readonly string[]).includes(proposal.lifecycle_state))
  return match?.id ?? 'needs_approval'
}

function riskMatches(proposal: CommercialActionProposal, filter: RiskFilter) {
  if (filter === 'all') return true
  if (filter === 'high') return !isSafeRisk(proposal.risk_level)
  return proposal.risk_level === filter
}

function isNeedsApproval(proposal: CommercialActionProposal) {
  return proposal.lifecycle_state === 'proposed' || proposal.lifecycle_state === 'waiting_approval'
}

function isSafeApprovalCandidate(proposal: CommercialActionProposal) {
  return isNeedsApproval(proposal) && isSafeRisk(proposal.risk_level)
}

function isSafeRisk(risk: string) {
  return risk === 'low' || risk === 'medium'
}

function RiskBadge({ risk }: { risk: string }) {
  const risky = !isSafeRisk(risk)
  return (
    <Badge variant={risky ? 'warning' : risk === 'medium' ? 'info' : 'success'} className="rounded-md">
      {riskLabel(risk)}
    </Badge>
  )
}

function LifecycleBadge({ state }: { state: string }) {
  const variant = state === 'executed'
    ? 'success'
    : state === 'failed' || state === 'blocked'
      ? 'error'
      : state === 'rejected' || state === 'cancelled'
        ? 'outline'
        : state === 'executing'
          ? 'info'
          : 'warning'
  return <Badge variant={variant} className="rounded-md">{stateLabel(state)}</Badge>
}

function riskLabel(risk: string) {
  if (risk === 'low') return 'Past xavf'
  if (risk === 'medium') return 'O‘rta xavf'
  if (risk === 'high') return 'Yuqori xavf'
  if (risk === 'critical') return 'Juda xavfli'
  return 'Tekshiruv'
}

function stateLabel(state: string) {
  const known: Record<string, string> = {
    proposed: 'Taklif',
    waiting_approval: 'Tasdiq kutmoqda',
    approved: 'Tasdiqlandi',
    executing: 'Ishlayapti',
    executed: 'Bajarildi',
    rejected: 'Rad etildi',
    blocked: 'To‘xtadi',
    failed: 'Xato',
    expired: 'Eskirdi',
    cancelled: 'Bekor qilindi',
  }
  return known[state] ?? 'Tekshiruv'
}

function agentRunStateLabel(state: string) {
  const known: Record<string, string> = {
    queued: 'Navbatda',
    running: 'Ishlayapti',
    waiting_approval: 'Tasdiq kutmoqda',
    waiting_tool: 'Tekshiruvda',
    completed: 'Tugadi',
    failed: 'Xato',
    cancelled: 'Bekor qilindi',
  }
  return known[state] ?? 'Jarayon'
}

function progressLabel(label: string) {
  return label.trim() || 'Agent holati yangilandi'
}

function progressDetail(detail: string) {
  const clean = detail.trim()
  if (!clean) return ''
  return clean
}

function actionLabel(actionType: string) {
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
    send_payment_link: 'To‘lov havolasini yuborish',
    create_calendar_event: 'Uchrashuv yaratish',
    compile_automation_rule: 'Qoida yaratish',
    'agent.create_custom_package': 'Agent yaratish',
    'agent.update_tool_grant': 'Agent ruxsatini o‘zgartirish',
    'agent.update_trigger': 'Agent triggerini o‘zgartirish',
    seller_agent_missing_business_info: 'Bilim yetishmayapti',
    seller_agent_blocked_reply_review: 'Javob tekshiruvi',
  }
  return known[actionType] ?? sentenceFromCode(actionType)
}

function actionOutcome(actionType: string) {
  const known: Record<string, string> = {
    send_reply: 'Mijozga Telegram orqali javob yuboradi.',
    send_status_message: 'Mijozga yakuniy javob emas, qisqa holat xabarini yuboradi.',
    edit_reply: 'Oldin yuborilgan Telegram xabarini tahrirlaydi.',
    edit_sent_reply: 'Oldin yuborilgan Telegram xabarini tahrirlaydi.',
    send_catalog_media: 'Mijozga tasdiqlangan mahsulot rasmi yoki faylini yuboradi.',
    schedule_sales_follow_up: 'Mijozga qayta yozish ishini navbatga qo‘yadi.',
    promoter_outreach: 'Sovigan mijozlarga qayta murojaat taklif qiladi.',
    create_business_task: 'Biznes egasi bajarishi kerak bo‘lgan vazifa yaratadi.',
    check_payment: 'To‘lov dalilini tekshirish ishini ochadi.',
    review_payment_candidate: 'To‘lovga o‘xshash dalilni tekshiruvga beradi.',
    create_delivery_order: 'Yetkazish bo‘yicha ish ochadi.',
    review_identity_merge: 'Bir xil mijoz yozuvlarini birlashtirishni taklif qiladi.',
    create_calendar_event: 'Uchrashuvni kalendarga qo‘shadi.',
    'agent.create_custom_package': 'Tasdiqlangandan keyin agent, AGENT.md, ko‘nikma, ruxsat va triggerlarni yaratadi.',
    'agent.update_tool_grant': 'Tasdiqlangandan keyin agentning Telegram ruxsatini qo‘shadi yoki o‘chiradi.',
    'agent.update_trigger': 'Tasdiqlangandan keyin agent qachon ish boshlashini yoqadi yoki to‘xtatadi.',
  }
  return known[actionType] ?? 'Agent taklif qilgan ishni ruxsat va audit orqali bajaradi.'
}

function reasonLabel(reasonCode: string) {
  const known: Record<string, string> = {
    sales_followup: 'Mijozga qayta yozish kerak.',
    customer_went_cold_after_price: 'Narx aytilgandan keyin suhbat to‘xtagan.',
    payment_needs_review: 'To‘lov dalili tekshiruv kutmoqda.',
    seller_promised_invoice: 'Sotuvchi mijozga keyingi ishni va’da qilgan.',
    phase7_test: 'Runtime sinov amali.',
    custom_agent_requires_owner_approval: 'Yangi agent egadan tasdiq talab qiladi.',
    approval_required_before_execution: 'Bu amal bajarilishidan oldin tasdiq kerak.',
    owner_approved: 'Ega bu amalni tasdiqladi.',
    owner_edited_draft: 'Ega javob matnini tahrirladi.',
    status_message_duplicate_in_run: 'Bu jarayonda shunday holat xabari allaqachon yuborilgan.',
    custom_agent_package_created: 'Agent paketi yaratildi.',
    custom_agent_package_reused: 'Oldin yaratilgan agent paketi qayta ishlatildi.',
    agent_tool_grant_change_requires_owner_approval: 'Agent ruxsati faqat egadan tasdiq olgandan keyin o‘zgaradi.',
    agent_tool_grant_granted: 'Agent ruxsati yoqildi.',
    agent_tool_grant_revoked: 'Agent ruxsati o‘chirildi.',
    tool_grant_not_found: 'O‘chiriladigan ruxsat topilmadi.',
    unsupported_tool_scope: 'Bu integratsiya ruxsati qo‘llab-quvvatlanmaydi.',
    agent_trigger_change_requires_owner_approval: 'Trigger faqat egadan tasdiq olgandan keyin o‘zgaradi.',
    agent_trigger_upserted: 'Trigger yoqildi.',
    agent_trigger_deactivated: 'Trigger to‘xtatildi.',
    agent_trigger_not_found: 'Trigger topilmadi.',
  }
  return known[reasonCode] ?? 'OQIM suhbat yoki manbadan ish signali topdi.'
}

function proposalTitle(proposal: CommercialActionProposal) {
  const candidate = objectValue(proposal.payload.candidate_value)
  return stringValue(candidate.title)
    || stringValue(candidate.task_title)
    || stringValue(candidate.summary)
    || stringValue(candidate.state_type)
    || stringValue(proposal.payload.title)
    || stringValue(proposal.payload.summary)
    || replyPreview(proposal)
    || actionLabel(proposal.action_type)
}

function proposalCustomer(proposal: CommercialActionProposal) {
  return stringValue(proposal.payload.customer_name)
    || stringValue(proposal.payload.customer_display_name)
    || `Mijoz ${proposal.customer_id}`
}

function replyPreview(proposal: CommercialActionProposal) {
  const candidate = objectValue(proposal.payload.candidate_value)
  return stringValue(proposal.payload.draft_text)
    || stringValue(proposal.payload.reply_text)
    || stringValue(proposal.payload.message_text)
    || stringValue(candidate.reply_text)
    || stringValue(candidate.message)
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function stringValue(value: unknown) {
  return typeof value === 'string' && value.trim() ? value.trim() : ''
}

function sentenceFromCode(value: string) {
  if (!value) return 'Agent ishi'
  return value
    .replaceAll('.', ' ')
    .replaceAll('_', ' ')
    .split(' ')
    .filter(Boolean)
    .map((part, index) => index === 0 ? part.charAt(0).toUpperCase() + part.slice(1) : part)
    .join(' ')
}

function evidenceLabel(ref: string) {
  if (ref.startsWith('message:') || ref.includes(':message:')) return telegramMessageLabel(ref)
  if (ref.startsWith('conversation:')) return `Suhbat ${idRefLabel(ref.replace(/^conversation:/, ''))}`
  if (ref.startsWith('source_unit:')) return `Manba bo‘lagi: ${sourceRefName(ref)}`
  if (ref.startsWith('source:') || ref.startsWith('onboarding:')) return `Manba: ${sourceRefName(ref)}`
  if (ref.startsWith('catalog:')) return `Katalog: ${humanRefName(ref.replace(/^catalog:/, ''))}`
  if (ref.startsWith('fact:')) return `Brain: ${humanRefName(ref.replace(/^fact:/, ''))}`
  if (ref.startsWith('policy:')) return `Ruxsat: ${humanRefName(ref.replace(/^policy:/, ''))}`
  if (ref.startsWith('candidate:')) return `Topilgan signal: ${humanRefName(ref.replace(/^candidate:/, ''))}`
  if (ref.startsWith('agent_package_request:')) return `Agent taklifi: ${humanRefName(ref.replace(/^agent_package_request:/, ''))}`
  if (ref.startsWith('agent_tool_grant:')) return `Integratsiya ruxsati: ${humanRefName(ref.replace(/^agent_tool_grant:/, ''))}`
  if (ref.startsWith('agent_trigger:')) return `Agent triggeri: ${humanRefName(ref.replace(/^agent_trigger:/, ''))}`
  if (ref.startsWith('owner_task:')) return `Vazifa: ${humanRefName(ref.replace(/^owner_task:/, ''))}`
  if (ref.startsWith('bi_command:')) return `BI: ${humanRefName(ref.replace(/^bi_command:/, ''))}`
  return `Dalil: ${humanRefName(ref)}`
}

function telegramMessageLabel(ref: string) {
  const label = messageRefLabel(ref).replace(/^:\s*/, '').trim()
  if (!label) return 'Telegram xabari'
  return `Telegram xabari${label.startsWith('#') ? ' ' : ': '}${label}`
}

function messageRefLabel(ref: string) {
  const parts = ref.split(':').filter(Boolean)
  const messageIndex = parts.indexOf('message')
  if (messageIndex >= 0 && parts[messageIndex + 1]) {
    const suffix = parts.slice(messageIndex + 2).map(humanRefName).join(' ')
    return `${messageIdLabel(parts[messageIndex + 1])} ${suffix}`.trim()
  }
  if (parts[0] === 'message' && parts[1]) {
    const suffix = parts.slice(2).map(humanRefName).join(' ')
    return `${messageIdLabel(parts[1])} ${suffix}`.trim()
  }
  return messageIdLabel(ref)
}

function messageIdLabel(value: string) {
  const label = idRefLabel(value)
  if (label.startsWith('#')) return label
  return label ? `: ${label}` : ''
}

function idRefLabel(value: string) {
  const cleaned = value.trim()
  if (!cleaned) return ''
  return /^\d+$/.test(cleaned) ? `#${cleaned}` : humanRefName(cleaned)
}

function sourceRefName(ref: string) {
  const parts = ref.split(':').filter(Boolean)
  const sourceIndex = parts.indexOf('source')
  if (sourceIndex >= 0 && parts[sourceIndex + 1] && !['ingested', 'unit'].includes(parts[sourceIndex + 1])) {
    return humanRefName(parts[sourceIndex + 1])
  }
  if (ref.startsWith('onboarding:source:')) {
    const candidate = ref.replace(/^onboarding:source:/, '').split(':')[0] ?? ''
    return /^\d+$/.test(candidate) ? `#${candidate}` : humanRefName(candidate)
  }
  const meaningful = parts.filter((part) => (
    !['source_unit', 'business_source', 'workspace', 'onboarding', 'source', 'ingested', 'unit'].includes(part)
    && !/^\d+$/.test(part)
  ))
  if (meaningful.length > 0) return humanRefName(meaningful[meaningful.length - 1])
  const numeric = [...parts].reverse().find((part) => /^\d+$/.test(part))
  return numeric ? `#${numeric}` : 'o‘qilgan manba'
}

function humanRefName(value: string) {
  const cleaned = value.trim().replace(/^:+|:+$/g, '')
  if (!cleaned) return 'dalil'
  return cleaned.replace(/[._-]+/g, ' ').split(/\s+/).filter(Boolean).join(' ')
}
