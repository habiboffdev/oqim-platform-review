import {
  BookOpen,
  ChatCircleText,
  ChatTeardropText,
  Cube,
  ListBullets,
  PencilSimpleLine,
  WarningCircle,
} from '@phosphor-icons/react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Progress, ProgressLabel, ProgressValue } from '@/components/ui/progress'
import type {
  OnboardingRuntimeProjection,
  OnboardingRuntimeStage,
  OnboardingSourceLearningEvent,
  OnboardingSourceLearningSource,
  WorkspaceOSProjection,
} from '@/lib/types'
import { uz } from '@/lib/uz'
import { AgentMarkdownPreview, AgentStatusItem } from './agent-markdown-preview'
import {
  historyLearningCustomerCount,
  historyLearningDetail,
  historyProcessedDetail,
  onboardingHistoryRuntimeDetail,
  runtimeDetailText,
  runtimeStateLabel,
  sellerSafeSourceTitle,
  sellerSafeStatusText,
  sourceLearningReasonLabel,
  sourceLearningStatusLabel,
  voiceDiscoveryLabel,
  voiceLearningDetail,
} from './copy'
import { FactReviewList } from './fact-review-list'
import { LaunchSummaryPanel } from './launch-summary-panel'
import { LearnedCollapsibleRow } from './learned-collapsible-row'
import { ProductPreviewTable, type ProductPendingSourceSignal } from './product-preview'
import type {
  DefaultAgentKey,
  LaunchStep,
  LearnedReviewActionInput,
  Phase,
  PermissionModeKey,
} from './types'

export type OnboardingActivityStatus = 'queued' | 'running' | 'done' | 'failed' | 'warning'

export interface OnboardingActivityEvent {
  id: string
  title: string
  detail: string
  status: OnboardingActivityStatus
}

export function LearnedOutputPanel({
  phase,
  runtime,
  workspaceOS,
  isLoading,
  error,
  startFailed,
  activityEvents,
  isSourceLearning,
  draftSourceCount,
  enabledDefaultAgents,
  permissionMode,
  launchStep,
  reviewActionPending,
  onRetryLearning,
  onReviewAction,
}: {
  phase: Phase
  runtime: OnboardingRuntimeProjection | undefined
  workspaceOS: WorkspaceOSProjection | undefined
  isLoading: boolean
  error: Error | null
  startFailed: boolean
  activityEvents: OnboardingActivityEvent[]
  isSourceLearning: boolean
  draftSourceCount?: number
  enabledDefaultAgents: DefaultAgentKey[]
  permissionMode: PermissionModeKey
  launchStep: LaunchStep
  reviewActionPending: string | null
  onRetryLearning: () => void
  onReviewAction: (input: LearnedReviewActionInput) => void
}) {
  if (phase === 'preferences') {
    return (
      <AgentLearningPanel
        runtime={runtime}
        workspaceOS={workspaceOS}
        startFailed={startFailed}
        activityEvents={activityEvents}
        isSourceLearning={isSourceLearning}
        reviewActionPending={reviewActionPending}
        onRetryLearning={onRetryLearning}
        onReviewAction={onReviewAction}
      />
    )
  }
  if (phase === 'credentials') {
    return (
      <LaunchSummaryPanel
        runtime={runtime}
        workspaceOS={workspaceOS}
        enabledDefaultAgents={enabledDefaultAgents}
        permissionMode={permissionMode}
        launchStep={launchStep}
      />
    )
  }

  return (
    <BusinessBrainLearnedPanel
      runtime={runtime}
      isLoading={isLoading}
      error={error}
      startFailed={startFailed}
      activityEvents={activityEvents}
      isSourceLearning={isSourceLearning}
      draftSourceCount={draftSourceCount ?? 0}
      reviewActionPending={reviewActionPending}
      onRetryLearning={onRetryLearning}
      onReviewAction={onReviewAction}
    />
  )
}

function BusinessBrainLearnedPanel({
  runtime,
  isLoading,
  error,
  startFailed,
  activityEvents,
  isSourceLearning,
  draftSourceCount,
  reviewActionPending,
  onRetryLearning,
  onReviewAction,
}: {
  runtime: OnboardingRuntimeProjection | undefined
  isLoading: boolean
  error: Error | null
  startFailed: boolean
  activityEvents: OnboardingActivityEvent[]
  isSourceLearning: boolean
  draftSourceCount: number
  reviewActionPending: string | null
  onRetryLearning: () => void
  onReviewAction: (input: LearnedReviewActionInput) => void
}) {
  const learnedReview = runtime?.learned_review
  const sourceLearning = runtime?.source_learning
  const progress = runtime?.progress
  const percent = runtime?.percent ?? progress?.percent ?? 0
  const isRunning = Boolean(runtime?.is_running)
  const isComplete = Boolean(runtime?.is_terminal && !runtime?.is_running)
  const products = learnedReview?.products ?? []
  const knowledge = learnedReview?.knowledge ?? []
  const rules = learnedReview?.rules ?? []
  const sourceSignals = sourceLearningCandidateSignals(sourceLearning)
  const historyCount = progress ? historyLearningCustomerCount(progress) : 0
  const historyActive = historyCount > 0 || Boolean(progress?.history_replayed_messages)
  const hasUnprocessedDraft = draftSourceCount > 0 && !isSourceLearning && !isRunning
  const hasLearningSignal = Boolean(
    hasUnprocessedDraft
      || isRunning
      || percent > 0
      || products.length > 0
      || knowledge.length > 0
      || rules.length > 0
      || historyActive
      || (sourceLearning?.summary.total ?? 0) > 0,
  )
  const showLearnedArtifacts = hasLearningSignal && !hasUnprocessedDraft
  const hasRetryableSources = Boolean(
    sourceLearning
      && (
        sourceLearning.sources.some((source) => source.retryable)
        || sourceLearning.status === 'failed'
        || sourceLearning.status === 'retrying'
        || sourceLearning.summary.failed > 0
        || sourceLearning.summary.retrying > 0
      ),
  )

  return (
    <Card className="flex h-full min-h-0 flex-col overflow-hidden py-0">
      <CardHeader className="px-6 py-6">
        <CardTitle className="font-sans text-2xl font-semibold tracking-tight">OQIM nimalarni o‘rganyapti</CardTitle>
        <CardDescription>
          {!hasLearningSignal
            ? 'Manba qo‘shing. OQIM boshlamaguncha bu yerda eski natija ko‘rsatilmaydi.'
            : hasUnprocessedDraft
            ? `${draftSourceCount} ta yangi manba tayyor. Davom eting, keyin OQIM o‘qishni jonli ko‘rsatadi.`
            : isRunning
            ? 'O‘qish davom etmoqda, natijalar shu yerda paydo bo‘ladi.'
            : 'Topilgan bilimlar, suhbat tarixi va yetishmayotgan manbalar shu yerda ko‘rinadi.'}
        </CardDescription>
        <CardAction>
          <Badge variant={hasUnprocessedDraft ? 'outline' : startFailed || runtime?.is_dlq ? 'error' : runtime?.is_running ? 'info' : runtime?.is_terminal ? 'success' : 'outline'}>
            {hasUnprocessedDraft ? 'Davom eting' : runtimeStateLabel(runtime, isLoading, startFailed)}
          </Badge>
        </CardAction>
      </CardHeader>
      <CardContent className="grid min-h-0 flex-1 gap-3 overflow-y-auto px-6 pb-6">
        <Progress value={hasUnprocessedDraft ? 0 : hasLearningSignal ? Math.max(percent, isRunning ? 8 : 0) : 0} className="mb-2">
          <ProgressLabel>O‘rganish jarayoni</ProgressLabel>
          <ProgressValue>{hasUnprocessedDraft ? 0 : Math.round(percent)}%</ProgressValue>
        </Progress>

        {hasUnprocessedDraft ? (
          <QueuedDraftStream draftSourceCount={draftSourceCount} />
        ) : (
          <LearningActivityStream
            phase="sources"
            runtime={runtime}
            events={activityEvents}
            isWorking={isSourceLearning || isRunning}
          />
        )}

        {hasUnprocessedDraft ? (
          <div className="rounded-lg border border-dashed border-border px-4 py-3 text-sm leading-6 text-muted-foreground">
            <span className="font-medium text-foreground">{draftSourceCount} ta dalil manbasi hali o‘qilmadi.</span>{' '}
            Davom eting: OQIM ularni dalil, katalog, bilim va qoida takliflariga ajratadi. Audio faqat tahrirlangan matn sifatida ishlatiladi.
          </div>
        ) : null}

        {!hasLearningSignal ? (
          <div className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm leading-6 text-muted-foreground">
            Hali manba o‘qilmadi. Sayt, fayl yoki Telegram kanal qo‘shing, keyin OQIM topgan narsalar shu yerda ochiladi.
          </div>
        ) : null}

        {showLearnedArtifacts && progress?.voice_profile_degraded ? (
          <p className="text-sm leading-6 text-muted-foreground">
            {sellerSafeStatusText('voice_profile_degraded')}
          </p>
        ) : null}

        {(error || startFailed || progress?.ai_learning_degraded) && (
          <Alert variant={startFailed ? 'destructive' : 'default'}>
            <WarningCircle />
            <AlertTitle>{startFailed || progress?.ai_learning_degraded ? uz.onboarding.learningDegraded : 'Holat yuklanmadi'}</AlertTitle>
            <AlertDescription>
              {startFailed || progress?.ai_learning_degraded
                ? uz.onboarding.learningDegradedDesc
                : 'Telegram ulanish holati hozircha kelmadi. OQIM buni taxmin qilib ko‘rsatmaydi.'}
            </AlertDescription>
          </Alert>
        )}

        {showLearnedArtifacts ? (
        <LearnedCollapsibleRow
          icon={<Cube />}
          title="Katalog"
          status={products.length > 0 ? 'Topildi' : isRunning ? 'O‘qilmoqda' : sourceSignals.catalog.length > 0 ? 'Taklif bor' : 'Hali yo‘q'}
          variant={products.length > 0 ? 'success' : isRunning ? 'info' : sourceSignals.catalog.length > 0 ? 'warning' : 'outline'}
          defaultOpen
        >
          <div className="grid gap-4">
            <div>
              <p className="font-medium">Katalogdan dastlabki namunalar</p>
              <p className="mt-1 text-sm text-muted-foreground">
                {products.length > 0
                  ? 'Topilgan mahsulotlar va ularning manbalari.'
                  : sourceSignals.catalog.length > 0
                    ? 'Mahsulotga o‘xshash dalillar topildi. OQIM ularni reviewga tayyorlaydi yoki qayta o‘qishni so‘raydi.'
                    : 'Mahsulot dalili topilsa shu yerda reviewga chiqadi.'}
              </p>
            </div>
            <ProductPreviewTable
              products={products}
              pendingSources={sourceSignals.catalog}
              isRunning={isRunning}
              isComplete={isComplete}
              disabled={reviewActionPending !== null}
              onReviewAction={onReviewAction}
            />
          </div>
        </LearnedCollapsibleRow>
        ) : null}

        {showLearnedArtifacts ? (
        <LearnedCollapsibleRow
          icon={<ListBullets />}
          title="Bilimlar"
          status={knowledge.length > 0 ? 'Topildi' : isRunning ? 'O‘qilmoqda' : sourceSignals.knowledge.length > 0 ? 'Taklif bor' : 'Hali yo‘q'}
          variant={knowledge.length > 0 ? 'success' : isRunning ? 'info' : sourceSignals.knowledge.length > 0 ? 'warning' : 'outline'}
          defaultOpen={knowledge.length > 0 || sourceSignals.knowledge.length > 0}
        >
          <p className="mb-3 text-sm leading-6 text-muted-foreground">
            Mijozga javob berishda ishlatiladigan narx, yetkazish, kafolat va xizmat maʼlumotlari.
          </p>
          {knowledge.length === 0 && sourceSignals.knowledge.length > 0 ? (
            <PendingSourceSignalList signals={sourceSignals.knowledge} />
          ) : null}
          <FactReviewList
            items={knowledge}
            disabled={reviewActionPending !== null}
            onReviewAction={onReviewAction}
          />
        </LearnedCollapsibleRow>
        ) : null}

        {showLearnedArtifacts ? (
        <LearnedCollapsibleRow
          icon={<BookOpen />}
          title="Qoidalar"
          status={rules.length > 0 ? 'Tasdiqlash kerak' : isRunning ? 'O‘qilmoqda' : 'Hali yo‘q'}
          variant={rules.length > 0 ? 'warning' : isRunning ? 'info' : 'outline'}
          defaultOpen={rules.length > 0}
        >
          <p className="mb-3 text-sm leading-6 text-muted-foreground">
            Agent qanday ishlashi, nimani taxmin qilmasligi va qachon sizdan ruxsat so‘rashi.
          </p>
          <FactReviewList
            items={rules}
            disabled={reviewActionPending !== null}
            onReviewAction={onReviewAction}
          />
        </LearnedCollapsibleRow>
        ) : null}

        {historyActive && !hasUnprocessedDraft ? (
          <LearnedCollapsibleRow
            icon={<ChatTeardropText />}
            title="Suhbat tarixi"
            status={historyCount > 0 ? `${historyCount} mijoz` : 'O‘qilmoqda'}
            variant={historyCount > 0 ? 'success' : 'info'}
            defaultOpen
          >
            <div className="grid gap-1.5 text-sm leading-6 text-muted-foreground">
              <p>
                {uz.onboarding.learningCustomers(
                  historyCount,
                  progress?.history_learning_conversation_limit ?? 50,
                )}
              </p>
              <p>{historyLearningDetail(progress ?? null)}</p>
              {progress ? (() => {
                const processed = historyProcessedDetail(progress)
                return processed ? <p>{processed}</p> : null
              })() : null}
            </div>
          </LearnedCollapsibleRow>
        ) : null}

        {showLearnedArtifacts ? <SourceTroubleList sources={sourceLearning?.sources ?? []} /> : null}

        {hasRetryableSources || startFailed || progress?.ai_learning_degraded || progress?.voice_profile_degraded || progress?.contact_classification_degraded ? (
          <Button type="button" variant="outline" size="sm" className="mt-1 w-fit" onClick={onRetryLearning}>
            {uz.onboarding.retryLearning}
          </Button>
        ) : null}
      </CardContent>
    </Card>
  )
}

function PendingSourceSignalList({ signals }: { signals: ProductPendingSourceSignal[] }) {
  return (
    <div className="mb-3 grid gap-2 rounded-lg border border-dashed border-border px-3 py-3 text-sm">
      {signals.slice(0, 4).map((signal) => (
        <div key={signal.id} className="grid gap-1 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
          <div className="min-w-0">
            <p className="truncate font-medium">{signal.title}</p>
            <p className="line-clamp-2 text-muted-foreground">{signal.detail}</p>
          </div>
          <Badge variant="outline" className="w-fit">
            {signal.statusLabel}
          </Badge>
        </div>
      ))}
    </div>
  )
}

function sourceLearningCandidateSignals(sourceLearning: OnboardingRuntimeProjection['source_learning'] | undefined) {
  const sources = sourceLearning?.sources ?? []
  const events = sourceLearning?.events ?? []
  const catalogSignals = dedupePendingSignals([
    ...sources
      .filter((source) => Number(source.catalog_candidate_count ?? 0) > 0)
      .map((source) => sourceToPendingSignal(source, 'catalog')),
    ...events
      .filter((event) => Number(event.catalog_candidate_count ?? 0) > 0)
      .map((event) => eventToPendingSignal(event, 'catalog')),
  ])
  const knowledgeSignals = dedupePendingSignals([
    ...sources
      .filter((source) => Number(source.memory_candidate_count ?? 0) > 0)
      .map((source) => sourceToPendingSignal(source, 'knowledge')),
    ...events
      .filter((event) => Number(event.memory_candidate_count ?? 0) > 0)
      .map((event) => eventToPendingSignal(event, 'knowledge')),
  ])
  return {
    catalog: catalogSignals,
    knowledge: knowledgeSignals,
  }
}

function dedupePendingSignals(signals: ProductPendingSourceSignal[]) {
  const byId = new Map<string, ProductPendingSourceSignal>()
  for (const signal of signals) byId.set(signal.id, signal)
  return Array.from(byId.values())
}

function sourceToPendingSignal(
  source: OnboardingSourceLearningSource,
  kind: 'catalog' | 'knowledge',
): ProductPendingSourceSignal {
  const catalogCount = Number(source.catalog_candidate_count ?? 0)
  const knowledgeCount = Number(source.memory_candidate_count ?? 0)
  const unitCount = Number(source.source_unit_count ?? 0)
  const mediaCount = Number(source.source_media_count ?? 0)
  const pieces = [
    kind === 'catalog' && catalogCount > 0 ? `${catalogCount} ta katalog taklifi` : null,
    kind === 'knowledge' && knowledgeCount > 0 ? `${knowledgeCount} ta bilim taklifi` : null,
    unitCount > 0 ? `${unitCount} ta dalil` : null,
    mediaCount > 0 ? `${mediaCount} ta media` : null,
    source.input_cache_reused ? 'cache ishlatildi' : null,
  ].filter(Boolean)
  return {
    id: `pending-${kind}:${source.source_ref}`,
    title: sellerSafeSourceTitle(source.label, source.kind),
    detail: pieces.length > 0 ? pieces.join(' · ') : sourcePurposeText(source),
    statusLabel: sourceLearningStatusLabel(source.status),
  }
}

function eventToPendingSignal(
  event: OnboardingSourceLearningEvent,
  kind: 'catalog' | 'knowledge',
): ProductPendingSourceSignal {
  const catalogCount = Number(event.catalog_candidate_count ?? 0)
  const knowledgeCount = Number(event.memory_candidate_count ?? 0)
  const unitCount = Number(event.source_unit_count ?? 0)
  const mediaCount = Number(event.source_media_count ?? 0)
  const pieces = [
    kind === 'catalog' && catalogCount > 0 ? `${catalogCount} ta katalog taklifi` : null,
    kind === 'knowledge' && knowledgeCount > 0 ? `${knowledgeCount} ta bilim taklifi` : null,
    unitCount > 0 ? `${unitCount} ta dalil` : null,
    mediaCount > 0 ? `${mediaCount} ta media` : null,
    event.input_cache_reused ? 'cache ishlatildi' : null,
  ].filter(Boolean)
  return {
    id: `pending-${kind}:${event.source_ref}`,
    title: event.title_uz,
    detail: pieces.length > 0 ? pieces.join(' · ') : runtimeDetailText(event.detail_uz, 'Dalil oqimi yangilandi.'),
    statusLabel: sourceLearningStatusLabel(event.status),
  }
}

function SourceTroubleList({
  sources,
}: {
  sources: Array<{
    id?: string | number
    kind: string
    label?: string | null
    status: string
    retryable?: boolean
    degraded_reasons?: string[] | null
  }>
}) {
  // Brief 01: surface failed/retrying source rows with plain Uzbek reasons so
  // the user can act on them without a third rail. The single retry button
  // lives at the bottom of the learned panel; this list only displays state.
  const trouble = sources.filter(
    (source) =>
      source.retryable
      || source.status === 'failed'
      || source.status === 'retrying'
      || source.status === 'missing',
  )
  if (trouble.length === 0) return null

  return (
    <div className="grid gap-2 rounded-lg border border-border bg-background px-4 py-3">
      <span className="text-sm font-medium">Manbalar holati</span>
      <ul className="grid gap-1.5 text-sm">
        {trouble.slice(0, 5).map((source, index) => {
          const reasonText =
            sourceLearningReasonLabel(source.degraded_reasons?.[0] ?? source.status)
            ?? sourceLearningStatusLabel(source.status)
          return (
            <li
              key={String(source.id ?? `${source.kind}:${index}`)}
              className="flex items-baseline justify-between gap-2"
            >
              <span className="truncate text-foreground">
                {sellerSafeSourceTitle(source.label, source.kind)}
              </span>
              <span className="shrink-0 text-muted-foreground">{reasonText}</span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

function AgentLearningPanel({
  runtime,
  workspaceOS,
  startFailed,
  activityEvents,
  isSourceLearning,
  reviewActionPending,
  onRetryLearning,
  onReviewAction,
}: {
  runtime: OnboardingRuntimeProjection | undefined
  workspaceOS: WorkspaceOSProjection | undefined
  startFailed: boolean
  activityEvents: OnboardingActivityEvent[]
  isSourceLearning: boolean
  reviewActionPending: string | null
  onRetryLearning: () => void
  onReviewAction: (input: LearnedReviewActionInput) => void
}) {
  const progress = runtime?.progress
  const learnedReview = runtime?.learned_review
  const rules = learnedReview?.rules ?? []
  const voiceDiscoveries = progress?.voice_discoveries ?? []
  const historyCount = progress ? historyLearningCustomerCount(progress) : 0
  const sellerAgent = workspaceOS?.agents.find((agent) => agent.package_key === 'seller' && agent.present)

  return (
    <Card className="flex h-full min-h-0 flex-col overflow-hidden py-0">
      <CardHeader className="px-6 py-6">
        <CardTitle className="font-sans text-2xl font-semibold tracking-tight">Agent nimani o‘rgandi</CardTitle>
        <CardDescription>So‘nggi 50 ta suhbatdan va qo‘shilgan qoidalardan agent fayli tuziladi.</CardDescription>
      </CardHeader>
      <CardContent className="grid min-h-0 flex-1 gap-3 overflow-y-auto px-6 pb-6">
        <LearningActivityStream
          phase="preferences"
          runtime={runtime}
          events={activityEvents}
          isWorking={isSourceLearning || Boolean(runtime?.is_running)}
        />

        <LearnedCollapsibleRow
          icon={<PencilSimpleLine />}
          title="Yozish uslubi"
          status={progress?.voice_profile_ready ? 'Topildi' : progress?.voice_profile_degraded ? 'Yordam kerak' : 'O‘rganmoqda'}
          variant={progress?.voice_profile_ready ? 'success' : progress?.voice_profile_degraded ? 'warning' : 'info'}
          defaultOpen={voiceDiscoveries.length > 0}
        >
          {voiceDiscoveries.length > 0 ? (
            <div className="grid gap-2">
              <p className="text-sm text-muted-foreground">{uz.onboarding.voiceLearningFound}</p>
              {voiceDiscoveries.slice(0, 4).map((item, index) => (
                <Badge key={`${voiceDiscoveryLabel(item)}:${index}`} variant="outline" className="w-fit">
                  {voiceDiscoveryLabel(item)}
                </Badge>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">{voiceLearningDetail(progress ?? {
              workspace_id: 0,
              phase: 'idle',
              percent: 0,
              contacts_found: 0,
              customers_identified: 0,
              products_extracted: 0,
              knowledge_items: 0,
              voice_profile_ready: false,
              voice_discoveries: [],
              completed: false,
              errors: [],
            })}</p>
          )}
        </LearnedCollapsibleRow>

        <LearnedCollapsibleRow
          icon={<ListBullets />}
          title="Qoidalar"
          status={rules.length > 0 ? 'Topildi' : 'Kutilmoqda'}
          variant={rules.length > 0 ? 'success' : 'outline'}
          defaultOpen={rules.length > 0}
        >
          <FactReviewList
            items={rules}
            disabled={reviewActionPending !== null}
            onReviewAction={onReviewAction}
          />
        </LearnedCollapsibleRow>

        <LearnedCollapsibleRow
          icon={<ChatCircleText />}
          title="Suhbatlardan o‘rganilgan"
          status={`${historyCount} suhbat`}
          variant={historyCount > 0 ? 'success' : 'outline'}
          defaultOpen
        >
          <p className="text-sm leading-6 text-muted-foreground">
            {progress ? onboardingHistoryRuntimeDetail(progress) : 'Telegram ulanganda OQIM oxirgi 50 ta muhim suhbatdan o‘rganadi.'}
          </p>
        </LearnedCollapsibleRow>

        <AgentMarkdownPreview
          progress={progress}
          rules={rules}
          documentPreview={sellerAgent?.document_preview}
          skillNames={sellerAgent?.skill_names}
        />

        <CardFooter className="mt-2 grid gap-4 rounded-xl border px-5 py-4 md:grid-cols-2 2xl:grid-cols-[1fr_1fr_1fr_auto]">
          <AgentStatusItem
            label="Qoidalar"
            value={rules.length > 0 ? `${rules.length} ta qoida topildi` : 'Hali topilmadi'}
            variant={rules.length > 0 ? 'success' : 'outline'}
          />
          <AgentStatusItem
            label="Ovoz holati"
            value={progress?.voice_profile_ready ? 'Tayyor' : 'Hali kuchsiz'}
            variant={progress?.voice_profile_ready ? 'success' : 'warning'}
          />
          <AgentStatusItem label="Ruxsat" value="Ko‘rib chiqiladi" variant="warning" />
          <Button type="button" variant="outline" onClick={onRetryLearning} disabled={startFailed}>
            Qayta o‘rganish
          </Button>
        </CardFooter>
      </CardContent>
    </Card>
  )
}

function QueuedDraftStream({ draftSourceCount }: { draftSourceCount: number }) {
  return (
    <div
      role="status"
      aria-live="polite"
      aria-label="Jonli onboarding jarayoni"
      className="rounded-lg border border-border/80 bg-muted/15 px-3.5 py-3"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">Jonli oqim</p>
          <p className="mt-1 truncate text-sm font-medium leading-5 text-foreground">
            {draftSourceCount} ta dalil manbasi tayyor
          </p>
        </div>
        <Badge variant="outline">Davom eting</Badge>
      </div>
      <div className="mt-2.5 h-1 overflow-hidden rounded-full bg-background">
        <div className="h-full w-0 rounded-full bg-primary" />
      </div>
      <p className="mt-2 line-clamp-2 text-sm leading-5 text-muted-foreground">
        Hali o‘qilmadi. Davom etganda OQIM har bir dalil manbasini katalog, bilim va qoida takliflariga ajratadi.
      </p>
    </div>
  )
}

function LearningActivityStream({
  phase,
  runtime,
  events,
  isWorking,
}: {
  phase: 'sources' | 'preferences'
  runtime: OnboardingRuntimeProjection | undefined
  events: OnboardingActivityEvent[]
  isWorking: boolean
}) {
  const progress = runtime?.progress
  const sourceSummary = runtime?.source_learning?.summary
  const durableEvents = runtimeActivityEvents({ phase, runtime, localEvents: events, isWorking })
  const fallbackEvents: OnboardingActivityEvent[] = (() => {
    if (durableEvents.length > 0) return []
    if (isWorking || runtime?.is_running) {
      return [{
        id: 'runtime-working',
        title: phase === 'preferences' ? 'Agent fayli yig‘ilmoqda' : 'Dalillar o‘qilmoqda',
        detail: runtime?.stages.find((stage) => stage.id === runtime.current_stage_id)?.label
          ?? (phase === 'preferences'
            ? 'Ovoz, qoidalar va suhbat namunalaridan AGENT.md tayyorlanadi.'
            : 'OQIM katalog, fakt va bilimlarni ajratib ko‘rsatadi.'),
        status: 'running',
      }]
    }
    if ((sourceSummary?.total ?? 0) > 0) {
      return [{
        id: 'source-summary',
        title: `${sourceSummary?.total ?? 0} ta dalil manbasi navbatda`,
        detail: 'Dalillar qayta o‘qilishi mumkin. Natija tasdiqlanmaguncha agentga yakuniy haqiqat bo‘lmaydi.',
        status: sourceSummary?.failed ? 'failed' : 'queued',
      }]
    }
    return [{
      id: 'idle',
      title: phase === 'preferences' ? 'Agent uchun qoida qo‘shing' : 'Manba qo‘shing',
      detail: phase === 'preferences'
        ? 'Davom etishda OQIM yozish uslubi, qoidalar va ruxsatlarni agent fayliga aylantiradi.'
        : 'Davom etish bosilganda OQIM qo‘shilgan manbalarni o‘qiydi va topgan narsasini shu yerda ko‘rsatadi.',
      status: 'queued',
    }]
  })()
  const visibleEvents = (durableEvents.length > 0 ? durableEvents : fallbackEvents).slice(0, 5)
  const activeEvent = visibleEvents[0]
  const streamWorking = isWorking || activeEvent?.status === 'running'
  const progressValue = Math.max(
    runtime?.percent ?? progress?.percent ?? 0,
    runtime?.source_learning?.percent ?? 0,
    streamWorking ? 8 : 0,
  )

  return (
    <div
      role="status"
      aria-live="polite"
      aria-label="Jonli onboarding jarayoni"
      className="rounded-lg border border-border/80 bg-muted/15 px-3.5 py-3"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">Jonli oqim</p>
          {activeEvent ? (
            <p className="mt-1 truncate text-sm font-medium leading-5 text-foreground">{activeEvent.title}</p>
          ) : null}
        </div>
        <Badge variant={streamWorking ? 'info' : activeEvent?.status === 'failed' ? 'error' : activeEvent?.status === 'warning' ? 'warning' : 'outline'}>
          {streamWorking ? 'Ishlayapti' : activeEvent?.status === 'failed' ? 'Qayta urinish' : activeEvent?.status === 'warning' ? 'Ko‘rib chiqish' : 'Kutilmoqda'}
        </Badge>
      </div>
      <div className="mt-2.5 h-1 overflow-hidden rounded-full bg-background">
        <div
          className="h-full rounded-full bg-primary transition-[width]"
          style={{ width: `${Math.max(0, Math.min(100, progressValue))}%` }}
        />
      </div>
      {activeEvent ? (
        <p className="mt-2 line-clamp-2 text-sm leading-5 text-muted-foreground">{activeEvent.detail}</p>
      ) : null}
      {visibleEvents.length > 1 ? (
        <div className="mt-2.5 grid gap-1.5 border-t border-border/70 pt-2.5">
          {visibleEvents.slice(1).map((event) => (
            <div key={event.id} className="grid grid-cols-[0.75rem_minmax(0,1fr)] gap-3">
              <span className={`mt-1.5 size-2 rounded-full ${activityDotClass(event.status)}`} />
              <span className="min-w-0">
                <span className="block text-sm font-medium leading-5">{event.title}</span>
                <span className="block line-clamp-1 text-sm leading-5 text-muted-foreground">{event.detail}</span>
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  )
}

function runtimeActivityEvents({
  phase,
  runtime,
  localEvents,
  isWorking,
}: {
  phase: 'sources' | 'preferences'
  runtime: OnboardingRuntimeProjection | undefined
  localEvents: OnboardingActivityEvent[]
  isWorking: boolean
}) {
  const sourceEvents = (runtime?.source_learning?.events ?? []).map(sourceLearningEventToActivity)
  const stageEvents = runtimeStageEvents(runtime)
  const sourceRows = sourceEvents.length > 0
    ? []
    : (runtime?.source_learning?.sources ?? []).slice(0, 4).map(sourceToActivity)
  const historyEvent = historyActivityEvent(runtime)
  const voiceEvent = phase === 'preferences' ? voiceActivityEvent(runtime) : null
  const events = dedupeActivityEvents([
    ...localEvents,
    ...sourceEvents,
    ...stageEvents,
    ...sourceRows,
    ...(historyEvent ? [historyEvent] : []),
    ...(voiceEvent ? [voiceEvent] : []),
  ])
  if (events.length > 0) return events.slice(-7).reverse()
  if (isWorking && runtime?.current_stage_id) {
    const stage = runtime.stages.find((item) => item.id === runtime.current_stage_id)
    if (stage) return [stageToActivity(stage, true)]
  }
  return []
}

function sourceLearningEventToActivity(event: OnboardingSourceLearningEvent): OnboardingActivityEvent {
  return {
    id: event.event_ref,
    title: event.title_uz,
    detail: runtimeDetailText(event.detail_uz, 'Manba holati yangilandi.'),
    status: activityStatus(event.status),
  }
}

function runtimeStageEvents(runtime: OnboardingRuntimeProjection | undefined): OnboardingActivityEvent[] {
  if (!runtime) return []
  return runtime.stages
    .filter((stage) => {
      if (stage.status === 'pending') return stage.id === runtime.current_stage_id
      return ['running', 'completed', 'failed', 'dlq'].includes(stage.status)
    })
    .map((stage) => stageToActivity(stage, stage.id === runtime.current_stage_id))
}

function stageToActivity(stage: OnboardingRuntimeStage, current: boolean): OnboardingActivityEvent {
  return {
    id: `runtime-stage:${stage.id}:${stage.status}`,
    title: current && stage.status === 'pending' ? `${stage.label} kutilmoqda` : stage.label,
    detail: runtimeDetailText(stage.error ?? stage.detail, stageDetail(stage.status)),
    status: activityStatus(stage.status),
  }
}

function sourceToActivity(source: OnboardingSourceLearningSource): OnboardingActivityEvent {
  const details = [
    source.catalog_candidate_count ? `${source.catalog_candidate_count} ta katalog taklifi` : null,
    source.memory_candidate_count ? `${source.memory_candidate_count} ta bilim taklifi` : null,
    source.source_unit_count ? `${source.source_unit_count} ta dalil` : null,
    source.source_media_count ? `${source.source_media_count} ta media` : null,
    source.rejected_candidate_count ? `${source.rejected_candidate_count} ta ishonchsiz taklif ajratildi` : null,
    source.degraded_reasons?.[0] ? sourceLearningReasonLabel(source.degraded_reasons[0]) : null,
  ].filter(Boolean)
  return {
    id: `source:${source.source_ref}`,
    title: `${sourceActionVerb(source.status)}: ${sellerSafeSourceTitle(source.label, source.kind)}`,
    detail: details.length > 0 ? details.join(' · ') : sourcePurposeText(source),
    status: activityStatus(source.status),
  }
}

function historyActivityEvent(runtime: OnboardingRuntimeProjection | undefined): OnboardingActivityEvent | null {
  const progress = runtime?.progress
  if (!progress) return null
  const customers = historyLearningCustomerCount(progress)
  const conversations = progress.history_replayed_conversations ?? 0
  const messages = progress.history_replayed_messages ?? 0
  if (customers <= 0 && conversations <= 0 && messages <= 0) return null
  return {
    id: 'runtime-history-learning',
    title: customers > 0 ? `${customers} mijozdan suhbat signali olindi` : 'Suhbat tarixi o‘qilmoqda',
    detail: historyProcessedDetail(progress) ?? historyLearningDetail(progress),
    status: runtime?.is_running ? 'running' : 'done',
  }
}

function voiceActivityEvent(runtime: OnboardingRuntimeProjection | undefined): OnboardingActivityEvent | null {
  const progress = runtime?.progress
  if (!progress) return null
  if (progress.voice_profile_ready) {
    return {
      id: 'runtime-voice-learning',
      title: 'Yozish uslubi topildi',
      detail: voiceLearningDetail(progress),
      status: 'done',
    }
  }
  if (progress.voice_profile_degraded) {
    return {
      id: 'runtime-voice-learning',
      title: 'Yozish uslubi uchun dalil kam',
      detail: voiceLearningDetail(progress),
      status: 'warning',
    }
  }
  return null
}

function dedupeActivityEvents(events: OnboardingActivityEvent[]) {
  const byId = new Map<string, OnboardingActivityEvent>()
  for (const event of events) byId.set(event.id, event)
  return Array.from(byId.values())
}

function stageDetail(status: string) {
  if (status === 'running') return 'Hozir bajarilmoqda. Natija tayyor bo‘lsa shu yerda paydo bo‘ladi.'
  if (status === 'completed') return 'Tayyor.'
  if (status === 'failed' || status === 'dlq') return 'Qayta urinish yoki tekshirish kerak.'
  return 'Navbatda.'
}

function sourceActionVerb(status: string) {
  if (['failed', 'missing'].includes(status)) return 'Tekshirish kerak'
  if (status === 'retrying') return 'Qayta urinmoqda'
  if (['learned', 'done', 'ready'].includes(status)) return 'Topildi'
  if (['review_ready', 'needs_review'].includes(status)) return 'Tasdiq kerak'
  if (status === 'conflict') return 'Konflikt'
  return 'O‘qilmoqda'
}

function sourcePurposeText(source: OnboardingSourceLearningSource) {
  if (source.purpose === 'agent_data') return 'Agent qoidalari, uslubi yoki ko‘nikmasi uchun o‘qiladi.'
  if (source.kind === 'telegram_channel') return 'Kanal postlari, rasmlar va eʼlonlardan dalil olinadi.'
  if (source.kind === 'website') return 'Sayt sahifalaridan mahsulot va savol-javob dalillari olinadi.'
  return 'Brain uchun alohida dalil sifatida saqlanadi.'
}

function activityStatus(status: string): OnboardingActivityStatus {
  if (['failed', 'missing', 'dlq'].includes(status)) return 'failed'
  if (['retrying', 'conflict', 'needs_review', 'review_ready'].includes(status)) return 'warning'
  if (['learned', 'done', 'ready', 'completed'].includes(status)) return 'done'
  if (['learning', 'running', 'queued'].includes(status)) return 'running'
  return 'queued'
}

function activityDotClass(status: OnboardingActivityStatus) {
  if (status === 'running') return 'bg-blue-500'
  if (status === 'done') return 'bg-emerald-500'
  if (status === 'failed') return 'bg-destructive'
  if (status === 'warning') return 'bg-amber-500'
  return 'bg-muted-foreground/40'
}
