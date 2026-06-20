import {
  ArrowClockwise,
  Brain,
  CheckCircle,
  ClockCounterClockwise,
  Funnel,
  ImageSquare,
  Lightning,
  Package,
  PaperPlaneTilt,
  ShieldCheck,
  Sparkle,
  WarningCircle,
  X,
} from '@phosphor-icons/react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { SellerAgentReplyReviewCard } from '@/components/blocks/seller-agent/seller-agent-reply-review-card'
import { readyCatalogProducts, uniqueCatalogSources } from '@/components/blocks/brain/catalog-workbench-model'
import {
  useActionRuntimeInbox,
  useApproveActionProposal,
  useExecuteActionProposal,
  useProcessActionProposal,
  useRejectActionProposal,
} from '@/hooks/use-action-runtime'
import { useLatestSellerAgentReply } from '@/hooks/use-seller-agent-replies'
import { useBrainCatalog } from '@/hooks/use-business-brain'
import { useShimmerState } from '@/hooks/use-websocket'
import { cn } from '@/lib/utils'
import { uz } from '@/lib/uz'
import type { SellerAgentReply, CatalogWorkspaceProduct, CommercialActionProposal, Conversation } from '@/lib/types'

interface SellerAgentSurfaceProps {
  conversation: Conversation | null | undefined
}

const ACTION_LABELS: Record<string, string> = {
  seller_reply: 'Javob taklifi',
  promoter_outreach: 'Qayta qiziqtirish',
  task_create: 'Vazifa',
  follow_up_schedule: 'Qayta aloqa',
  sales_followup: 'Savdo qayta aloqasi',
  integration_action: 'Integratsiya',
  bi_insight: 'Tahlil',
}

const REASON_LABELS: Record<string, string> = {
  customer_went_cold_after_price: 'Narxdan keyin jim qoldi',
  low_confidence: 'Ishonch past',
  needs_seller_confirmation: 'Tasdiq kerak',
  customer_paid: 'To‘lovga o‘xshaydi',
  media_ready: 'Media tayyor',
}

function compactLabel(value: string | null | undefined) {
  if (!value) return 'Tekshirish kerak'
  return ACTION_LABELS[value] ?? REASON_LABELS[value] ?? value.split('_').join(' ')
}

function stageLabel(stage: string | null | undefined) {
  if (!stage) return 'Bosqich noma’lum'
  return (uz.pipeline.stages as Record<string, string>)[stage] || stage
}

function confidenceLabel(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return 'Ishonch yo‘q'
  return `${Math.round(value * 100)}% ishonch`
}

function evidenceRefLabel(ref: string) {
  const normalized = String(ref || '').toLowerCase()
  if (normalized.startsWith('message:') || normalized.startsWith('conversation:')) {
    return uz.workspaceUi.conversations.conversationEvidence
  }
  if (normalized.includes('media') || normalized.includes('photo') || normalized.includes('image')) {
    return uz.workspaceUi.conversations.mediaEvidence
  }
  if (normalized.startsWith('source') || normalized.includes('source_unit')) {
    return uz.workspaceUi.conversations.sourceEvidence
  }
  return uz.workspaceUi.conversations.systemEvidence
}

function payloadText(payload: Record<string, unknown>) {
  const candidates = [
    payload.message_goal,
    payload.label,
    payload.reply_text,
    payload.draft_text,
    payload.summary,
    payload.title,
  ]
  for (const value of candidates) {
    if (typeof value === 'string' && value.trim()) return value
  }
  const brief = payload.reply_brief ?? payload.draft_brief
  if (brief && typeof brief === 'object' && !Array.isArray(brief)) {
    const goal = (brief as Record<string, unknown>).message_goal
    if (typeof goal === 'string' && goal.trim()) return goal
  }
  return null
}

function proposalTone(proposal: CommercialActionProposal): 'outline' | 'warning' | 'success' | 'error' | 'info' {
  if (proposal.lifecycle_state === 'approved' || proposal.lifecycle_state === 'executed') return 'success'
  if (proposal.lifecycle_state === 'blocked' || proposal.lifecycle_state === 'failed') return 'error'
  if (proposal.risk_level === 'high' || proposal.risk_level === 'critical') return 'warning'
  return 'info'
}

function nextActionCopy(conversation: Conversation) {
  const action = conversation.next_best_action
  if (!action) return 'AI hali keyingi eng yaxshi amalni chiqarmadi.'
  if (!action.ready) {
    if (action.reason === 'waiting_on_media_hydration') {
      return 'Media o‘rganilmoqda. Rasm, video yoki ovozdan keyin javob tayyorlanadi.'
    }
    return `Hozircha bloklangan: ${compactLabel(action.reason)}.`
  }
  return compactLabel(action.action)
}

function conversationProposals(proposals: CommercialActionProposal[], conversationId: number) {
  return proposals.filter((proposal) => proposal.conversation_id === conversationId).slice(0, 3)
}

function hasPendingMutations(mutations: Array<{ isPending: boolean }>) {
  return mutations.some((mutation) => mutation.isPending)
}

function nextActionReady(reply: SellerAgentReply | null | undefined, conversation: Conversation) {
  return Boolean(reply) || Boolean(conversation.next_best_action?.ready)
}

function nextActionText(reply: SellerAgentReply | null | undefined, conversation: Conversation) {
  if (reply) return 'Javob tayyor. Yuborishdan oldin dalil va ohangni tekshiring.'
  return nextActionCopy(conversation)
}

function SurfaceEmpty() {
  return (
    <div className="flex h-full items-center justify-center px-5">
      <div className="max-w-xs text-center">
        <div className="mx-auto flex size-10 items-center justify-center rounded-lg border border-border/70 bg-background">
          <Sparkle className="size-5" weight="thin" />
        </div>
        <h2 className="mt-4 text-sm font-semibold tracking-tight">Suhbat tanlang</h2>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">
          {uz.workspaceUi.conversations.sellerAgentEmpty}
        </p>
      </div>
    </div>
  )
}

function SurfaceRow({
  icon: Icon,
  label,
  value,
  tone = 'default',
}: {
  icon: typeof Brain
  label: string
  value: string
  tone?: 'default' | 'warning' | 'success'
}) {
  return (
    <div className="grid grid-cols-[28px_1fr] gap-2 rounded-md border border-border/60 bg-background px-3 py-2.5">
      <div className={cn(
        'flex size-7 items-center justify-center rounded-md bg-foreground/[0.04] text-muted-foreground',
        tone === 'warning' && 'bg-warning/10 text-warning-foreground',
        tone === 'success' && 'bg-success/10 text-success-foreground',
      )}>
        <Icon className="size-4" weight="thin" />
      </div>
      <div className="min-w-0">
        <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          {label}
        </p>
        <p className="mt-1 truncate text-sm font-medium">{value}</p>
      </div>
    </div>
  )
}

function ProposalCard({
  proposal,
  disabled,
  onProcess,
  onApprove,
  onReject,
  onExecute,
}: {
  proposal: CommercialActionProposal
  disabled: boolean
  onProcess: (proposalId: string) => void
  onApprove: (proposalId: string) => void
  onReject: (proposalId: string) => void
  onExecute: (proposalId: string) => void
}) {
  const tone = proposalTone(proposal)
  const summary = payloadText(proposal.payload) ?? compactLabel(proposal.reason_code)
  const canApprove = proposal.lifecycle_state === 'proposed' || proposal.lifecycle_state === 'waiting_approval'
  const canExecute = proposal.lifecycle_state === 'approved'

  return (
    <div className="rounded-lg border border-border/70 bg-background p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge variant={tone} className="rounded-md">
              {compactLabel(proposal.action_type)}
            </Badge>
            <Badge variant="outline" className="rounded-md">
              {confidenceLabel(proposal.confidence)}
            </Badge>
          </div>
          <p className="mt-2 line-clamp-2 text-sm font-medium leading-5">
            {summary}
          </p>
        </div>
        <span className="shrink-0 text-[11px] text-muted-foreground">
          {compactLabel(proposal.lifecycle_state)}
        </span>
      </div>

      {proposal.source_refs.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {proposal.source_refs.slice(0, 3).map((ref) => (
            <span
              key={ref}
              className="rounded border border-border/60 bg-foreground/[0.025] px-1.5 py-0.5 text-[10px] text-muted-foreground"
            >
              {evidenceRefLabel(ref)}
            </span>
          ))}
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {canApprove && (
          <Button
            size="xs"
            onClick={() => onApprove(proposal.proposal_id)}
            disabled={disabled}
            className="gap-1.5"
          >
            <CheckCircle className="size-3.5" weight="thin" />
            Tasdiqlash
          </Button>
        )}
        {canExecute && (
          <Button
            size="xs"
            onClick={() => onExecute(proposal.proposal_id)}
            disabled={disabled}
            className="gap-1.5"
          >
            <Lightning className="size-3.5" weight="thin" />
            Bajarish
          </Button>
        )}
        <Button
          size="xs"
          variant="outline"
          onClick={() => onProcess(proposal.proposal_id)}
          disabled={disabled}
          className="gap-1.5"
        >
          <ArrowClockwise className="size-3.5" weight="thin" />
          Tekshirish
        </Button>
        {canApprove && (
          <Button
            size="xs"
            variant="ghost"
            onClick={() => onReject(proposal.proposal_id)}
            disabled={disabled}
            className="ml-auto gap-1.5 text-muted-foreground hover:text-destructive"
          >
            <X className="size-3.5" weight="thin" />
            Rad
          </Button>
        )}
      </div>
    </div>
  )
}

function SurfaceContextSummary({
  conversation,
  products,
}: {
  conversation: Conversation
  products: CatalogWorkspaceProduct[]
}) {
  const crmStage = conversation.crm_stage
  const readyProducts = readyCatalogProducts(products)
  const sourceCount = uniqueCatalogSources(products).length
  const mediaProducts = products.filter((product) => product.media.length > 0).length

  return (
    <section className="space-y-2">
      <div className="grid gap-2">
        <SurfaceRow
          icon={Funnel}
          label="Mijoz holati"
          value={stageLabel(crmStage?.stage ?? conversation.pipeline_stage)}
          tone={conversation.needs_attention ? 'warning' : 'default'}
        />
        <SurfaceRow
          icon={Brain}
          label="Biznes ma’lumoti"
          value={`${readyProducts} mahsulot · ${sourceCount} manba`}
          tone={readyProducts > 0 ? 'success' : 'warning'}
        />
        <SurfaceRow
          icon={ImageSquare}
          label="Media dalil"
          value={mediaProducts > 0 ? `${mediaProducts} rasmli mahsulot` : 'Rasmli dalil yo‘q'}
          tone={mediaProducts > 0 ? 'success' : 'default'}
        />
      </div>

      {conversation.hydration?.needed && (
        <div className="rounded-lg border border-warning/30 bg-warning/8 px-3 py-2.5">
          <div className="flex items-center gap-2 text-sm font-medium text-warning-foreground">
            <ClockCounterClockwise className="size-4" weight="thin" />
            Suhbat tarixi to‘liq emas
          </div>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            OQIM eski xabarlarni alohida tiklaydi. Javoblar faqat hozirgi kanonik xabarlarga tayangan holda ko‘rsatiladi.
          </p>
        </div>
      )}
    </section>
  )
}

function ReplyDecisionSection({
  reply,
  isThinking,
}: {
  reply: SellerAgentReply | null | undefined
  isThinking: boolean
}) {
  return (
    <section>
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <PaperPlaneTilt className="size-4 text-muted-foreground" weight="thin" />
          <h3 className="text-sm font-semibold tracking-tight">Javob qarori</h3>
        </div>
        {reply && (
          <Badge variant="outline" className="rounded-md">
            Javob-{reply.id}
          </Badge>
        )}
      </div>

      {reply ? (
        <SellerAgentReplyReviewCard reply={reply} mode="panel" />
      ) : isThinking ? (
        <div className="rounded-lg border border-border/70 bg-background p-4">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Sparkle className="size-4 animate-pulse text-muted-foreground" weight="thin" />
            Javob tayyorlanmoqda
          </div>
          <div className="mt-3 space-y-2">
            <div className="h-2.5 w-5/6 rounded bg-foreground/[0.06]" />
            <div className="h-2.5 w-2/3 rounded bg-foreground/[0.06]" />
          </div>
        </div>
      ) : (
        <div className="rounded-lg border border-dashed border-border/70 bg-background px-4 py-5">
          <div className="flex items-center gap-2 text-sm font-medium">
            <ShieldCheck className="size-4 text-muted-foreground" weight="thin" />
            Hozircha yuboriladigan javob yo‘q
          </div>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            Yangi mijoz xabari, qayta aloqa yoki action taklifi kelganda javob shu yerda chiqadi.
          </p>
        </div>
      )}
    </section>
  )
}

function ActionProposalsSection({
  proposals,
  isError,
  disabled,
  onProcess,
  onApprove,
  onReject,
  onExecute,
}: {
  proposals: CommercialActionProposal[]
  isError: boolean
  disabled: boolean
  onProcess: (proposalId: string) => void
  onApprove: (proposalId: string) => void
  onReject: (proposalId: string) => void
  onExecute: (proposalId: string) => void
}) {
  return (
    <section>
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Lightning className="size-4 text-muted-foreground" weight="thin" />
          <h3 className="text-sm font-semibold tracking-tight">{uz.workspaceUi.conversations.actionProposals}</h3>
        </div>
        <Badge variant="outline" className="rounded-md">
          {proposals.length}
        </Badge>
      </div>

      {isError ? (
        <div className="rounded-lg border border-destructive/30 bg-destructive/8 px-3 py-2.5">
          <div className="flex items-center gap-2 text-sm font-medium text-destructive-foreground">
            <WarningCircle className="size-4" weight="thin" />
            Takliflar yuklanmadi
          </div>
        </div>
      ) : proposals.length ? (
        <div className="space-y-2">
          {proposals.map((proposal) => (
            <ProposalCard
              key={proposal.proposal_id}
              proposal={proposal}
              disabled={disabled}
              onProcess={onProcess}
              onApprove={onApprove}
              onReject={onReject}
              onExecute={onExecute}
            />
          ))}
        </div>
      ) : (
        <div className="rounded-lg border border-border/70 bg-background px-4 py-4">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Package className="size-4 text-muted-foreground" weight="thin" />
            Hozircha taklif yo‘q
          </div>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            To‘lov, yetkazish, qayta aloqa yoki integratsiya ishi aniqlansa, tasdiqlash uchun shu yerda chiqadi.
          </p>
        </div>
      )}
    </section>
  )
}

export function SellerAgentSurface({ conversation }: SellerAgentSurfaceProps) {
  const reply = useLatestSellerAgentReply(conversation?.id)
  const actionInbox = useActionRuntimeInbox()
  const catalog = useBrainCatalog()
  const processProposal = useProcessActionProposal()
  const approveProposal = useApproveActionProposal()
  const rejectProposal = useRejectActionProposal()
  const executeProposal = useExecuteActionProposal()
  const shimmerIds = useShimmerState()

  if (!conversation) return <SurfaceEmpty />

  const proposals = conversationProposals(actionInbox.data?.items ?? [], conversation.id)
  const products = catalog.data?.products ?? []
  const isThinking = shimmerIds.has(conversation.id)
  const isActionPending = hasPendingMutations([
    processProposal,
    approveProposal,
    rejectProposal,
    executeProposal,
  ])

  return (
    <div className="flex h-full min-h-0 flex-col bg-foreground/[0.012]">
      <div className="shrink-0 border-b border-border/60 px-4 py-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="font-mono text-[10px] uppercase tracking-[0.24em] text-muted-foreground">
              {uz.workspaceUi.conversations.sellerAgent}
            </p>
            <h2 className="mt-1 truncate text-base font-semibold tracking-tight">
              {conversation.customer_name}
            </h2>
          </div>
          <Badge variant={nextActionReady(reply, conversation) ? 'success' : 'warning'} className="rounded-md">
            {nextActionReady(reply, conversation) ? 'Tayyor' : 'Tekshiruv'}
          </Badge>
        </div>
        <p className="mt-2 line-clamp-2 text-sm leading-6 text-muted-foreground">
          {nextActionText(reply, conversation)}
        </p>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4">
        <SurfaceContextSummary conversation={conversation} products={products} />
        <ReplyDecisionSection reply={reply} isThinking={isThinking} />
        <ActionProposalsSection
          proposals={proposals}
          isError={actionInbox.isError}
          disabled={isActionPending}
          onProcess={(proposalId) => processProposal.mutate(proposalId)}
          onApprove={(proposalId) => approveProposal.mutate(proposalId)}
          onReject={(proposalId) => rejectProposal.mutate({ proposalId })}
          onExecute={(proposalId) => executeProposal.mutate(proposalId)}
        />
      </div>
    </div>
  )
}
