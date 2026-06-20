import {
  ArrowClockwise,
  CheckCircle,
  FileText,
  ShieldCheck,
  TelegramLogo,
  WarningCircle,
} from '@phosphor-icons/react'
import type { ReactNode } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Progress, ProgressLabel, ProgressValue } from '@/components/ui/progress'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'
import type {
  OnboardingRuntimeProjection,
  OnboardingSourceLearningEvent,
  OnboardingSourceLearningSource,
  WorkspaceOSProjection,
} from '@/lib/types'
import { runtimeDetailText, sourceLearningReasonLabel } from './copy'
import type { OnboardingActivityEvent, OnboardingActivityStatus } from './learned-panels'
import type { Phase } from './types'

type RailEventStatus = OnboardingActivityStatus | 'warning'

interface RailEvent {
  id: string
  title: string
  detail: string
  status: RailEventStatus
  meta?: string[]
  cached?: boolean
}

interface RailStats {
  sourceCount: number
  sourceUnitCount: number
  mediaCount: number
  proposalCount: number
  pendingReviewCount: number
}

export function WorkspaceOSRail({
  workspaceOS,
  runtime,
  telegramConnected,
  activityEvents,
  phase,
  draftSourceCount = 0,
  isSourceLearning,
  isRebuilding,
  onRetryLearning,
  onRebuild,
}: {
  workspaceOS?: WorkspaceOSProjection
  runtime?: OnboardingRuntimeProjection
  telegramConnected: boolean
  activityEvents: OnboardingActivityEvent[]
  phase?: Phase
  draftSourceCount?: number
  isSourceLearning: boolean
  isRebuilding: boolean
  onRetryLearning: () => void
  onRebuild: () => void
}) {
  const readiness = workspaceOS?.readiness
  const sourceLearning = runtime?.source_learning
  const sourceSummary = sourceLearning?.summary ?? workspaceOS?.sources?.summary
  const sources = sourceLearning?.sources ?? workspaceOS?.sources?.sources ?? []
  const pendingReviewCount = (
    (runtime?.learned_review?.summary.total_review_items ?? 0)
    + (workspaceOS?.actions?.needs_approval ?? 0)
  )
  const retryableSource = sources.find((source) => source.retryable)
  const businessPreview = workspaceOS?.documents?.sections_preview ?? []
  const readyAgents = workspaceOS?.agents.filter((agent) => agent.present) ?? []
  const visibleIssues = readiness?.status !== 'not_provisioned'
    ? readiness?.issues
      .filter((issue) => issue.code !== 'agent_missing')
      .filter((issue) => issue.severity !== 'info')
      .slice(0, 3) ?? []
    : []
  const baseWorking = isSourceLearning || Boolean(runtime?.is_running) || isRebuilding
  const processEvents = buildRailEvents({
    activityEvents,
    sources,
    runtime,
    phase,
    draftSourceCount,
    isSourceLearning,
    telegramConnected,
  })
  const working = baseWorking || processEvents.some((event) => event.status === 'running')
  const railStats = buildRailStats({ sources, pendingReviewCount })
  const hasDraftSources = phase === 'sources' && draftSourceCount > 0 && !working
  const currentEvent = currentRailEvent(processEvents, hasDraftSources)
  const progressValue = railProgressValue({
    readiness,
    runtime,
    sourceLearning,
    pendingReviewCount,
    working,
    hasDraftSources,
    onboardingIncomplete: workspaceOS?.onboarding_completed === false,
  })
  const sourceLearningPercent = hasDraftSources ? null : sourceLearning?.summary?.total ? sourceLearning.percent : null
  const currentDetail = currentEvent?.detail ?? 'OQIM dalilni o‘qiganda qaysi ish ketayotgani, cache, retry va topilgan takliflar shu yerda chiqadi.'
  const attentionSources = sources.filter((source) => (
    source.retryable
    || ['failed', 'missing', 'retrying', 'learning', 'running', 'queued', 'review_ready', 'needs_review', 'conflict'].includes(String(source.status))
  ))

  return (
    <aside className="flex h-full min-h-0 flex-col overflow-hidden border-l border-border/80 bg-background">
      <div className="shrink-0 border-b border-border/70 px-4 py-3.5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">BI agent kuzatuvi</p>
            <h2 className="mt-1 line-clamp-1 text-base font-semibold tracking-tight">
              {currentEvent?.title ?? 'OQIM tayyorlanmoqda'}
            </h2>
            <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">{currentDetail}</p>
          </div>
          <Badge variant={working ? 'info' : hasDraftSources ? 'outline' : readiness?.status === 'ready' ? 'success' : readiness?.status === 'degraded' ? 'error' : 'outline'}>
            {working ? 'Ishlayapti' : hasDraftSources ? 'Davom eting' : readinessLabel(readiness?.status, workspaceOS?.onboarding_completed === false)}
          </Badge>
        </div>
        {currentEvent ? <LiveStatusBar event={currentEvent} progressValue={progressValue} /> : null}
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 pb-4 pt-4">
        <RailMetricStrip
          sourceLearningPercent={sourceLearningPercent}
          pendingReviewCount={pendingReviewCount}
          working={working}
          stats={railStats}
        />

        <section className="grid gap-2" aria-label="BI agent jonli oqimi">
          <SectionLabel>Jonli oqim</SectionLabel>
          <div className="grid gap-2">
            {processEvents.slice(0, 5).map((event) => (
              <RailStreamRow key={event.id} event={event} />
            ))}
          </div>
        </section>

        <Separator />

        <section className="grid gap-1">
          <StatusRow
            icon={<TelegramLogo />}
            title="Telegram"
            detail={telegramConnected ? 'ulangan' : 'ulanish kutilmoqda'}
            tone={telegramConnected ? 'success' : 'muted'}
          />
          <StatusRow
            icon={<FileText />}
            title="Dalillar"
            detail={hasDraftSources
              ? `${draftSourceCount} ta dalil navbatda, hali o‘qilmadi`
              : sourceStatusSummary(sourceSummary, railStats)}
            tone={hasDraftSources || sourceSummary?.failed || sourceSummary?.retrying ? 'warning' : sourceSummary?.total ? 'success' : 'muted'}
            action={retryableSource ? 'Qayta urinish' : undefined}
            isRebuilding={isRebuilding}
            onAction={onRetryLearning}
          />
          <StatusRow
            icon={<ShieldCheck />}
            title="Tasdiq"
            detail={pendingReviewCount ? `${pendingReviewCount} ta taklif` : 'shoshilinch ish yo‘q'}
            tone={pendingReviewCount ? 'warning' : 'success'}
          />
        </section>

        {attentionSources.length > 0 ? (
          <section className="grid gap-2">
            <SectionLabel>Dalillar oqimi</SectionLabel>
            <div className="grid gap-2">
              {attentionSources.slice(0, 4).map((source, index) => (
                <SourceMiniRow key={String(source.source_ref ?? source.fact_id ?? index)} source={source} />
              ))}
            </div>
          </section>
        ) : null}

        <Separator />

        <section className="grid gap-3">
          <SectionLabel>OS fayllari</SectionLabel>
          <div className="grid gap-2">
            <CompactArtifactRow
              title="BUSINESS.md"
              detail={businessPreview.length > 0
                ? `${workspaceOS?.documents.business_section_count ?? businessPreview.length} bo‘lim`
                : 'yakunlashda yaratiladi'}
              ready={Boolean(workspaceOS?.documents.business_md_ready)}
            />
            <CompactArtifactRow
              title="AGENT.md"
              detail={readyAgents.length > 0 ? `${readyAgents.length} agent sozlandi` : 'agent tanlovidan keyin'}
              ready={readyAgents.some((agent) => agent.health === 'ready')}
            />
            <CompactArtifactRow
              title="SKILL.md"
              detail={skillSummary(readyAgents)}
              ready={readyAgents.some((agent) => agent.skill_count > 0)}
            />
          </div>
        </section>

        {businessPreview.length > 0 ? (
          <section className="grid gap-2">
            <SectionLabel>Business fayldan</SectionLabel>
            {businessPreview.slice(0, 3).map((section) => (
              <div key={section.section_key} className="min-w-0">
                <p className="truncate text-sm font-medium">{section.title}</p>
                <p className="line-clamp-2 text-xs leading-5 text-muted-foreground">{section.body_preview}</p>
              </div>
            ))}
          </section>
        ) : null}

        {visibleIssues.length > 0 ? (
          <section className="grid gap-3">
            <SectionLabel>Eʼtibor kerak</SectionLabel>
            <div className="grid gap-2">
              {visibleIssues.map((issue) => (
                <div key={`${issue.code}:${issue.target_ref}`} className="rounded-lg border border-border/80 px-3 py-3">
                  <div className="flex items-start gap-2">
                    <WarningCircle className="mt-0.5 size-4 shrink-0 text-amber-600" />
                    <div className="min-w-0">
                      <p className="text-sm font-medium">{issue.title_uz}</p>
                      <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">{issue.detail_uz}</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
            <Button type="button" variant="outline" className="w-full" onClick={onRebuild} disabled={isRebuilding}>
              {isRebuilding ? <ArrowClockwise data-icon="inline-start" className="animate-spin" /> : <CheckCircle data-icon="inline-start" />}
              Qayta yig‘ish
            </Button>
          </section>
        ) : null}
      </div>
    </aside>
  )
}

export function OnboardingStatusBar({
  workspaceOS,
  runtime,
  telegramConnected,
  activityEvents,
  phase,
  draftSourceCount = 0,
  isSourceLearning,
  isRebuilding,
  onRetryLearning,
}: {
  workspaceOS?: WorkspaceOSProjection
  runtime?: OnboardingRuntimeProjection
  telegramConnected: boolean
  activityEvents: OnboardingActivityEvent[]
  phase?: Phase
  draftSourceCount?: number
  isSourceLearning: boolean
  isRebuilding: boolean
  onRetryLearning?: () => void
}) {
  const readiness = workspaceOS?.readiness
  const sourceLearning = runtime?.source_learning
  const sources = sourceLearning?.sources ?? workspaceOS?.sources?.sources ?? []
  const pendingReviewCount = (
    (runtime?.learned_review?.summary.total_review_items ?? 0)
    + (workspaceOS?.actions?.needs_approval ?? 0)
  )
  const baseWorking = isSourceLearning || Boolean(runtime?.is_running) || isRebuilding
  const hasDraftSources = phase === 'sources' && draftSourceCount > 0 && !baseWorking
  const processEvents = buildRailEvents({
    activityEvents,
    sources,
    runtime,
    phase,
    draftSourceCount,
    isSourceLearning,
    telegramConnected,
  })
  const working = baseWorking || processEvents.some((event) => event.status === 'running')
  const progressValue = railProgressValue({
    readiness,
    runtime,
    sourceLearning,
    pendingReviewCount,
    working,
    hasDraftSources,
    onboardingIncomplete: workspaceOS?.onboarding_completed === false,
  })
  const currentEvent = currentRailEvent(processEvents, hasDraftSources)
  const sourceCount = sourceLearning?.summary.total ?? workspaceOS?.sources?.summary.total ?? 0
  const railStats = buildRailStats({ sources, pendingReviewCount })
  const displaySourceCount = hasDraftSources
    ? draftSourceCount
    : sourceCount
  const hasRetryableSource = sources.some((source) => (
    source.retryable
    || ['failed', 'missing', 'retrying'].includes(String(source.status))
  ))
  const hasLearningDegradation = Boolean(
    runtime?.is_retryable
      || runtime?.is_dlq
      || runtime?.can_requeue
      || runtime?.progress?.ai_learning_degraded
      || runtime?.progress?.voice_profile_degraded
      || runtime?.progress?.contact_classification_degraded,
  )
  const canRetryLearning = Boolean(onRetryLearning && (hasRetryableSource || hasLearningDegradation))
  const osFilesPending = Boolean(
    phase === 'credentials'
      && workspaceOS?.onboarding_completed === false
      && workspaceOS.documents
      && !workspaceOS.documents.business_md_ready,
  )
  const statusFacts = compactStatusFacts({
    stats: railStats,
    currentEvent,
    displaySourceCount,
    pendingReviewCount,
    working,
    hasDraftSources,
    draftSourceCount,
    osFilesPending,
  })

  if (!currentEvent) return null

  return (
    <div
      role="status"
      aria-live="polite"
      aria-label="Onboarding status satri"
      className="grid gap-3 border-b border-border/80 bg-background/95 pb-3 lg:grid-cols-[minmax(0,1fr)_minmax(12rem,15rem)] lg:items-start"
    >
      <div className="min-w-0">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <span className="text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">Jonli oqim</span>
          <span className={cn('size-2 rounded-full', activityDotClass(currentEvent.status), currentEvent.status === 'running' && 'animate-pulse')} />
          <p className="min-w-0 flex-1 truncate text-sm font-semibold">{currentEvent.title}</p>
          <Badge variant={working ? 'info' : hasDraftSources ? 'outline' : readiness?.status === 'ready' ? 'success' : 'outline'} className="shrink-0">
            {working ? 'Ishlayapti' : hasDraftSources ? 'Davom eting' : readinessLabel(readiness?.status, workspaceOS?.onboarding_completed === false)}
          </Badge>
        </div>
        <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">
          {currentEvent.detail}
        </p>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">
          {statusFacts}
          {runtime?.lease_expired ? ' · ish qayta olinadi' : ''}
        </p>
        <StatusEventStrip events={processEvents} />
      </div>
      <div className="grid gap-1.5 lg:pt-0.5">
        <Progress value={progressValue} className="gap-1.5">
          <ProgressLabel className="text-xs text-muted-foreground">OS tayyorligi</ProgressLabel>
          <ProgressValue className="text-xs">{Math.round(progressValue)}%</ProgressValue>
        </Progress>
        <p className="text-left text-[11px] leading-4 text-muted-foreground lg:text-right">
          {pendingReviewCount > 0
            ? `${pendingReviewCount} ta ko‘rib chiqiladi`
            : displaySourceCount > 0
              ? `${displaySourceCount} ta dalil manbasi`
              : 'Dalil kutilmoqda'}
        </p>
        {canRetryLearning ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="mt-1 justify-self-end"
            onClick={onRetryLearning}
            disabled={isRebuilding}
          >
            {isRebuilding ? <ArrowClockwise data-icon="inline-start" className="animate-spin" /> : null}
            Qayta urinish
          </Button>
        ) : null}
      </div>
    </div>
  )
}

function StatusEventStrip({ events }: { events: RailEvent[] }) {
  if (events.length <= 1) return null
  return (
    <div className="mt-2 flex min-w-0 flex-wrap gap-x-3 gap-y-1 border-t border-border/60 pt-2">
      {events.slice(1, 4).map((event) => (
        <div
          key={event.id}
          className="flex min-w-0 max-w-full items-center gap-1.5 text-xs text-muted-foreground"
        >
          <span className={cn('size-1.5 shrink-0 rounded-full', activityDotClass(event.status), event.status === 'running' && 'animate-pulse')} />
          <span className="max-w-64 truncate">{event.title}</span>
        </div>
      ))}
    </div>
  )
}

function LiveStatusBar({
  event,
  progressValue,
}: {
  event: RailEvent
  progressValue: number
}) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="mt-3 border-t border-border/70 pt-3"
    >
      <div className="flex items-start gap-3">
        <span className={cn('mt-1 size-2 rounded-full', activityDotClass(event.status), event.status === 'running' && 'animate-pulse')} />
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-3">
            <p className="min-w-0 truncate text-sm font-medium leading-5">{event.title}</p>
            <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
              {Math.round(progressValue)}%
            </span>
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary transition-[width]"
              style={{ width: `${Math.max(0, Math.min(100, progressValue))}%` }}
            />
          </div>
          <p className="mt-2 line-clamp-2 text-xs leading-5 text-muted-foreground">{event.detail}</p>
          <EventMetaChips event={event} className="mt-2" />
        </div>
      </div>
    </div>
  )
}

function RailMetricStrip({
  sourceLearningPercent,
  pendingReviewCount,
  working,
  stats,
}: {
  sourceLearningPercent: number | null
  pendingReviewCount: number
  working: boolean
  stats: RailStats
}) {
  return (
    <section className="grid gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>{working ? 'OQIM o‘qiyapti' : pendingReviewCount ? 'Ko‘rib chiqish kerak' : 'Barqaror'}</span>
        {sourceLearningPercent !== null ? (
          <span>Dalil o‘qish {Math.round(sourceLearningPercent)}%</span>
        ) : null}
      </div>
      <div className="flex flex-wrap gap-1.5 text-[11px] text-muted-foreground">
        <span className="rounded-md bg-muted px-1.5 py-0.5">{stats.sourceCount} dalil manbasi</span>
        <span className="rounded-md bg-muted px-1.5 py-0.5">{stats.sourceUnitCount + stats.mediaCount} dalil</span>
        <span className="rounded-md bg-muted px-1.5 py-0.5">{stats.proposalCount} taklif</span>
        <span className="rounded-md bg-muted px-1.5 py-0.5">{stats.pendingReviewCount} tasdiq</span>
      </div>
    </section>
  )
}

function compactStatusFacts({
  stats,
  currentEvent,
  displaySourceCount,
  pendingReviewCount,
  working,
  hasDraftSources,
  draftSourceCount,
  osFilesPending,
}: {
  stats: RailStats
  currentEvent?: RailEvent
  displaySourceCount: number
  pendingReviewCount: number
  working: boolean
  hasDraftSources: boolean
  draftSourceCount: number
  osFilesPending: boolean
}) {
  if (hasDraftSources) {
    return `${draftSourceCount} ta yangi dalil tayyor, hali o‘qilmadi`
  }
  const facts = [
    displaySourceCount > 0 ? `${displaySourceCount} manba` : null,
    currentEvent?.cached ? 'cache' : null,
    ...(currentEvent?.meta ?? []),
    stats.proposalCount > 0 ? `${stats.proposalCount} taklif` : null,
    stats.sourceUnitCount > 0 || stats.mediaCount > 0
      ? `jami ${stats.sourceUnitCount + stats.mediaCount} dalil`
      : null,
    pendingReviewCount > 0 ? `${pendingReviewCount} tasdiq` : null,
    osFilesPending ? 'OS fayllari yakunlashda yaratiladi' : null,
  ].filter(Boolean)
  if (facts.length === 0) return working ? 'OQIM birinchi natijani kutmoqda' : 'Manba qo‘shilganda oqim shu yerda boshlanadi'
  return facts.join(' · ')
}

function RailStreamRow({ event }: { event: RailEvent }) {
  return (
    <div className="grid grid-cols-[0.75rem_minmax(0,1fr)] gap-3">
      <span className={cn('mt-1.5 size-2 rounded-full', activityDotClass(event.status), event.status === 'running' && 'animate-pulse')} />
      <span className="min-w-0">
        <span className="block truncate text-sm font-medium leading-5">{event.title}</span>
        <span className="block line-clamp-2 text-xs leading-5 text-muted-foreground">{event.detail}</span>
        <EventMetaChips event={event} className="mt-1.5" />
      </span>
    </div>
  )
}

function EventMetaChips({
  event,
  className,
}: {
  event: RailEvent
  className?: string
}) {
  const chips = [
    ...(event.cached ? ['cache'] : []),
    ...(event.meta ?? []),
  ].slice(0, 4)
  if (chips.length === 0) return null
  return (
    <span className={cn('flex flex-wrap gap-1.5', className)}>
      {chips.map((chip) => (
        <span key={chip} className="rounded-md bg-muted px-1.5 py-0.5 text-[11px] leading-4 text-muted-foreground">
          {chip}
        </span>
      ))}
    </span>
  )
}

function StatusRow({
  icon,
  title,
  detail,
  tone,
  action,
  isRebuilding = false,
  onAction,
}: {
  icon: ReactNode
  title: string
  detail: string
  tone: 'success' | 'warning' | 'muted'
  action?: string
  isRebuilding?: boolean
  onAction?: () => void
}) {
  return (
    <div className="flex items-center gap-3 py-2.5">
      <span className="grid size-9 shrink-0 place-items-center rounded-md bg-muted text-foreground [&_svg]:size-5">
        {icon}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium">{title}</span>
        <span className="block truncate text-sm text-muted-foreground">{detail}</span>
      </span>
      {action && onAction ? (
        <Button type="button" size="sm" variant="outline" onClick={onAction} disabled={isRebuilding}>
          {isRebuilding ? <ArrowClockwise data-icon="inline-start" className="animate-spin" /> : null}
          {action}
        </Button>
      ) : (
        <span className={statusDotClass(tone)} />
      )}
    </div>
  )
}

function CompactArtifactRow({
  title,
  detail,
  ready,
}: {
  title: string
  detail: string
  ready: boolean
}) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border/60 py-2.5 last:border-b-0">
      <span className="min-w-0">
        <span className="block text-sm font-medium">{title}</span>
        <span className="block truncate text-xs text-muted-foreground">{detail}</span>
      </span>
      <Badge variant={ready ? 'success' : 'outline'}>{ready ? 'Tayyor' : 'Kutilmoqda'}</Badge>
    </div>
  )
}

function SourceMiniRow({
  source,
}: {
  source: OnboardingSourceLearningSource | WorkspaceOSProjection['sources']['sources'][number]
}) {
  const detail = sourceMiniDetail(source)
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-3 py-1">
      <span className="min-w-0">
        <span className="block truncate text-sm font-medium">{safeSourceTitle(source)}</span>
        <span className="block truncate text-xs text-muted-foreground">{detail}</span>
      </span>
      <span className="mt-1.5 flex items-center gap-2">
        <span className={cn('size-2 shrink-0 rounded-full', activityDotClass(railStatus(source.status)))} />
        <span className="text-[11px] text-muted-foreground">{sourceStatusLabel(source.status)}</span>
      </span>
    </div>
  )
}

function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <p className="text-sm font-medium text-muted-foreground">
      {children}
    </p>
  )
}

function sourceStatusLabel(status: string) {
  if (['failed', 'missing'].includes(status)) return 'qayta tekshirish kerak'
  if (status === 'retrying') return 'qayta urinmoqda'
  if (['learned', 'done', 'ready'].includes(status)) return 'o‘rganildi'
  if (['review_ready', 'needs_review'].includes(status)) return 'tasdiq kutmoqda'
  if (status === 'conflict') return 'konflikt bor'
  if (['learning', 'running', 'queued'].includes(status)) return 'o‘qilmoqda'
  return 'navbatda'
}

function buildRailStats({
  sources,
  pendingReviewCount,
}: {
  sources: Array<OnboardingSourceLearningSource | WorkspaceOSProjection['sources']['sources'][number]>
  pendingReviewCount: number
}): RailStats {
  return sources.reduce<RailStats>((stats, source) => {
    stats.sourceCount += 1
    stats.sourceUnitCount += Number(source.source_unit_count ?? 0)
    stats.mediaCount += Number(source.source_media_count ?? 0)
    stats.proposalCount += Number('catalog_candidate_count' in source ? source.catalog_candidate_count ?? 0 : 0)
    stats.proposalCount += Number('memory_candidate_count' in source ? source.memory_candidate_count ?? 0 : 0)
    return stats
  }, {
    sourceCount: 0,
    sourceUnitCount: 0,
    mediaCount: 0,
    proposalCount: 0,
    pendingReviewCount,
  })
}

function sourceStatusSummary(
  summary: OnboardingRuntimeProjection['source_learning']['summary'] | WorkspaceOSProjection['sources']['summary'] | undefined,
  stats: RailStats,
) {
  if (!summary?.total) return 'hali tanlanmagan'
  if ('learning' in summary && summary.learning > 0) return `${summary.learning}/${summary.total} dalil o‘qilmoqda`
  if ('needs_review' in summary && summary.needs_review > 0) return `${summary.needs_review} dalil tasdiq kutmoqda`
  if ('failed' in summary && summary.failed > 0) return `${summary.failed} dalil qayta urinishga muhtoj`
  if (stats.proposalCount > 0) return `${stats.proposalCount} taklif, ${stats.sourceUnitCount + stats.mediaCount} dalil`
  return `${summary.total} ta dalil manbasi`
}

function sourceMiniDetail(
  source: OnboardingSourceLearningSource | WorkspaceOSProjection['sources']['sources'][number],
) {
  const stage = String('stage' in source ? source.stage ?? '' : '')
  const stageText = sourceStageDetail(stage, source.kind)
  const units = Number(source.source_unit_count ?? 0)
  const media = Number(source.source_media_count ?? 0)
  const catalogCandidates = Number('catalog_candidate_count' in source ? source.catalog_candidate_count ?? 0 : 0)
  const memoryCandidates = Number('memory_candidate_count' in source ? source.memory_candidate_count ?? 0 : 0)
  const rejectedCandidates = Number('rejected_candidate_count' in source ? source.rejected_candidate_count ?? 0 : 0)
  const attemptCount = Number('attempt_count' in source ? source.attempt_count ?? 0 : 0)
  const maxAttempts = Number('max_attempts' in source ? source.max_attempts ?? 0 : 0)
  const pieces = [
    stageText,
    catalogCandidates ? `${catalogCandidates} katalog` : null,
    memoryCandidates ? `${memoryCandidates} bilim` : null,
    units ? `${units} dalil` : null,
    media ? `${media} media` : null,
    rejectedCandidates ? `${rejectedCandidates} rad etildi` : null,
    attemptCount > 0 && maxAttempts > 0 ? `${attemptCount}/${maxAttempts}-urinish` : null,
  ].filter(Boolean)
  if (pieces.length > 0) return pieces.join(' · ')
  return sourcePurposeLabel(source.purpose, source.kind)
}

function buildRailEvents({
  activityEvents,
  sources,
  runtime,
  phase,
  draftSourceCount,
  isSourceLearning,
  telegramConnected,
}: {
  activityEvents: OnboardingActivityEvent[]
  sources: Array<OnboardingSourceLearningSource | WorkspaceOSProjection['sources']['sources'][number]>
  runtime?: OnboardingRuntimeProjection
  phase?: Phase
  draftSourceCount?: number
  isSourceLearning: boolean
  telegramConnected: boolean
}): RailEvent[] {
  const working = isSourceLearning || Boolean(runtime?.is_running)
  const draftEvent = draftSourceEvent({ phase, draftSourceCount, working })
  if (draftEvent) return [draftEvent]
  const durableEvents = (runtime?.source_learning?.events ?? []).map(sourceLearningEventToRailEvent)
  const stageEvents = buildRuntimeStageEvents(runtime)
  const historyEvent = historyRailEvent(runtime)
  const voiceEvent = voiceRailEvent(runtime)
  const sourceEvents = durableEvents.length > 0
    ? []
    : sources.slice(0, 4).map((source, index) => sourceToRailEvent(source, index))
  const events: RailEvent[] = [
    ...activityEvents.map((event) => ({ ...event })),
    ...durableEvents,
    ...stageEvents,
    ...(historyEvent ? [historyEvent] : []),
    ...(voiceEvent ? [voiceEvent] : []),
    ...sourceEvents,
    ...(draftEvent ? [draftEvent] : []),
  ]

  if (events.length > 0) return dedupeRailEvents(events).slice(-7).reverse()
  if (working) {
    return [{
      id: 'runtime-working',
      title: runtime?.stages.find((stage) => stage.id === runtime.current_stage_id)?.label ?? 'O‘qish davom etmoqda',
      detail: 'OQIM topgan narsasini katalog, bilim, qoida va dalillarga ajratadi.',
      status: 'running',
    }]
  }
  return [{
    id: 'waiting',
    title: telegramConnected ? 'Dalil qo‘shing' : 'Telegram ulanishini yakunlang',
    detail: telegramConnected
      ? 'Davom etishda OQIM har bir dalil manbasini alohida o‘qib, natijani shu yerda ochadi.'
      : 'Ulangan sessiyadan keyin suhbat tarixi va kanallar o‘qilishi mumkin.',
    status: 'queued',
  }]
}

function currentRailEvent(events: RailEvent[], hasDraftSources: boolean) {
  if (hasDraftSources) {
    return events.find((event) => event.id === 'draft-sources') ?? events[0]
  }
  return events.find((event) => event.status === 'running')
    ?? events.find((event) => event.status === 'failed' || event.status === 'warning')
    ?? events[0]
}

function draftSourceEvent({
  phase,
  draftSourceCount = 0,
  working,
}: {
  phase?: Phase
  draftSourceCount?: number
  working: boolean
}): RailEvent | null {
  if (phase !== 'sources' || draftSourceCount <= 0 || working) return null
  return {
    id: 'draft-sources',
    title: `${draftSourceCount} ta dalil navbatda`,
    detail: 'Hali o‘qilmadi. Davom eting: OQIM har birini dalil, katalog, bilim, qoida va agent sozlamasiga ajratadi.',
    status: 'queued',
  }
}

function buildRuntimeStageEvents(runtime?: OnboardingRuntimeProjection): RailEvent[] {
  if (!runtime) return []
  const currentStageId = runtime.current_stage_id
  return runtime.stages
    .filter((stage) => {
      if (stage.status === 'pending') return stage.id === currentStageId
      return ['running', 'failed', 'dlq', 'completed'].includes(stage.status)
    })
    .slice(-5)
    .map((stage) => ({
      id: `runtime-stage:${stage.id}:${stage.status}`,
      title: stage.label,
      detail: runtimeDetailText(stage.error ?? stage.detail, runtimeStageDetail(stage.status)),
      status: railStatus(stage.status),
    }))
}

function historyRailEvent(runtime?: OnboardingRuntimeProjection): RailEvent | null {
  const progress = runtime?.progress
  if (!progress) return null
  const customers = Number(progress.customers_identified ?? 0)
  const contacts = Number(progress.contacts_found ?? 0)
  const conversations = Number(progress.history_replayed_conversations ?? 0)
  const messages = Number(progress.history_replayed_messages ?? 0)
  const limit = Number(progress.history_learning_conversation_limit ?? progress.visible_dialog_limit ?? 50)
  if (customers <= 0 && contacts <= 0 && conversations <= 0 && messages <= 0) return null
  const title = customers > 0
    ? `${customers} mijozdan suhbat signali olindi`
    : `Oxirgi ${limit} suhbat oynasi o‘qilmoqda`
  const detailParts = [
    `OQIM javob uslubi, savol turlari va mijoz holatini oxirgi ${limit} mijoz oynasidan ajratadi.`,
    conversations > 0 || messages > 0
      ? `${conversations || limit} suhbat, ${messages || 'xabarlar'} xabar ko‘rildi.`
      : null,
  ].filter(Boolean)
  return {
    id: 'runtime-history-learning',
    title,
    detail: detailParts.join(' '),
    status: runtime?.is_running && !progress.completed ? 'running' : 'done',
    meta: [
      customers > 0 ? `${customers} mijoz` : null,
      conversations > 0 ? `${conversations} suhbat` : null,
      messages > 0 ? `${messages} xabar` : null,
    ].filter(Boolean) as string[],
  }
}

function voiceRailEvent(runtime?: OnboardingRuntimeProjection): RailEvent | null {
  const progress = runtime?.progress
  if (!progress) return null
  const discoveries = Array.isArray(progress.voice_discoveries) ? progress.voice_discoveries : []
  if (progress.voice_profile_ready || discoveries.length > 0) {
    const discoveryText = discoveries
      .map((discovery) => [discovery.label, discovery.subtitle].filter(Boolean).join(' · '))
      .filter(Boolean)
      .slice(0, 2)
      .join('; ')
    return {
      id: 'runtime-voice-learning',
      title: 'Yozish uslubi o‘rganildi',
      detail: discoveryText || 'Suhbatlardan salomlashish, aniqlashtirish va javob ohangi ajratildi.',
      status: runtime?.is_running && !progress.completed ? 'running' : 'done',
      meta: discoveries.length > 0 ? [`${discoveries.length} signal`] : [],
    }
  }
  if (progress.voice_profile_degraded) {
    return {
      id: 'runtime-voice-learning',
      title: 'Yozish uslubi uchun dalil kam',
      detail: 'OQIM ovoz va suhbatdan signal izlayapti. Ko‘proq suhbat, audio yoki qoida qo‘shilsa uslub aniqroq bo‘ladi.',
      status: 'warning',
      meta: ['ko‘proq dalil kerak'],
    }
  }
  return null
}

function runtimeStageDetail(status: string) {
  if (status === 'running') return 'Hozir bajarilmoqda. Natija tayyor bo‘lsa shu yerda ochiladi.'
  if (status === 'completed') return 'Tayyor.'
  if (status === 'failed' || status === 'dlq') return 'Qayta urinish yoki tekshirish kerak.'
  return 'Navbatda.'
}

function sourceLearningEventToRailEvent(event: OnboardingSourceLearningEvent): RailEvent {
  return {
    id: event.event_ref,
    title: event.title_uz,
    detail: runtimeDetailText(event.detail_uz, 'Dalil holati yangilandi.'),
    status: railStatus(event.status),
    meta: sourceLearningEventMeta(event),
    cached: Boolean(event.input_cache_reused),
  }
}

function sourceToRailEvent(
  source: OnboardingSourceLearningSource | WorkspaceOSProjection['sources']['sources'][number],
  index: number,
): RailEvent {
  const status = railStatus(source.status)
  const units = Number(source.source_unit_count ?? 0)
  const media = Number(source.source_media_count ?? 0)
  const catalogCandidates = Number('catalog_candidate_count' in source ? source.catalog_candidate_count ?? 0 : 0)
  const memoryCandidates = Number('memory_candidate_count' in source ? source.memory_candidate_count ?? 0 : 0)
  const rejectedCandidates = Number('rejected_candidate_count' in source ? source.rejected_candidate_count ?? 0 : 0)
  const stage = String('stage' in source ? source.stage ?? '' : '')
  const attemptCount = Number('attempt_count' in source ? source.attempt_count ?? 0 : 0)
  const maxAttempts = Number('max_attempts' in source ? source.max_attempts ?? 0 : 0)
  const attempt = attemptCount > 0 && maxAttempts > 0 ? `${attemptCount}/${maxAttempts}-urinish` : null
  const stageDetail = sourceStageDetail(stage, source.kind)
  const details = [
    stageDetail,
    catalogCandidates > 0 ? `${catalogCandidates} ta katalog taklifi` : null,
    memoryCandidates > 0 ? `${memoryCandidates} ta bilim taklifi` : null,
    units > 0 ? `${units} ta dalil` : null,
    media > 0 ? `${media} ta media` : null,
    rejectedCandidates > 0 ? `${rejectedCandidates} ta ishonchsiz taklif ajratildi` : null,
    attempt,
    source.degraded_reasons?.[0] ? sourceLearningReasonLabel(source.degraded_reasons[0]) : null,
  ].filter(Boolean)
  return {
    id: `source:${source.source_ref ?? source.fact_id ?? index}`,
    title: `${sourceVerb(source.status)}: ${safeSourceTitle(source)}`,
    detail: details.length > 0 ? details.join(' · ') : sourcePurposeLabel(source.purpose, source.kind),
    status,
    meta: sourceMetaChips({
      sourceUnitCount: units,
      sourceMediaCount: media,
      catalogCandidateCount: catalogCandidates,
      memoryCandidateCount: memoryCandidates,
      rejectedCandidateCount: rejectedCandidates,
      attemptCount,
      maxAttempts,
    }),
    cached: Boolean('input_cache_reused' in source ? source.input_cache_reused : false),
  }
}

function sourceLearningEventMeta(event: OnboardingSourceLearningEvent) {
  return sourceMetaChips({
    sourceUnitCount: Number(event.source_unit_count ?? 0),
    sourceMediaCount: Number(event.source_media_count ?? 0),
    catalogCandidateCount: Number(event.catalog_candidate_count ?? 0),
    memoryCandidateCount: Number(event.memory_candidate_count ?? 0),
    rejectedCandidateCount: Number(event.rejected_candidate_count ?? 0),
    attemptCount: Number(event.attempt_count ?? 0),
    maxAttempts: Number(event.max_attempts ?? 0),
  })
}

function sourceMetaChips({
  sourceUnitCount,
  sourceMediaCount,
  catalogCandidateCount,
  memoryCandidateCount,
  rejectedCandidateCount,
  attemptCount,
  maxAttempts,
}: {
  sourceUnitCount: number
  sourceMediaCount: number
  catalogCandidateCount: number
  memoryCandidateCount: number
  rejectedCandidateCount: number
  attemptCount: number
  maxAttempts: number
}) {
  return [
    catalogCandidateCount > 0 ? `${catalogCandidateCount} katalog` : null,
    memoryCandidateCount > 0 ? `${memoryCandidateCount} bilim` : null,
    sourceMediaCount > 0 ? `${sourceMediaCount} media` : null,
    sourceUnitCount > 0 ? `${sourceUnitCount} dalil` : null,
    rejectedCandidateCount > 0 ? `${rejectedCandidateCount} rad` : null,
    attemptCount > 0 && maxAttempts > 0 ? `${attemptCount}/${maxAttempts}` : null,
  ].filter(Boolean) as string[]
}

function dedupeRailEvents(events: RailEvent[]) {
  const byId = new Map<string, RailEvent>()
  for (const event of events) byId.set(event.id, event)
  return Array.from(byId.values())
}

function railProgressValue({
  readiness,
  runtime,
  sourceLearning,
  pendingReviewCount,
  working,
  hasDraftSources,
  onboardingIncomplete,
}: {
  readiness?: WorkspaceOSProjection['readiness']
  runtime?: OnboardingRuntimeProjection
  sourceLearning?: OnboardingRuntimeProjection['source_learning']
  pendingReviewCount: number
  working: boolean
  hasDraftSources: boolean
  onboardingIncomplete: boolean
}) {
  const raw = Math.max(
    readiness?.percent ?? 0,
    runtime?.percent ?? 0,
    sourceLearning?.percent ?? 0,
    working ? 8 : 0,
  )
  if (hasDraftSources) return clampProgress(Math.min(raw, 62), 0, 62)
  if (readiness?.status === 'ready' && pendingReviewCount === 0 && !onboardingIncomplete) {
    return 100
  }
  if (working) return clampProgress(raw, 8, 92)
  if (pendingReviewCount > 0) return clampProgress(Math.max(raw, 72), 0, 90)
  if (readiness?.status === 'degraded') return clampProgress(Math.max(raw, 45), 0, 80)
  if (readiness?.status === 'needs_review') return clampProgress(Math.max(raw, 70), 0, 90)
  if (readiness?.status === 'not_provisioned' || onboardingIncomplete) {
    return clampProgress(raw, 0, 88)
  }
  return clampProgress(raw, 0, 95)
}

function clampProgress(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, Math.round(value)))
}

function sourceVerb(status: string) {
  if (['failed', 'retrying', 'missing'].includes(status)) return 'Tekshirish'
  if (['learned', 'done', 'ready', 'review_ready', 'needs_review'].includes(status)) return 'Topildi'
  if (['conflict'].includes(status)) return 'Konflikt'
  return 'O‘qilmoqda'
}

function sourceStageDetail(stage: string, kind: string) {
  if (stage === 'fetching_telegram') return 'Telegramdan post va media olinmoqda'
  if (stage === 'ingesting') return 'Dalillar bo‘linmoqda'
  if (stage === 'using_cache') return 'Saqlangan dalil qayta ishlatilmoqda'
  if (stage === 'extracting') return 'Katalog, bilim va qoidalar ajratilmoqda'
  if (kind === 'voice_note') return 'Audio avval matnga aylantiriladi'
  return null
}

function railStatus(status: string): RailEventStatus {
  if (['failed', 'missing', 'dlq'].includes(status)) return 'failed'
  if (['retrying', 'conflict', 'needs_review', 'review_ready'].includes(status)) return 'warning'
  if (['learned', 'done', 'ready', 'completed'].includes(status)) return 'done'
  if (['learning', 'running', 'queued'].includes(status)) return 'running'
  return 'queued'
}

function safeSourceTitle(source: { label?: string | null; kind: string }) {
  const label = String(source.label ?? '').trim()
  if (label && label.toLowerCase() !== 'manba') return label
  return sourceKindLabel(source.kind)
}

function sourceKindLabel(kind: string) {
  if (kind === 'website') return 'Sayt'
  if (kind === 'telegram_channel') return 'Telegram kanal'
  if (kind === 'file') return 'Fayl'
  if (kind === 'screenshot') return 'Rasm yoki skrinshot'
  if (kind === 'text') return 'Qo‘lda yozilgan matn'
  if (kind === 'voice_note') return 'Audio matni'
  return 'Biznes manbasi'
}

function sourcePurposeLabel(purpose: string | undefined, kind: string) {
  if (purpose === 'agent_data') return 'Agent qoidalari va yozish uslubi uchun'
  if (kind === 'telegram_channel') return 'Kanal postlari o‘qiladi'
  if (kind === 'website') return 'Sayt sahifalari o‘qiladi'
  return 'Brain uchun dalil sifatida saqlanadi'
}

function readinessLabel(status?: string, onboardingIncomplete = false) {
  if (onboardingIncomplete && status === 'not_provisioned') return 'Yakunlanmagan'
  if (status === 'ready') return 'Tayyor'
  if (status === 'needs_review') return 'Ko‘rib chiqish'
  if (status === 'degraded') return 'Yordam kerak'
  if (status === 'not_provisioned') return 'Yig‘ilmagan'
  return 'Kutilmoqda'
}

function statusDotClass(tone: 'success' | 'warning' | 'muted') {
  if (tone === 'success') return 'size-2 rounded-full bg-emerald-500'
  if (tone === 'warning') return 'size-2 rounded-full bg-amber-500'
  return 'size-2 rounded-full bg-muted-foreground/30'
}

function activityDotClass(status: RailEventStatus) {
  if (status === 'running') return 'bg-blue-500'
  if (status === 'done') return 'bg-emerald-500'
  if (status === 'failed') return 'bg-destructive'
  if (status === 'warning') return 'bg-amber-500'
  return 'bg-muted-foreground/40'
}

function skillSummary(agents: WorkspaceOSProjection['agents']) {
  const count = agents.reduce((sum, agent) => sum + agent.skill_count, 0)
  if (count > 0) return `${count} skill`
  return 'agentlar tayyor bo‘lganda'
}
