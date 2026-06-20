import { useState } from 'react'
import { Link } from '@tanstack/react-router'
import {
  ArrowUpRight as ArrowUpRightIcon,
  Check as CheckIcon,
  Clock as ClockIcon,
  MagnifyingGlass as SearchIcon,
  ShieldCheck as ShieldCheckIcon,
  Warning as AlertTriangleIcon,
} from '@phosphor-icons/react'
import { useActionRuntimePolicy } from '@/hooks/use-action-runtime'
import { usePromoterPolicy } from '@/hooks/use-bi-promoter'
import { useLlmPolicies } from '@/hooks/use-llm-policies'
import { useSellerAgentRuntime } from '@/hooks/use-seller-agent-runtime'
import { useTelegramAuthDiagnostics } from '@/hooks/use-telegram-auth-diagnostics'
import { useTelegramConnectionStatus } from '@/hooks/use-telegram-connection-status'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useAuth } from '@/lib/auth-context'
import type { TelegramAuthAttemptDiagnostic } from '@/lib/types'

type RuntimeState = 'ready' | 'warning' | 'blocked' | 'loading' | 'gated'

interface RuntimeCard {
  id: string
  name: string
  category: string
  description: string
  state: RuntimeState
  stateLabel: string
  glyph: string
  glyphTone: string
  metrics: { label: string; value: string }[]
  href?: string
  cta: string
  loading?: boolean
}

const FILTERS = ['All', 'Ready', 'Warning', 'Blocked', 'Gated', 'AI', 'Runtime'] as const
const FILTER_LABELS: Record<(typeof FILTERS)[number], string> = {
  All: 'Hammasi',
  Ready: 'Tayyor',
  Warning: 'Tekshiring',
  Blocked: 'To‘siq',
  Gated: 'Keyinroq',
  AI: 'AI',
  Runtime: 'Avtopilot',
}

function stateTone(state: RuntimeState) {
  if (state === 'ready') return 'success'
  if (state === 'warning') return 'warning'
  if (state === 'blocked') return 'error'
  return 'outline'
}

function yesNo(value: boolean | undefined) {
  if (value == null) return 'noma’lum'
  return value ? 'ha' : 'yo‘q'
}

function percent(value: number | undefined) {
  if (value == null) return 'noma’lum'
  return `${Math.round(value * 100)}%`
}

function matchesFilter(card: RuntimeCard, filter: (typeof FILTERS)[number]) {
  if (filter === 'All') return true
  if (filter === 'Ready') return card.state === 'ready'
  if (filter === 'Warning') return card.state === 'warning'
  if (filter === 'Blocked') return card.state === 'blocked'
  if (filter === 'Gated') return card.state === 'gated'
  if (filter === 'AI') return card.category === 'AI'
  if (filter === 'Runtime') return card.category === 'Avtopilot' || card.category === 'Javoblar'
  return true
}

export function SettingsPage() {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>('All')
  const [query, setQuery] = useState('')
  const { session, user } = useAuth()
  const canInspectFounderRuntime = Boolean(user?.is_founder)
  const telegram = useTelegramConnectionStatus()
  const llm = useLlmPolicies({ enabled: canInspectFounderRuntime })
  const sellerAgentRuntime = useSellerAgentRuntime({ enabled: canInspectFounderRuntime })
  const telegramAuthDiagnostics = useTelegramAuthDiagnostics({ enabled: canInspectFounderRuntime })
  const actionPolicy = useActionRuntimePolicy()
  const promoterPolicy = usePromoterPolicy()

  const telegramProjection = session?.integrations.find((item) => item.provider === 'telegram_personal')
  const telegramState: RuntimeState = telegram.isLoading
    ? 'loading'
    : telegram.data?.state === 'connected' || telegramProjection?.durable_connected
      ? 'ready'
      : telegram.data?.needsReconnect || telegramProjection?.needs_reconnect
        ? 'blocked'
        : telegram.data?.state === 'degraded'
          ? 'warning'
          : 'blocked'

  const llmState: RuntimeState = llm.isLoading
    ? 'loading'
    : !canInspectFounderRuntime
      ? 'ready'
    : llm.error
      ? 'blocked'
      : (llm.data?.tasks?.length ?? 0) > 0
        ? 'ready'
        : 'warning'

  const sellerAgentState: RuntimeState = sellerAgentRuntime.isLoading
    ? 'loading'
    : !canInspectFounderRuntime
      ? 'ready'
    : sellerAgentRuntime.data?.generation_blocked || sellerAgentRuntime.data?.is_disabled
      ? 'blocked'
      : sellerAgentRuntime.data?.cooldown_active || (sellerAgentRuntime.data?.failed_candidates ?? 0) > 0
        ? 'warning'
        : 'ready'

  const actionState: RuntimeState = actionPolicy.isLoading
    ? 'loading'
    : actionPolicy.data?.enabled
      ? 'ready'
      : 'warning'

  const promoterState: RuntimeState = promoterPolicy.isLoading
    ? 'loading'
    : promoterPolicy.data?.enabled && promoterPolicy.data?.approved
      ? 'ready'
      : promoterPolicy.data?.enabled
        ? 'warning'
        : 'blocked'

  const cards: RuntimeCard[] = [
    {
      id: 'telegram',
      name: 'Telegram Personal',
      category: 'Kanal',
      description: 'Mijoz xabarlari shu akkauntdan keladi va javoblar shu yerdan yuboriladi.',
      state: telegramState,
      stateLabel: telegramState === 'ready' ? 'ulangan' : telegramState === 'warning' ? 'tekshiring' : 'qayta ulang',
      glyph: 'TG',
      glyphTone: 'bg-foreground text-background',
      metrics: [
        { label: 'akkaunt', value: yesNo(telegram.data?.identityLinked ?? telegramProjection?.identity_linked) },
        { label: 'navbat', value: String(telegram.data?.queueSize ?? 0) },
        { label: 'qayta urinish', value: String(telegram.data?.reconnectAttempts ?? 0) },
      ],
      href: '/onboarding?reconnect=telegram',
      cta: telegramState === 'ready' ? 'Ulanishni ko‘rish' : 'Qayta ulash',
      loading: telegram.isLoading,
    },
    {
      id: 'llm',
      name: 'AI javob sifati',
      category: 'AI',
      description: 'AI qaysi ishda qanday javob berishi, xatoda qanday yumshoq to‘xtashi va qancha ishlashi boshqariladi.',
      state: llmState,
      stateLabel: !canInspectFounderRuntime
        ? 'ichki sozlama'
        : llm.error ? 'yuklanmadi' : `${llm.data?.tasks?.length ?? 0} ish`,
      glyph: 'LG',
      glyphTone: 'bg-rose-500 text-white',
      metrics: [
        { label: 'AI', value: canInspectFounderRuntime ? String(llm.data?.models?.length ?? 0) : 'ichki' },
        { label: 'ish', value: canInspectFounderRuntime ? String(llm.data?.tasks?.length ?? 0) : 'nazoratda' },
        {
          label: 'tanlov',
          value: canInspectFounderRuntime ? String(Object.keys(llm.data?.overrides ?? {}).length) : 'ichki',
        },
      ],
      href: '/agents',
      cta: 'Agentlarni ochish',
      loading: llm.isLoading,
    },
    {
      id: 'seller-agent-runtime',
      name: 'Sotuvchi agent navbati',
      category: 'Javoblar',
      description: 'Sotuvchi agent qaysi mijozlarga javob tayyorlagani, qaysilari kutayotgani va qaysilari tekshiruvga muhtojligi.',
      state: sellerAgentState,
      stateLabel: canInspectFounderRuntime
        ? sellerAgentRuntime.data?.seller_agent_enabled ? 'javob yozadi' : 'to‘xtagan'
        : 'ichki navbat',
      glyph: 'SA',
      glyphTone: 'bg-blue-500 text-white',
      metrics: [
        { label: 'tayyor', value: canInspectFounderRuntime ? String(sellerAgentRuntime.data?.ready_candidates ?? 0) : 'chatda' },
        { label: 'yozmoqda', value: canInspectFounderRuntime ? String(sellerAgentRuntime.data?.active_candidates ?? 0) : 'AI' },
        { label: 'xato', value: canInspectFounderRuntime ? String(sellerAgentRuntime.data?.failed_candidates ?? 0) : 'ichki' },
      ],
      href: '/conversations',
      cta: 'Javoblarni ochish',
      loading: sellerAgentRuntime.isLoading,
    },
    {
      id: 'action-runtime',
      name: 'Avtopilot',
      category: 'Avtopilot',
      description: 'AI qachon o‘zi yuborishi, qachon sotuvchidan ruxsat so‘rashi va kimga xabar berishi.',
      state: actionState,
      stateLabel: actionPolicy.data?.enabled ? 'tayyor' : 'faqat ruxsat bilan',
      glyph: 'AR',
      glyphTone: 'bg-violet-500 text-white',
      metrics: [
        { label: 'ishonch', value: percent(actionPolicy.data?.confidence_threshold) },
        { label: 'ruxsatli ish', value: String(actionPolicy.data?.low_risk_allowlist?.length ?? 0) },
        { label: 'xabar', value: actionPolicy.data?.escalation_destination === 'telegram_seller_bot' ? 'Telegram' : 'Ilova' },
      ],
      href: '/actions',
      cta: 'Ruxsat navbati',
      loading: actionPolicy.isLoading,
    },
    {
      id: 'promoter',
      name: 'Qayta jalb qilish',
      category: 'AI',
      description: 'Sovib qolgan yoki oldin sotib olgan mijozlarga qayta yozish qoidalari.',
      state: promoterState,
      stateLabel: promoterPolicy.data?.approved ? 'ruxsat bor' : 'ruxsat kerak',
      glyph: 'PR',
      glyphTone: 'bg-amber-500 text-white',
      metrics: [
        { label: 'yoqilgan', value: yesNo(promoterPolicy.data?.enabled) },
        { label: 'bosqich', value: String(promoterPolicy.data?.allowed_stages?.length ?? 0) },
        { label: 'haftalik', value: String(promoterPolicy.data?.max_contacts_per_7d ?? 0) },
      ],
      href: '/intelligence',
      cta: 'Rejani ochish',
      loading: promoterPolicy.isLoading,
    },
    {
      id: 'calendar',
      name: 'Kalendar uchrashuvlari',
      category: 'Keyinroq',
      description: 'Mijoz uchrashuv so‘rasa, AI kalendarga qo‘shishni taklif qiladigan ulanish.',
      state: 'gated',
      stateLabel: 'hali ulanmagan',
      glyph: 'GC',
      glyphTone: 'bg-zinc-500 text-white',
      metrics: [
        { label: 'ulanish', value: 'yo‘q' },
        { label: 'ruxsat', value: 'kerak' },
        { label: 'holat', value: 'keyin' },
      ],
      cta: 'Keyinroq',
    },
  ]

  const visible = cards.filter((card) => {
    if (query.trim() && !card.name.toLowerCase().includes(query.toLowerCase())) return false
    return matchesFilter(card, filter)
  })

  const ready = cards.filter((card) => card.state === 'ready').length
  const blocked = cards.filter((card) => card.state === 'blocked').length

  return (
    <div className="h-full overflow-y-auto bg-background text-foreground">
      <div className="mx-auto max-w-6xl px-8 py-12">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <div className="font-mono text-[10px] uppercase tracking-[0.3em] text-muted-foreground">
              Sozlamalar
            </div>
            <h1 className="mt-1 font-heading text-3xl">Ish joyi boshqaruvi</h1>
            <p className="mt-1.5 max-w-2xl text-sm leading-6 text-muted-foreground">
              Telegram ulanishi, AI javoblari, avtopilot va qayta jalb qilish shu yerdan boshqariladi.
            </p>
          </div>
          <div className="grid min-w-[240px] grid-cols-2 gap-2">
            <SummaryPill icon={CheckIcon} label="tayyor" value={String(ready)} />
            <SummaryPill icon={AlertTriangleIcon} label="to‘siq" value={String(blocked)} />
          </div>
        </div>

        <div className="mt-8 flex flex-col gap-3 sm:flex-row sm:items-center">
          <div className="flex flex-1 items-center gap-2 rounded-md border border-border/70 bg-background/40 px-3">
            <SearchIcon className="size-3.5 opacity-50" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Sozlama yoki ulanishni qidirish"
              className="h-9 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            />
          </div>
          <div className="flex flex-wrap gap-1.5">
            {FILTERS.map((item) => (
              <button
                key={item}
                type="button"
                onClick={() => setFilter(item)}
                className={`rounded-md px-2.5 py-1.5 font-mono text-[10px] uppercase tracking-[0.25em] transition-colors ${
                  filter === item
                    ? 'bg-foreground text-background'
                    : 'border border-border/60 text-muted-foreground hover:text-foreground'
                }`}
              >
                {FILTER_LABELS[item]}
              </button>
            ))}
          </div>
        </div>

        <ul className="mt-6 grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {visible.map((card) => (
            <RuntimeCard key={card.id} card={card} />
          ))}
        </ul>

        {visible.length === 0 ? (
          <div className="mt-8 rounded-xl border border-dashed border-border/70 bg-background/30 px-6 py-12 text-center">
            <p className="text-sm text-muted-foreground">"{query}" bo‘yicha sozlama topilmadi.</p>
          </div>
        ) : null}

        {canInspectFounderRuntime ? (
          <TelegramAuthSupportPanel
            loading={telegramAuthDiagnostics.isLoading}
            attempts={telegramAuthDiagnostics.data?.attempts ?? []}
          />
        ) : null}
      </div>
    </div>
  )
}

function SummaryPill({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof CheckIcon
  label: string
  value: string
}) {
  return (
    <div className="rounded-xl border border-border/60 bg-background/40 px-4 py-3">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Icon className="size-3.5" />
        {label}
      </div>
      <div className="mt-1 font-heading text-2xl">{value}</div>
    </div>
  )
}

function RuntimeCard({ card }: { card: RuntimeCard }) {
  return (
    <li className="group flex min-h-[260px] flex-col gap-3 rounded-xl border border-border/60 bg-background/40 p-4 transition-colors hover:border-foreground/30">
      <div className="flex items-start justify-between">
        <div
          className={`flex size-10 items-center justify-center rounded-lg text-[12px] font-medium ${card.glyphTone}`}
        >
          {card.loading ? <ClockIcon className="size-4 animate-pulse" /> : card.glyph}
        </div>
        <Badge variant={stateTone(card.state)} className="rounded-md">
          {card.stateLabel}
        </Badge>
      </div>

      <div>
        <div className="flex items-center gap-2">
          <span className="font-medium">{card.name}</span>
        </div>
        <div className="mt-0.5 font-mono text-[10px] uppercase tracking-[0.2em] text-muted-foreground/80">
          {card.category}
        </div>
        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
          {card.description}
        </p>
      </div>

      <div className="grid grid-cols-3 gap-2">
        {card.loading
          ? Array.from({ length: 3 }).map((_, index) => <Skeleton key={index} className="h-12 rounded-lg" />)
          : card.metrics.map((metric) => (
            <div key={metric.label} className="min-w-0 rounded-lg border border-border/60 px-2 py-2">
              <div className="truncate font-mono text-[9px] uppercase tracking-[0.18em] text-muted-foreground">
                {metric.label}
              </div>
              <div className="mt-1 truncate text-sm font-medium">{metric.value}</div>
            </div>
          ))}
      </div>

      <div className="mt-auto pt-1">
        {card.href ? (
          <Button
            render={card.href.includes('?') ? <a href={card.href} /> : <Link to={card.href} />}
            size="sm"
            variant={card.state === 'blocked' ? 'default' : 'outline'}
            className="w-full justify-between"
          >
            {card.cta}
            <ArrowUpRightIcon />
          </Button>
        ) : (
          <Button size="sm" variant="outline" disabled className="w-full justify-between">
            {card.cta}
            <ShieldCheckIcon />
          </Button>
        )}
      </div>
    </li>
  )
}

function authStateTone(state: string | null | undefined) {
  if (state === 'authenticated') return 'success'
  if (state === 'failed') return 'error'
  if (state === 'scheduled' || state === 'running') return 'warning'
  return 'outline'
}

function deliveryLabel(type: string | null | undefined) {
  if (!type) return 'noma’lum'
  if (type.includes('Sms')) return 'SMS'
  if (type.includes('Call')) return 'Qo‘ng‘iroq'
  if (type.includes('App')) return 'Telegram'
  return type.replace('auth.', '').replace('SentCodeType', '').replace('CodeType', '')
}

function authRouteLabel(attempt: TelegramAuthAttemptDiagnostic) {
  const transport = attempt.auth_transport ? attempt.auth_transport.toUpperCase() : 'yo‘l noma’lum'
  const dc = attempt.connected_initial_dc_id ? `DC${attempt.connected_initial_dc_id}` : 'DC noma’lum'
  const tried = attempt.attempted_dc_ids.length > 0
    ? `sinov: ${attempt.attempted_dc_ids.map((id) => `DC${id}`).join(', ')}`
    : `${attempt.recovery_attempt_count}/${attempt.max_recovery_attempts} tiklash`
  const expected = attempt.delivery_degraded
    ? ` · kutilgan: ${deliveryLabel(attempt.preferred_delivery_type)}`
    : ''
  return `${transport} · ${dc} · ${tried}${expected}`
}

function shortDate(value: string | null | undefined) {
  if (!value) return 'yo‘q'
  return new Intl.DateTimeFormat('uz-UZ', {
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

function TelegramAuthSupportPanel({
  loading,
  attempts,
}: {
  loading: boolean
  attempts: TelegramAuthAttemptDiagnostic[]
}) {
  const latest = attempts.slice(0, 4)

  return (
    <section className="mt-10 rounded-xl border border-border/60 bg-background/40 p-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="font-mono text-[10px] uppercase tracking-[0.25em] text-muted-foreground">
            Yordam diagnostikasi
          </div>
          <h2 className="mt-1 font-heading text-xl">Telegram kod holati</h2>
          <p className="mt-1 max-w-2xl text-xs leading-5 text-muted-foreground">
            Kod yuborilishi, keyingi SMS/qo‘ng‘iroq tiklanishi va xato sababi shu yerda ko‘rinadi.
          </p>
        </div>
        <Badge variant={attempts.some((item) => item.state === 'failed') ? 'error' : 'outline'}>
          {loading ? 'tekshirilmoqda' : `${attempts.length} urinish`}
        </Badge>
      </div>

      <div className="mt-4 grid gap-2">
        {loading ? (
          Array.from({ length: 3 }).map((_, index) => <Skeleton key={index} className="h-20 rounded-lg" />)
        ) : latest.length > 0 ? (
          latest.map((attempt) => (
            <div
              key={attempt.id}
              className="grid gap-3 rounded-lg border border-border/60 p-3 text-xs md:grid-cols-[1.2fr_1fr_1fr_1.4fr]"
            >
              <div className="min-w-0">
                <div className="font-medium">{attempt.phone_masked}</div>
                <div className="mt-1 truncate text-muted-foreground">
                  {attempt.temp_session_id ?? 'sessiya yo‘q'}
                </div>
              </div>
              <div>
                <div className="text-muted-foreground">Holat</div>
                <div className="mt-1 flex flex-wrap gap-1">
                  <Badge variant={authStateTone(attempt.state)}>{attempt.state}</Badge>
                  <Badge variant={authStateTone(attempt.recovery_state)}>{attempt.recovery_state ?? 'kutmoqda'}</Badge>
                </div>
              </div>
              <div>
                <div className="text-muted-foreground">Yo‘l</div>
                <div className="mt-1 font-medium">
                  {deliveryLabel(attempt.delivery_type)} → {deliveryLabel(attempt.next_delivery_type)}
                </div>
                <div className="mt-1 text-muted-foreground">
                  {authRouteLabel(attempt)}
                </div>
              </div>
              <div className="min-w-0">
                <div className="text-muted-foreground">Keyingi qadam</div>
                <div className="mt-1 font-medium">{shortDate(attempt.next_recovery_at)}</div>
                <div className="mt-1 truncate text-muted-foreground">
                  {attempt.last_error ?? attempt.last_step ?? 'xato yo‘q'}
                </div>
              </div>
            </div>
          ))
        ) : (
          <div className="rounded-lg border border-dashed border-border/70 px-4 py-6 text-center text-sm text-muted-foreground">
            Hali Telegram kod urinishlari yo‘q.
          </div>
        )}
      </div>
    </section>
  )
}
