import { useMemo, useState, type ReactNode } from 'react'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@/lib/api-client'
import { queryKeys } from '@/lib/query-keys'
import { useBusinessBrainFacts } from '@/hooks/use-business-brain'
import { BrainFactSurface } from '@/components/blocks/brain/brain-command-center'
import {
  ArrowClockwise as RefreshCwIcon,
  ArrowUpRight as ArrowUpRightIcon,
  ChartBar as ChartBarIcon,
  ChartLineUp as ChartNoAxesCombinedIcon,
  ClipboardText as ClipboardCheckIcon,
  Gauge as GaugeIcon,
  MagnifyingGlass as SearchIcon,
  Megaphone as MegaphoneIcon,
  Package as PackageIcon,
  SealCheck as BadgeCheckIcon,
  Sparkle as SparklesIcon,
  Truck as TruckIcon,
  Users as UsersRoundIcon,
  Warning as AlertTriangleIcon,
} from '@phosphor-icons/react'
import {
  useBIAnalyticsDashboard,
  useBIInvestigationMutation,
  usePromoterPlanMutation,
  usePromoterPolicy,
} from '@/hooks/use-bi-promoter'
import { useActionRuntimeInbox } from '@/hooks/use-action-runtime'
import { useCustomers } from '@/hooks/use-customers'
import { usePipeline } from '@/hooks/use-pipeline'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { uz } from '@/lib/uz'
import type {
  BIInsight,
  CommercialActionProposal,
  CrmPipelineProjection,
  Customer,
} from '@/lib/types'

const filters = ['overview', 'customers', 'opportunities', 'orders', 'tasks', 'followups', 'analytics'] as const
const filterLabels: Record<(typeof filters)[number], string> = {
  overview: 'Umumiy',
  customers: 'Mijozlar',
  opportunities: 'Imkoniyatlar',
  orders: 'Buyurtmalar',
  tasks: 'Vazifalar',
  followups: 'Qayta yozish',
  analytics: 'Tahlil',
}

const metrics = [
  { key: 'customer_count', label: uz.workspaceUi.intelligence.metrics.customers, icon: UsersRoundIcon },
  { key: 'opportunity_count', label: uz.workspaceUi.intelligence.metrics.opportunities, icon: GaugeIcon },
  { key: 'reply_needed_count', label: uz.workspaceUi.intelligence.metrics.replyNeeded, icon: SparklesIcon },
  { key: 'orders_count', label: uz.workspaceUi.intelligence.metrics.orders, icon: PackageIcon },
  { key: 'stalled_opportunity_count', label: uz.workspaceUi.intelligence.metrics.stalled, icon: AlertTriangleIcon },
] as const

const orderActionTypes = new Set([
  'send_payment_link',
  'check_payment',
  'create_delivery_order',
])

const statePlanes = [
  {
    label: uz.workspaceUi.intelligence.planes.customer,
    icon: UsersRoundIcon,
    glyphTone: 'bg-blue-500/10 text-blue-600 dark:text-blue-400',
    description: 'Mijoz kim, qayerdan yozgan, javob kerakmi va qaysi bosqichda.',
    owns: ['bosqich', 'javob kerak', 'kontakt'],
  },
  {
    label: uz.workspaceUi.intelligence.planes.opportunity,
    icon: GaugeIcon,
    glyphTone: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400',
    description: 'Mijoz nimani xohlayapti, nimaga ikkilanmoqda va keyingi savdo qadami.',
    owns: ['qiziqish', 'e’tiroz', 'keyingi qadam'],
  },
  {
    label: uz.workspaceUi.intelligence.planes.order,
    icon: BadgeCheckIcon,
    glyphTone: 'bg-violet-500/10 text-violet-700 dark:text-violet-400',
    description: 'Buyurtma, to‘lov tasdig‘i, summa va ehtiyotkor tekshiruv.',
    owns: ['buyurtma', 'to‘lov', 'tekshiruv'],
  },
  {
    label: uz.workspaceUi.intelligence.planes.delivery,
    icon: TruckIcon,
    glyphTone: 'bg-sky-500/10 text-sky-700 dark:text-sky-400',
    description: 'Manzil, yetkazish va’dasi va yetkazishdagi ochiq ishlar.',
    owns: ['manzil', 'kuryer', 'muddat'],
  },
  {
    label: uz.workspaceUi.intelligence.planes.task,
    icon: ClipboardCheckIcon,
    glyphTone: 'bg-amber-500/10 text-amber-700 dark:text-amber-400',
    description: 'Biznes mijozga va’da qilgan va bajarishi kerak bo‘lgan ishlar.',
    owns: ['qo‘ng‘iroq', 'ombor', 'hisob'],
  },
  {
    label: uz.workspaceUi.intelligence.planes.followup,
    icon: RefreshCwIcon,
    glyphTone: 'bg-rose-500/10 text-rose-700 dark:text-rose-400',
    description: 'Mijoz sovib qolsa yoki javob bermasa, sotuvni qayta boshlash.',
    owns: ['eslatish', 'qayta jalb', 'sovigan mijoz'],
  },
] as const

function freshnessTone(freshness?: string) {
  if (freshness === 'projection_current') return 'success'
  if (freshness === 'projection_partial') return 'warning'
  if (freshness === 'degraded') return 'error'
  return 'outline'
}

function freshnessLabel(freshness?: string) {
  if (freshness === 'projection_current') return 'yangilangan'
  if (freshness === 'projection_partial') return 'qisman'
  if (freshness === 'degraded') return 'tekshirish kerak'
  return 'yuklanmoqda'
}

function displayValue(value: unknown) {
  if (value == null) return '0'
  if (typeof value === 'number') return value.toLocaleString()
  return String(value)
}

function readableCode(value: string) {
  const known: Record<string, string> = {
    planned: 'reja tayyor',
    blocked: 'to‘siq bor',
    high: 'yuqori',
    medium: 'o‘rta',
    low: 'past',
    customer_went_cold_after_price: 'Narxdan keyin mijoz jim qoldi',
    sales_followup: 'Qayta yozish',
    promoter_outreach: 'Qayta jalb qilish',
    send_payment_link: 'to‘lov havolasi',
    check_payment: 'to‘lov tekshiruvi',
    create_delivery_order: 'yetkazish',
    create_business_task: 'vazifa',
    schedule_sales_follow_up: 'qayta yozish',
  }
  return known[value] ?? value.replaceAll('_', ' ')
}

function readableSeverity(value: string) {
  if (value === 'high') return 'yuqori'
  if (value === 'medium') return 'o‘rta'
  if (value === 'low') return 'past'
  return readableCode(value)
}

// Phase 4 sub-tab wrapper. Customer state stays under ?tab=customer; the
// new locked Intelligence content (Agents / Skills / Rules) lives at the
// other tabs.
export function OQIMIntelligencePage() {
  const search = useSearch({ strict: false }) as { tab?: string }
  const navigate = useNavigate()
  const active: IntelligenceTab = (() => {
    if (search.tab === 'skills') return 'skills'
    if (search.tab === 'rules') return 'rules'
    if (search.tab === 'customer') return 'customer'
    return 'agents'
  })()
  const select = (tab: IntelligenceTab) =>
    navigate({
      to: '/intelligence',
      search: tab === 'agents' ? {} : { tab },
      replace: true,
    })

  return (
    <div className="flex h-full min-h-0 flex-col bg-background text-foreground">
      <header className="border-b border-border/60 px-6 py-4">
        <div className="font-mono text-[9px] uppercase tracking-[0.22em] text-muted-foreground">
          OQIM intelligence
        </div>
        <h1 className="mt-0.5 text-base font-semibold tracking-tight">Aql</h1>
        <nav className="mt-3 flex flex-wrap gap-0.5">
          {([
            { id: 'agents', label: 'Agentlar' },
            { id: 'skills', label: 'Ko‘nikmalar' },
            { id: 'rules', label: 'Qoidalar' },
            { id: 'customer', label: 'Mijoz holati' },
          ] as { id: IntelligenceTab; label: string }[]).map((tab) => {
            const selected = active === tab.id
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => select(tab.id)}
                className={
                  'rounded-md px-2.5 py-1 text-xs transition-colors ' +
                  (selected
                    ? 'bg-foreground/[0.06] text-foreground'
                    : 'text-muted-foreground hover:bg-foreground/[0.03] hover:text-foreground')
                }
              >
                {tab.label}
              </button>
            )
          })}
        </nav>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {active === 'agents' && <AgentsSubTab />}
        {active === 'skills' && <SkillsSubTab />}
        {active === 'rules' && <RulesSubTab />}
        {active === 'customer' && <CustomerStateDashboard />}
      </div>
    </div>
  )
}

type IntelligenceTab = 'agents' | 'skills' | 'rules' | 'customer'

function CustomerStateDashboard() {
  const [activeFilter, setActiveFilter] = useState<(typeof filters)[number]>('overview')
  const [query, setQuery] = useState('')
  const dashboard = useBIAnalyticsDashboard()
  const policy = usePromoterPolicy()
  const actionInbox = useActionRuntimeInbox()
  const investigation = useBIInvestigationMutation()
  const campaign = usePromoterPlanMutation()
  const customers = useCustomers()
  const pipeline = usePipeline()
  const summary = dashboard.data?.summary ?? {}
  const insights = dashboard.data?.insights ?? []
  const productRows = dashboard.data?.breakdowns?.products ?? []
  const channelRows = dashboard.data?.breakdowns?.channels ?? []
  const orderActions = useMemo(
    () => (actionInbox.data?.items ?? []).filter((item) => isOrderAction(item.action_type)),
    [actionInbox.data?.items],
  )
  const businessTasks = useMemo(
    () => (actionInbox.data?.items ?? []).filter((item) => item.action_type === 'create_business_task'),
    [actionInbox.data?.items],
  )
  const followUps = useMemo(
    () => (actionInbox.data?.items ?? []).filter((item) => item.action_type === 'schedule_sales_follow_up'),
    [actionInbox.data?.items],
  )
  const pendingActions = actionInbox.data?.items?.filter((item) => item.requires_approval).length ?? 0

  return (
    <div className="h-full overflow-y-auto bg-background text-foreground">
      <div className="mx-auto max-w-5xl px-8 py-12">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="font-mono text-[10px] text-muted-foreground uppercase tracking-[0.3em]">
              {uz.workspaceUi.intelligence.eyebrow}
            </div>
            <h1 className="mt-1 font-heading text-3xl">{uz.workspaceUi.intelligence.title}</h1>
            <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
              {uz.workspaceUi.intelligence.description}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              loading={investigation.isPending}
              onClick={() => investigation.mutate()}
            >
              <ChartBarIcon />
              {uz.workspaceUi.intelligence.investigate}
            </Button>
            <Button
              type="button"
              loading={campaign.isPending}
              onClick={() => campaign.mutate()}
            >
              <MegaphoneIcon />
              {uz.workspaceUi.intelligence.planCampaign}
            </Button>
          </div>
        </div>

        <div className="mt-6 flex flex-col gap-3 border-y border-border/60 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-1 items-center gap-2 rounded-md border border-border/70 bg-background/40 px-3">
            <SearchIcon className="size-4 text-muted-foreground" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="h-9 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground/60"
              placeholder="Mijoz, buyurtma yoki qayta yozishni qidirish"
            />
          </div>
          <div className="flex flex-wrap gap-1.5">
            {filters.map((filter) => (
              <button
                type="button"
                key={filter}
                onClick={() => setActiveFilter(filter)}
                className={`rounded-md px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.2em] transition-colors ${
                  activeFilter === filter
                    ? 'bg-foreground text-background'
                    : 'border border-border/60 text-muted-foreground hover:text-foreground'
                }`}
              >
                {filterLabels[filter]}
              </button>
            ))}
          </div>
        </div>

        <div className="mt-5 flex flex-wrap gap-2">
          <Badge variant={freshnessTone(dashboard.data?.freshness)} className="rounded-md">
            {freshnessLabel(dashboard.data?.freshness)}
          </Badge>
          <Badge variant={pendingActions > 0 ? 'warning' : 'outline'} className="rounded-md">
            {pendingActions} ruxsat kutilmoqda
          </Badge>
          <Button render={<Link to="/actions" />} variant="outline" size="sm">
            {uz.workspaceUi.intelligence.actionInbox}
            <ArrowUpRightIcon />
          </Button>
        </div>

        {dashboard.error && (
          <Alert variant="destructive" className="mt-5">
            <AlertTriangleIcon className="size-4" />
            <AlertTitle>Mijozlar ma’lumoti yuklanmadi</AlertTitle>
            <AlertDescription>
              Hozir faqat sahifa tuzilmasi ko‘rinadi. Ma’lumot kelganda mijozlar, buyurtmalar va xulosalar shu yerda chiqadi.
            </AlertDescription>
          </Alert>
        )}

        <div className="mt-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          {metrics.map((metric) => (
            <Card key={metric.key} size="sm" className="rounded-xl border-border/60 bg-background/40">
              <CardHeader className="gap-1">
                <CardTitle className="flex items-center gap-2 text-sm">
                  <metric.icon className="size-4 text-muted-foreground" weight="thin" />
                  {metric.label}
                </CardTitle>
                <CardAction>
                  <Badge variant={freshnessTone(dashboard.data?.freshness)} className="rounded-md">
                    live
                  </Badge>
                </CardAction>
              </CardHeader>
              <CardContent>
                {dashboard.isLoading ? (
                  <Skeleton className="h-8 w-20" />
                ) : (
                  <p className="text-2xl font-semibold tabular-nums tracking-tight">
                    {displayValue(summary[metric.key])}
                  </p>
                )}
              </CardContent>
            </Card>
          ))}
        </div>

        <FocusedCrmPanel
          activeFilter={activeFilter}
          query={query}
          customers={customers.data?.customers ?? []}
          customersLoading={customers.isLoading}
          pipeline={pipeline.data}
          pipelineLoading={pipeline.isLoading}
          orders={orderActions}
          ordersLoading={actionInbox.isLoading}
          tasks={businessTasks}
          tasksLoading={actionInbox.isLoading}
          followUps={followUps}
          followUpsLoading={actionInbox.isLoading}
          insights={insights}
          insightsLoading={dashboard.isLoading}
        />

        <Card className="mt-6 rounded-xl border-border/60 bg-background/40">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ChartNoAxesCombinedIcon className="size-5 text-muted-foreground" weight="thin" />
              {uz.workspaceUi.intelligence.stateMap}
            </CardTitle>
            <CardDescription>
              Kompaniya ma’lumoti, mijoz holati va savdo ishlari shu yerda alohida ko‘rinadi.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid gap-3 md:grid-cols-2">
              {statePlanes.map((plane) => (
                <div
                  key={plane.label}
                  className="group flex flex-col gap-3 rounded-xl border border-border/60 bg-background/40 p-4 transition-colors hover:border-foreground/30"
                >
                  <div className="flex items-start gap-3">
                    <div className={`flex size-10 shrink-0 items-center justify-center rounded-lg ${plane.glyphTone}`}>
                      <plane.icon className="size-4" />
                    </div>
                    <div className="min-w-0">
                      <p className="text-sm font-medium">{plane.label}</p>
                      <p className="mt-1 text-xs leading-5 text-muted-foreground">{plane.description}</p>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {plane.owns.map((item) => (
                      <Badge key={item} variant="outline" className="rounded-md">
                        {item}
                      </Badge>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        <div className="mt-6 grid gap-4 xl:grid-cols-[1fr_0.8fr]">
          <Card className="rounded-xl border-border/60 bg-background/40">
            <CardHeader>
            <CardTitle>{uz.workspaceUi.intelligence.insights}</CardTitle>
            <CardDescription>
                {dashboard.data?.source_refs?.length ?? 0} ta suhbat yoki buyurtma dalili ishlatilgan.
            </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {dashboard.isLoading ? (
                <Skeleton className="h-40 w-full" />
              ) : insights.length === 0 ? (
                <p className="text-sm text-muted-foreground">{uz.workspaceUi.intelligence.emptyInsights}</p>
              ) : (
                insights.slice(0, 6).map((insight) => (
                  <div key={insight.insight_id} className="rounded-lg border border-border/60 px-4 py-3">
                    <div className="flex items-center justify-between gap-3">
                      <p className="text-sm font-medium">{insight.insight_type.replaceAll('_', ' ')}</p>
                      <Badge variant={freshnessTone(insight.freshness)} className="rounded-md">
                        {Math.round(insight.confidence * 100)}%
                      </Badge>
                    </div>
                    <p className="mt-2 text-sm leading-6 text-muted-foreground">{insight.answer}</p>
                    <p className="mt-2 text-xs text-muted-foreground">
                      Dalil: {insight.source_refs.length || 0} ta manba
                    </p>
                  </div>
                ))
              )}
            </CardContent>
          </Card>

          <div className="grid gap-4">
            <Card className="rounded-xl border-border/60 bg-background/40">
            <CardHeader>
              <CardTitle>{uz.workspaceUi.intelligence.boundary}</CardTitle>
              <CardDescription>
                  Ishonch past yoki xavfli holatda OQIM avval sotuvchidan ruxsat so‘raydi.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <RuntimeRow
                  label="Qayta jalb qilish"
                  value={policy.data?.enabled ? 'yoqilgan' : 'o‘chirilgan'}
                  tone={policy.data?.enabled ? 'success' : 'outline'}
              />
              <RuntimeRow
                  label="Sotuvchi ruxsati"
                  value={policy.data?.approved ? 'bor' : 'kerak'}
                  tone={policy.data?.approved ? 'success' : 'warning'}
              />
              <RuntimeRow
                  label="Kutayotgan ruxsatlar"
                  value={String(pendingActions)}
                  tone={pendingActions > 0 ? 'warning' : 'outline'}
              />
            </CardContent>
          </Card>
            <BreakdownCard title={uz.workspaceUi.intelligence.productDemand} rows={productRows} empty="Mahsulot bo‘yicha hali yetarli savdo signali yo‘q." />
            <BreakdownCard title={uz.workspaceUi.intelligence.channelHealth} rows={channelRows} empty="Kanal bo‘yicha hali yetarli savdo signali yo‘q." />
          </div>
        </div>

        {(investigation.data || campaign.data) && (
          <div className="mt-6 grid gap-4 xl:grid-cols-2">
            {investigation.data && (
              <Card className="rounded-xl border-border/60 bg-background/40">
                <CardHeader>
                  <CardTitle>Tekshiruv natijasi</CardTitle>
                  <CardDescription>
                    {investigation.data.findings.length} muammo, {investigation.data.fix_candidates.length} tuzatish taklifi
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  {investigation.data.findings.slice(0, 5).map((finding) => (
                    <div key={finding.finding_ref} className="rounded-lg border border-border/60 px-4 py-3">
                      <div className="flex items-center justify-between gap-3">
                        <p className="text-sm font-medium">{finding.title}</p>
                        <Badge variant={finding.severity === 'high' ? 'error' : 'warning'} className="rounded-md">
                          {readableSeverity(finding.severity)}
                        </Badge>
                      </div>
                      <p className="mt-2 text-sm text-muted-foreground">{finding.summary}</p>
                    </div>
                  ))}
                </CardContent>
              </Card>
            )}

            {campaign.data && (
              <Card className="rounded-xl border-border/60 bg-background/40">
                <CardHeader>
                  <CardTitle>Kampaniya rejasi</CardTitle>
                  <CardDescription>
                    {campaign.data.proposals.length} taklif, {campaign.data.blocked_reasons.length} to‘siq
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  <Badge variant={campaign.data.status === 'planned' ? 'success' : 'warning'} className="rounded-md">
                    {readableCode(campaign.data.status)}
                  </Badge>
                  {campaign.data.blocked_reasons.map((reason) => (
                    <p key={reason} className="text-sm text-muted-foreground">{reason}</p>
                  ))}
                  {campaign.data.proposals.slice(0, 5).map((proposal) => (
                    <div key={proposal.proposal_id} className="rounded-lg border border-border/60 px-4 py-3">
                      <p className="text-sm font-medium">{readableCode(proposal.action_type)}</p>
                      <p className="mt-1 text-sm text-muted-foreground">{readableCode(proposal.reason_code)}</p>
                    </div>
                  ))}
                </CardContent>
              </Card>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function RuntimeRow({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone: 'success' | 'warning' | 'outline'
}) {
  return (
    <div className="flex items-center justify-between rounded-lg border border-border/60 px-3 py-2 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <Badge variant={tone} className="rounded-md">
        {value}
      </Badge>
    </div>
  )
}

function includesQuery(values: Array<string | number | null | undefined>, query: string) {
  const normalized = query.trim().toLowerCase()
  if (!normalized) return true
  return values.some((value) => String(value ?? '').toLowerCase().includes(normalized))
}

function FocusedCrmPanel({
  activeFilter,
  query,
  customers,
  customersLoading,
  pipeline,
  pipelineLoading,
  orders,
  ordersLoading,
  tasks,
  tasksLoading,
  followUps,
  followUpsLoading,
  insights,
  insightsLoading,
}: {
  activeFilter: (typeof filters)[number]
  query: string
  customers: Customer[]
  customersLoading: boolean
  pipeline: CrmPipelineProjection | undefined
  pipelineLoading: boolean
  orders: CommercialActionProposal[]
  ordersLoading: boolean
  tasks: CommercialActionProposal[]
  tasksLoading: boolean
  followUps: CommercialActionProposal[]
  followUpsLoading: boolean
  insights: BIInsight[]
  insightsLoading: boolean
}) {
  if (activeFilter === 'overview') return null

  if (activeFilter === 'customers') {
    const rows = customers.filter((customer) =>
      includesQuery([customer.display_name, customer.phone_number, customer.stage, customer.crm_stage?.stage], query),
    )
    return (
      <LiveListCard
        title="Mijozlar"
        description={`${customers.length} ta mijoz`}
        loading={customersLoading}
        empty="Bu qidiruvga mos mijoz topilmadi."
      >
        {rows.slice(0, 8).map((customer) => (
          <Link
            key={customer.id}
            to={customer.latest_conversation_id ? '/conversations/$conversationId' : '/conversations'}
            params={customer.latest_conversation_id ? { conversationId: String(customer.latest_conversation_id) } : undefined}
            className="grid gap-1 rounded-lg border border-border/60 px-3 py-2 text-sm hover:border-foreground/30"
          >
            <div className="flex items-center justify-between gap-3">
              <span className="truncate font-medium">{customer.display_name}</span>
              <Badge variant={customer.needs_followup ? 'warning' : 'outline'} className="rounded-md">
                {customer.crm_stage?.stage ?? customer.stage ?? 'noma’lum'}
              </Badge>
            </div>
            <span className="truncate text-xs text-muted-foreground">
              {customer.latest_conversation_tail?.latest_message_text ?? customer.ai_brief ?? customer.phone_number}
            </span>
          </Link>
        ))}
      </LiveListCard>
    )
  }

  if (activeFilter === 'opportunities') {
    const stages = pipeline?.stages ?? []
    return (
      <LiveListCard
        title="Imkoniyatlar"
        description={`${pipeline?.total ?? 0} ta faol savdo suhbati`}
        loading={pipelineLoading}
        empty="Hali faol imkoniyat kartalari yo‘q."
      >
        {stages.flatMap((stage) =>
          stage.cards
            .filter((card) => includesQuery([card.customer_name, stage.stage, card.last_message_text], query))
            .slice(0, 4)
            .map((card) => (
              <Link
                key={`${stage.stage}-${card.conversation_id}`}
                to="/conversations/$conversationId"
                params={{ conversationId: String(card.conversation_id) }}
                className="grid gap-1 rounded-lg border border-border/60 px-3 py-2 text-sm hover:border-foreground/30"
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="truncate font-medium">{card.customer_name ?? `Suhbat ${card.conversation_id}`}</span>
                  <Badge variant="outline" className="rounded-md">
                    {(uz.pipeline.stages as Record<string, string>)[stage.stage] ?? stage.stage}
                  </Badge>
                </div>
                <span className="truncate text-xs text-muted-foreground">{card.last_message_text ?? 'Xabar ko‘rinmayapti'}</span>
              </Link>
            )),
        )}
      </LiveListCard>
    )
  }

  if (activeFilter === 'orders') {
    const rows = orders.filter((order) =>
      includesQuery([
        actionProposalCustomer(order),
        actionProposalTitle(order),
        order.action_type,
        order.reason_code,
        order.lifecycle_state,
      ], query),
    )
    return (
      <LiveListCard
        title="Buyurtmalar"
        description={`${orders.length} ta buyurtma va to‘lov ishi`}
        loading={ordersLoading}
        empty="Bu qidiruvga mos buyurtma ishi topilmadi."
      >
        {rows.slice(0, 8).map((order) => (
          <Link
            key={order.proposal_id}
            to="/conversations/$conversationId"
            params={{ conversationId: String(order.conversation_id) }}
            className="grid gap-1 rounded-lg border border-border/60 px-3 py-2 text-sm hover:border-foreground/30"
          >
            <div className="flex items-center justify-between gap-3">
              <span className="truncate font-medium">{actionProposalTitle(order)}</span>
              <Badge variant={order.requires_approval ? 'warning' : 'outline'} className="rounded-md">
                {readableCode(order.action_type)}
              </Badge>
            </div>
            <span className="truncate text-xs text-muted-foreground">
              {actionProposalCustomer(order)} · {readableCode(order.reason_code)}
            </span>
          </Link>
        ))}
      </LiveListCard>
    )
  }

  if (activeFilter === 'tasks') {
    const rows = tasks.filter((task) =>
      includesQuery([
        actionProposalCustomer(task),
        actionProposalTitle(task),
        task.action_type,
        task.reason_code,
        task.lifecycle_state,
      ], query),
    )
    return (
      <LiveListCard
        title="Biznes vazifalar"
        description={`${tasks.length} ta bajarilishi kerak ish`}
        loading={tasksLoading}
        empty="Bu qidiruvga mos vazifa topilmadi."
      >
        {rows.slice(0, 8).map((task) => (
          <Link
            key={task.proposal_id}
            to="/conversations/$conversationId"
            params={{ conversationId: String(task.conversation_id) }}
            className="grid gap-1 rounded-lg border border-border/60 px-3 py-2 text-sm hover:border-foreground/30"
          >
            <div className="flex items-center justify-between gap-3">
              <span className="truncate font-medium">{actionProposalTitle(task)}</span>
              <Badge variant={task.priority === 'high' ? 'warning' : 'outline'} className="rounded-md">
                {readableCode(task.lifecycle_state)}
              </Badge>
            </div>
            <span className="truncate text-xs text-muted-foreground">
              {actionProposalCustomer(task)} · {readableCode(task.reason_code)}
            </span>
          </Link>
        ))}
      </LiveListCard>
    )
  }

  if (activeFilter === 'followups') {
    const rows = followUps.filter((followUp) =>
      includesQuery([
        actionProposalCustomer(followUp),
        actionProposalTitle(followUp),
        followUp.reason_code,
        followUp.lifecycle_state,
      ], query),
    )
    return (
      <LiveListCard
        title="Qayta yozish"
        description={`${followUps.length} ta mijozga yana yozish kerak`}
        loading={followUpsLoading}
        empty="Bu qidiruvga mos qayta yozish topilmadi."
      >
        {rows.slice(0, 8).map((followUp) => (
          <Link
            key={followUp.proposal_id}
            to="/conversations/$conversationId"
            params={{ conversationId: String(followUp.conversation_id) }}
            className="grid gap-1 rounded-lg border border-border/60 px-3 py-2 text-sm hover:border-foreground/30"
          >
            <div className="flex items-center justify-between gap-3">
              <span className="truncate font-medium">{actionProposalTitle(followUp)}</span>
              <Badge variant={followUp.priority === 'high' ? 'warning' : 'outline'} className="rounded-md">
                {followUp.lifecycle_state}
              </Badge>
            </div>
            <span className="truncate text-xs text-muted-foreground">
              {actionProposalCustomer(followUp)} · {readableCode(followUp.reason_code)}
            </span>
          </Link>
        ))}
      </LiveListCard>
    )
  }

  const rows = insights.filter((insight) =>
    includesQuery([insight.insight_type, insight.answer, insight.freshness], query),
  )
  return (
    <LiveListCard
      title="Tahlil"
      description={`${insights.length} ta savdo xulosasi`}
      loading={insightsLoading}
      empty="Bu qidiruvga mos xulosa topilmadi."
    >
      {rows.slice(0, 8).map((insight) => (
        <div key={insight.insight_id} className="grid gap-1 rounded-lg border border-border/60 px-3 py-2 text-sm">
          <div className="flex items-center justify-between gap-3">
            <span className="truncate font-medium">{insight.insight_type.replaceAll('_', ' ')}</span>
            <Badge variant={freshnessTone(insight.freshness)} className="rounded-md">
              {Math.round(insight.confidence * 100)}%
            </Badge>
          </div>
          <span className="line-clamp-2 text-xs text-muted-foreground">{insight.answer}</span>
        </div>
      ))}
    </LiveListCard>
  )
}

function isOrderAction(actionType: string) {
  return orderActionTypes.has(actionType)
}

function actionProposalTitle(proposal: CommercialActionProposal) {
  const candidate = objectValue(proposal.payload.candidate_value)
  return stringValue(candidate.title)
    || stringValue(candidate.task_title)
    || stringValue(candidate.summary)
    || stringValue(candidate.state_type)
    || stringValue(proposal.payload.title)
    || readableCode(proposal.action_type)
    || readableCode(proposal.reason_code)
}

function actionProposalCustomer(proposal: CommercialActionProposal) {
  return stringValue(proposal.payload.customer_name)
    || stringValue(proposal.payload.customer_display_name)
    || (proposal.customer_id ? `Mijoz #${proposal.customer_id}` : 'Mijoz')
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function stringValue(value: unknown) {
  return typeof value === 'string' && value.trim() ? value.trim() : ''
}

function LiveListCard({
  title,
  description,
  loading,
  empty,
  children,
}: {
  title: string
  description: string
  loading: boolean
  empty: string
  children: ReactNode
}) {
  const childArray = Array.isArray(children) ? children : [children]
  const hasRows = childArray.some(Boolean)
  return (
    <Card className="mt-6 rounded-xl border-border/60 bg-background/40">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-2">
        {loading ? (
          <>
            <Skeleton className="h-14 rounded-lg" />
            <Skeleton className="h-14 rounded-lg" />
            <Skeleton className="h-14 rounded-lg" />
          </>
        ) : hasRows ? (
          children
        ) : (
          <p className="text-sm text-muted-foreground">{empty}</p>
        )}
      </CardContent>
    </Card>
  )
}

function BreakdownCard({
  title,
  rows,
  empty,
}: {
  title: string
  rows: Record<string, unknown>[]
  empty: string
}) {
  return (
    <Card className="rounded-xl border-border/60 bg-background/40">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{rows.length} ta qator</CardDescription>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">{empty}</p>
        ) : (
          <div className="space-y-2">
            {rows.slice(0, 6).map((row) => (
              <div
                key={String(row.key)}
                className={cn(
                  'grid grid-cols-[minmax(0,1fr)_auto_auto] gap-3 rounded-lg border border-border/60 px-3 py-2 text-sm',
                )}
              >
                <span className="truncate">{String(row.key)}</span>
                <span className="tabular-nums text-muted-foreground">{displayValue(row.orders)} buyurtma</span>
                <span className="tabular-nums text-muted-foreground">{displayValue(row.opportunities)} imkoniyat</span>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Phase 4 sub-tab implementations: Agents / Skills / Rules
// ---------------------------------------------------------------------------

interface IntelAgentRow {
  id: number
  name: string
  agent_type: string
  trust_mode: string
  is_active: boolean
  skill_count: number
}

interface IntelSkillRow {
  id: number
  workspace_id: number
  agent_id: number | null
  slug: string
  name: string
  description: string
  tools: string[]
  enabled: boolean
}

function useIntelligenceAgents() {
  return useQuery({
    queryKey: queryKeys.agents.all,
    queryFn: () =>
      api.get<{ schema_version: 'intelligence_agents.v1'; items: IntelAgentRow[] }>(
        '/api/intelligence/agents',
      ),
    staleTime: 30_000,
  })
}

function useIntelligenceSkills() {
  return useQuery({
    queryKey: ['intelligence', 'skills'] as const,
    queryFn: () =>
      api.get<{ schema_version: 'intelligence_skills.v1'; items: IntelSkillRow[] }>(
        '/api/intelligence/skills',
      ),
    staleTime: 30_000,
  })
}

function AgentsSubTab() {
  const agents = useIntelligenceAgents()
  if (agents.isLoading) {
    return <div className="px-6 py-10 text-sm text-muted-foreground">Yuklanmoqda…</div>
  }
  const items = agents.data?.items ?? []
  if (items.length === 0) {
    return (
      <div className="px-6 py-12 text-center">
        <p className="text-sm text-muted-foreground">
          Hali agent yo‘q. Onboarding davomida sotuvchi va yordamchi agentlar yaratiladi.
        </p>
      </div>
    )
  }
  return (
    <div className="grid gap-2 px-6 py-5">
      {items.map((agent) => (
        <Link
          key={agent.id}
          to="/agents/$agentId"
          params={{ agentId: String(agent.id) }}
          className="flex items-center justify-between gap-3 rounded-lg border border-border bg-background px-4 py-3 transition-colors hover:bg-foreground/[0.03]"
        >
          <div className="min-w-0">
            <div className="truncate text-sm font-medium">{agent.name}</div>
            <div className="mt-0.5 truncate text-xs text-muted-foreground">
              {agent.agent_type} · {agent.skill_count} ta ko‘nikma
            </div>
          </div>
          <span
            className={
              'shrink-0 rounded-full border border-border bg-background px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider ' +
              (agent.is_active ? 'text-foreground' : 'text-muted-foreground')
            }
          >
            {agent.trust_mode}
          </span>
        </Link>
      ))}
    </div>
  )
}

function SkillsSubTab() {
  const skills = useIntelligenceSkills()
  if (skills.isLoading) {
    return <div className="px-6 py-10 text-sm text-muted-foreground">Yuklanmoqda…</div>
  }
  const items = skills.data?.items ?? []
  if (items.length === 0) {
    return (
      <div className="px-6 py-12 text-center">
        <p className="text-sm text-muted-foreground">
          Workspace ko‘nikmalari hozircha yo‘q. AgentSkill yozuvlari ko‘paygach shu yerda chiqadi.
        </p>
      </div>
    )
  }
  return (
    <div className="grid gap-2 px-6 py-5">
      {items.map((skill) => (
        <div
          key={skill.id}
          className="rounded-lg border border-border bg-background px-4 py-3"
        >
          <div className="flex items-baseline justify-between gap-3">
            <div className="min-w-0">
              <div className="truncate text-sm font-medium">{skill.name}</div>
              <div className="mt-0.5 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                {skill.slug}
              </div>
            </div>
            <span
              className={
                'shrink-0 rounded-full border border-border bg-background px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider ' +
                (skill.enabled ? 'text-foreground' : 'text-muted-foreground')
              }
            >
              {skill.enabled ? 'yoqilgan' : 'o‘chirilgan'}
            </span>
          </div>
          {skill.description ? (
            <p className="mt-2 text-xs leading-5 text-muted-foreground">{skill.description}</p>
          ) : null}
        </div>
      ))}
    </div>
  )
}

function RulesSubTab() {
  const facts = useBusinessBrainFacts()
  return (
    <div className="px-6 py-5">
      <BrainFactSurface
        facts={facts.data?.items ?? []}
        surface="rules"
        title="Qoidalar"
        description="Avtopilot, support, yetkazish, qaytarish, handoff va sotuvchi chegaralari shu yerda ko‘rinadi."
        loading={facts.isLoading}
        actionBusy={false}
        onReviewAction={() => toast.info('Qoidani tahrirlash agent detalida yiqiladi.')}
        onManualUpdate={() => toast.info('Qoidani tahrirlash agent detalida yiqiladi.')}
      />
    </div>
  )
}
