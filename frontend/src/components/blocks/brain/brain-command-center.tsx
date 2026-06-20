import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import {
  BookOpen,
  CheckCircle,
  Database,
  FileArrowUp,
  GlobeHemisphereWest,
  Microphone,
  Rows,
  TelegramLogo,
  WarningCircle,
} from '@phosphor-icons/react'
import type { Icon } from '@phosphor-icons/react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Progress, ProgressLabel, ProgressValue } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import {
  readOnboardingFileSource,
  type OnboardingFileSource,
} from '@/lib/file-source'
import { uz } from '@/lib/uz'
import type {
  SellerAgentReply,
  BusinessBrainFactReadModel,
  BusinessBrainFactReviewActionInput,
  BusinessBrainManualFactUpdateInput,
  BusinessBrainSourceCreateInput,
  CatalogWorkspaceProduct,
  CommercialActionProposal,
  KnowledgeItem,
  OnboardingSourceLearningProjection,
} from '@/lib/types'
import {
  brainReadinessFacts,
  brainStats,
  conversationPairParts,
  editableFactText,
  factOwnerLabel,
  factPrimarySourceLabel,
  factRepairActionLabel,
  factSummary,
  factTitle,
  factsForSurface,
  surfaceForFact,
  sourceSummaries,
  visibleSourceLabels,
  type BrainSurface,
} from './brain-workspace-model'

export function BrainOverview({
  facts,
  products,
  knowledge,
  replies,
  proposals,
  loading,
  sourceLearning,
}: {
  facts: BusinessBrainFactReadModel[]
  products: CatalogWorkspaceProduct[]
  knowledge: KnowledgeItem[]
  replies: SellerAgentReply[]
  proposals: CommercialActionProposal[]
  loading: boolean
  sourceLearning?: OnboardingSourceLearningProjection
}) {
  const stats = brainStats({ facts, products, knowledge, replies, proposals })
  const readiness = brainReadinessFacts({ facts, products, knowledge })
  const sources = sourceSummaries(facts, sourceLearning?.sources)
  const sourceCopy = uz.workspaceUi.brain.sources
  const intake = [
    { label: sourceCopy.kinds.website, icon: GlobeHemisphereWest, count: sources.filter((source) => source.kind === 'Sayt').length },
    { label: sourceCopy.kinds.telegram, icon: TelegramLogo, count: sources.filter((source) => source.kind === 'Telegram').length },
    { label: sourceCopy.kinds.file, icon: FileArrowUp, count: sources.filter((source) => ['PDF', 'Jadval', 'Matn', 'Rasm'].includes(source.kind)).length },
    { label: sourceCopy.kinds.voice, icon: Microphone, count: factsForSurface(facts, 'voice').length + factsForSurface(facts, 'rules').length },
    { label: sourceCopy.kinds.history, icon: Database, count: factsForSurface(facts, 'pairs').length + replies.filter((reply) => reply.learning_runtime?.state === 'learned').length },
  ]
  const capabilities = [
    sourceCopy.capabilities.sales,
    sourceCopy.capabilities.support,
    sourceCopy.capabilities.programs,
    sourceCopy.capabilities.company,
    sourceCopy.capabilities.voice,
  ]

  return (
    <div className="space-y-5">
      <div className="grid gap-3 md:grid-cols-4">
        <OverviewMetric label="Manba" value={stats.sources} helper="o‘rganilgan yoki navbatda" loading={loading} />
        <OverviewMetric label="Tayyor dalil" value={stats.readyGrounding} helper="agentlar ishlata oladi" loading={loading} />
        <OverviewMetric label="Ruxsat navbati" value={stats.reviewQueue} helper="tasdiq yoki tuzatish kerak" loading={loading} />
        <OverviewMetric label="Juftlik" value={stats.learningPairs} helper="sotuvchi javobi namunasi" loading={loading} />
      </div>

      <section className="border-y border-border/70 bg-background py-4">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h3 className="text-base font-semibold">{sourceCopy.groundingTitle}</h3>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">{sourceCopy.groundingDesc}</p>
          </div>
          <Badge variant={readiness.every((item) => item.ready) ? 'success' : 'warning'}>
            {readiness.filter((item) => item.ready).length} / {readiness.length}
          </Badge>
        </div>
        <div className="mt-4 grid gap-2 px-0 md:grid-cols-5">
          {readiness.map((item) => (
            <div key={item.label} className="rounded-lg border border-border/60 bg-foreground/[0.015] p-3 transition-colors hover:bg-foreground/[0.035]">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium">{item.label}</span>
                {item.ready ? (
                  <CheckCircle className="size-4 text-emerald-600" weight="thin" />
                ) : (
                  <WarningCircle className="size-4 text-amber-600" weight="thin" />
                )}
              </div>
              <div className="mt-2 text-xl font-semibold tabular-nums">{item.value}</div>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">{item.helper}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="space-y-4 border-y border-border/70 bg-background py-4">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h3 className="text-base font-semibold">{sourceCopy.capabilityTitle}</h3>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">{sourceCopy.capabilityDesc}</p>
          </div>
          <Badge variant="outline">{sourceCopy.noCatalogConfusion}</Badge>
        </div>
        <div className="grid gap-2 md:grid-cols-5">
          {capabilities.map((item) => (
            <div key={item.title} className="rounded-lg border border-border/60 bg-foreground/[0.015] p-3">
              <div className="text-sm font-medium">{item.title}</div>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">{item.description}</p>
            </div>
          ))}
        </div>
        <div className="grid gap-2 md:grid-cols-5">
          {intake.map((item) => (
            <IntakeLane key={item.label} icon={item.icon} label={item.label} count={item.count} loading={loading} />
          ))}
        </div>
      </section>
    </div>
  )
}

export function BrainSourcesWorkbench({
  facts,
  loading,
  onCreateSource,
  onRetrySource,
  creating,
  retrying,
  sourceLearning,
}: {
  facts: BusinessBrainFactReadModel[]
  loading: boolean
  onCreateSource: (payload: BusinessBrainSourceCreateInput) => Promise<void>
  onRetrySource: (sourceRef?: string) => Promise<void>
  creating: boolean
  retrying: boolean
  sourceLearning?: OnboardingSourceLearningProjection
}) {
  const sources = sourceSummaries(facts, sourceLearning?.sources)
  const sourceCopy = uz.workspaceUi.brain.sources
  const retryableCount = sources.filter((source) => source.retryable).length
  if (loading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-24 rounded-xl" />
        <Skeleton className="h-24 rounded-xl" />
        <Skeleton className="h-24 rounded-xl" />
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <BrainSourceIntake onCreateSource={onCreateSource} creating={creating} />

      <SourceLearningProgress sourceLearning={sourceLearning} />

      <section className="border-y border-border/70 bg-background py-4">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h3 className="text-base font-semibold">{sourceCopy.listTitle}</h3>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">{sourceCopy.listDesc}</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {retryableCount ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={retrying}
                onClick={() => void onRetrySource()}
              >
                Hammasini qayta o‘rganish
              </Button>
            ) : null}
            <Badge variant="outline">{sources.length}</Badge>
          </div>
        </div>
      </section>

      {sources.length ? (
        <div className="overflow-hidden rounded-lg border border-border/70 bg-background">
          <div className="divide-y divide-border/60">
            {sources.map((source) => (
              <div key={source.fact.fact_id} className="grid gap-3 px-4 py-4 2xl:grid-cols-[minmax(0,1fr)_190px_150px] 2xl:items-start">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="truncate text-sm font-semibold">{source.title}</div>
                    <Badge variant="outline" className="w-fit">{source.kind}</Badge>
                    <Badge variant={source.status === 'learned' ? 'success' : source.retryable || source.status === 'failed' || source.status === 'retrying' ? 'warning' : 'outline'} className="w-fit">
                      {sourceStatusLabel(source.status)}
                    </Badge>
                  </div>
                  <p className="mt-2 line-clamp-2 text-sm leading-6 text-muted-foreground">
                    {source.preview || sourceEmptyCopy(source.status)}
                  </p>
                  <div className="mt-3 flex flex-wrap items-center gap-1.5">
                    {source.outputs.length ? (
                      source.outputs.map((output) => (
                        <Badge key={`${source.fact.fact_id}-${output.label}`} variant="outline">
                          {output.label}: {output.count}
                        </Badge>
                      ))
                    ) : (
                      <Badge variant="outline">{sourceOutputEmptyLabel(source.status)}</Badge>
                    )}
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {source.retryable ? (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={retrying}
                      onClick={() => void onRetrySource(source.sourceRef)}
                    >
                      Qayta
                    </Button>
                  ) : null}
                </div>
                <div className="text-xs leading-5 text-muted-foreground xl:text-right">
                  <div>{source.sourceUnits} matn · {source.media} media</div>
                  {source.degradedReasons.length ? (
                    <div className="truncate text-amber-700">{sourceIssueCopy(source.degradedReasons)}</div>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <EmptyBrainState
          icon={Rows}
          title="Hali manba yo‘q"
          description="Sayt, Telegram kanal, PDF, CSV/XLSX, ovoz yoki suhbat tarixi qo‘shilganda shu yerda ko‘rinadi."
        />
      )}
    </div>
  )
}

function sourceStatusLabel(status: string) {
  return (uz.workspaceUi.brain.sources.statuses as Record<string, string>)[status] ?? status
}

function sourceIssueCopy(reasons: string[]) {
  const visible = reasons
    .map(sourceIssueLabel)
    .filter(Boolean)
  return Array.from(new Set(visible)).join(', ')
}

function sourceIssueLabel(reason: string) {
  const normalized = reason.trim().toLowerCase()
  const labels: Record<string, string> = {
    fetch_failed: 'Manbani ochib bo‘lmadi',
    no_source_evidence: 'O‘qiladigan matn topilmadi',
    provider_timeout: 'O‘qish vaqti tugadi',
    rate_limited: 'Provayder vaqtincha sekinlashtirdi',
    unsupported_content: 'Format qo‘llanmaydi',
    empty_text: 'Matn topilmadi',
    degraded: 'Tekshirish kerak',
  }
  return labels[normalized] ?? 'Tekshirish kerak'
}

function sourceEmptyCopy(status: string) {
  if (status === 'queued' || status === 'learning' || status === 'retrying') {
    return 'OQIM bu manbani o‘qiyapti. Natija tayyor bo‘lsa Katalog, Bilim yoki Qoidalarda chiqadi.'
  }
  if (status === 'failed' || status === 'missing') {
    return 'Bu manbadan o‘qiladigan matn topilmadi. Qayta urinib ko‘ring yoki boshqa fayl/manba qo‘shing.'
  }
  return 'Bu manbadan hali ko‘rinadigan ma’lumot ajratilmadi.'
}

function sourceOutputEmptyLabel(status: string) {
  if (status === 'queued' || status === 'learning' || status === 'retrying') return 'Natija tayyorlanmoqda'
  if (status === 'failed' || status === 'missing') return 'O‘qib bo‘lmadi'
  return 'Ajratilgan fakt yo‘q'
}

type BrainSourceMode = 'website' | 'telegram_channel' | 'file' | 'text' | 'voice_note'

function BrainSourceIntake({
  onCreateSource,
  creating,
}: {
  onCreateSource: (payload: BusinessBrainSourceCreateInput) => Promise<void>
  creating: boolean
}) {
  const [mode, setMode] = useState<BrainSourceMode>('website')
  const [url, setUrl] = useState('')
  const [handle, setHandle] = useState('')
  const [text, setText] = useState('')
  const [file, setFile] = useState<OnboardingFileSource | null>(null)
  const [recording, setRecording] = useState(false)
  const [recordingError, setRecordingError] = useState('')
  const recorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const chunksRef = useRef<BlobPart[]>([])
  const sourceCopy = uz.workspaceUi.brain.sources

  useEffect(() => {
    return () => cleanupRecording(false)
  }, [])

  async function submit() {
    if (mode === 'website') {
      await onCreateSource({ kind: 'website', label: 'Sayt', url: url.trim() })
      setUrl('')
      return
    }
    if (mode === 'telegram_channel') {
      await onCreateSource({ kind: 'telegram_channel', label: 'Telegram kanal', handle: handle.trim() })
      setHandle('')
      return
    }
    if (mode === 'file' && file) {
      await onCreateSource({
        kind: 'file',
        label: file.fileName,
        file_name: file.fileName,
        content_type: file.contentType,
        content_base64: file.contentBase64,
        byte_size: file.byteSize,
      })
      setFile(null)
      return
    }
    if (mode === 'voice_note') {
      await onCreateSource({
        kind: 'voice_note',
        label: file?.fileName || 'Ovoz va qoida',
        transcript: text.trim() || undefined,
        ...(file
          ? {
              file_name: file.fileName,
              content_type: file.contentType,
              content_base64: file.contentBase64,
              byte_size: file.byteSize,
            }
          : {}),
      })
      setText('')
      setFile(null)
      return
    }
    await onCreateSource({ kind: 'text', label: 'Qo‘lda yozilgan manba', text: text.trim() })
    setText('')
  }

  async function startRecording() {
    setRecordingError('')
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setRecordingError(sourceCopy.recordingUnavailable)
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const recorder = new MediaRecorder(stream)
      chunksRef.current = []
      streamRef.current = stream
      recorderRef.current = recorder
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data)
      }
      recorder.onstop = () => {
        const mimeType = recorder.mimeType || 'audio/webm'
        const blob = new Blob(chunksRef.current, { type: mimeType })
        const recordedFile = new File([blob], `oqim-voice-${Date.now()}.webm`, { type: mimeType })
        void readOnboardingFileSource(recordedFile).then(setFile).catch(() => setRecordingError(sourceCopy.recordingFailed))
        cleanupRecording()
      }
      recorder.start()
      setRecording(true)
    } catch {
      setRecordingError(sourceCopy.recordingFailed)
      streamRef.current?.getTracks().forEach((track) => track.stop())
      streamRef.current = null
      recorderRef.current = null
      setRecording(false)
    }
  }

  function stopRecording() {
    if (recorderRef.current && recorderRef.current.state !== 'inactive') {
      recorderRef.current.stop()
    }
    setRecording(false)
  }

  function cleanupRecording(updateState = true) {
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    recorderRef.current = null
    chunksRef.current = []
    if (updateState) setRecording(false)
  }

  const canSubmit = (
    (mode === 'website' && url.trim())
    || (mode === 'telegram_channel' && handle.trim())
    || (mode === 'file' && file)
    || (mode === 'voice_note' && (text.trim() || file))
    || (mode === 'text' && text.trim())
  )

  return (
    <section className="rounded-xl border border-border/70 bg-background p-4">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h3 className="text-base font-semibold">{sourceCopy.addTitle}</h3>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">{sourceCopy.addDesc}</p>
        </div>
        <Badge variant="outline">{sourceCopy.newBadge}</Badge>
      </div>

      <div className="mt-4 grid gap-2 sm:grid-cols-5">
        {[
          { id: 'website', label: sourceCopy.modes.website, icon: GlobeHemisphereWest },
          { id: 'telegram_channel', label: sourceCopy.modes.telegram, icon: TelegramLogo },
          { id: 'file', label: sourceCopy.modes.file, icon: FileArrowUp },
          { id: 'text', label: sourceCopy.modes.text, icon: BookOpen },
          { id: 'voice_note', label: sourceCopy.modes.voice, icon: Microphone },
        ].map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => {
              cleanupRecording()
              setRecordingError('')
              setMode(item.id as BrainSourceMode)
              setFile(null)
            }}
            className={`flex items-center justify-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors ${
              mode === item.id
                ? 'border-foreground bg-foreground text-background'
                : 'border-border/70 text-muted-foreground hover:bg-foreground/[0.04] hover:text-foreground'
            }`}
          >
            <item.icon className="size-4" weight="thin" />
            {item.label}
          </button>
        ))}
      </div>

      <div className="mt-4">
        {mode === 'website' && (
          <Input value={url} onChange={(event: ChangeEvent<HTMLInputElement>) => setUrl(event.target.value)} placeholder={sourceCopy.placeholders.website} />
        )}
        {mode === 'telegram_channel' && (
          <Input value={handle} onChange={(event: ChangeEvent<HTMLInputElement>) => setHandle(event.target.value)} placeholder={sourceCopy.placeholders.telegram} />
        )}
        {mode === 'file' && (
          <FileInput
            file={file}
            accept=".pdf,.txt,.csv,.xlsx,.xlsm,.md,application/pdf,text/plain,text/csv,text/markdown,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel"
            onFile={setFile}
          />
        )}
        {mode === 'voice_note' && (
          <div className="grid gap-3 md:grid-cols-[1fr_1fr]">
            <div className="grid gap-2">
              <FileInput file={file} accept="audio/*" onFile={setFile} />
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  type="button"
                  variant={recording ? 'destructive' : 'outline'}
                  size="sm"
                  onClick={() => recording ? stopRecording() : void startRecording()}
                >
                  <Microphone className="size-4" weight="thin" />
                  {recording ? sourceCopy.stopRecording : sourceCopy.startRecording}
                </Button>
                {file ? <Badge variant="success">{sourceCopy.audioReady}</Badge> : null}
              </div>
              {recordingError ? (
                <p className="text-xs leading-5 text-destructive">{recordingError}</p>
              ) : null}
            </div>
            <Textarea
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder={sourceCopy.placeholders.voice}
              className="min-h-24 resize-none"
            />
          </div>
        )}
        {mode === 'text' && (
          <Textarea
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder={sourceCopy.placeholders.text}
            className="min-h-28 resize-none"
          />
        )}
      </div>

      <div className="mt-4 flex items-center justify-end">
        <Button type="button" onClick={() => void submit()} disabled={!canSubmit || creating}>
          {creating ? sourceCopy.adding : sourceCopy.add}
        </Button>
      </div>
    </section>
  )
}

function FileInput({
  file,
  accept,
  onFile,
}: {
  file: OnboardingFileSource | null
  accept: string
  onFile: (file: OnboardingFileSource | null) => void
}) {
  const sourceCopy = uz.workspaceUi.brain.sources
  return (
    <label className="flex min-h-24 cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-border/80 bg-foreground/[0.015] px-4 py-5 text-center hover:border-foreground/40">
      <input
        type="file"
        accept={accept}
        className="sr-only"
        onChange={(event) => {
          const selected = event.target.files?.[0]
          if (!selected) {
            onFile(null)
            return
          }
          void readOnboardingFileSource(selected).then(onFile).catch(() => onFile(null))
        }}
      />
      <FileArrowUp className="size-5 text-muted-foreground" weight="thin" />
      <span className="mt-2 text-sm font-medium">{file ? file.fileName : sourceCopy.chooseFile}</span>
      <span className="mt-1 text-xs text-muted-foreground">{file ? `${Math.ceil(file.byteSize / 1024)} KB` : sourceCopy.fileHint}</span>
    </label>
  )
}

function SourceLearningProgress({
  sourceLearning,
}: {
  sourceLearning?: OnboardingSourceLearningProjection
}) {
  const sourceCopy = uz.workspaceUi.brain.sources
  if (!sourceLearning || sourceLearning.summary.total === 0) return null
  const summary = sourceLearning.summary
  return (
    <section className="border-y border-border/70 bg-background py-4">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h3 className="text-base font-semibold">{sourceCopy.progressTitle}</h3>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">
            {sourceCopy.progressDesc(summary.learned, summary.needs_review, summary.failed + summary.retrying)}
          </p>
        </div>
        <Badge variant={summary.failed ? 'warning' : sourceLearning.status === 'learned' ? 'success' : 'outline'}>
          {sourceStatusLabel(sourceLearning.status)}
        </Badge>
      </div>
      <Progress value={sourceLearning.percent} className="mt-4">
        <ProgressLabel>{sourceCopy.progressLabel}</ProgressLabel>
        <ProgressValue>{sourceLearning.percent}%</ProgressValue>
      </Progress>
    </section>
  )
}

export function BrainFactSurface({
  facts,
  surface,
  title,
  description,
  loading,
  actionBusy = false,
  onReviewAction,
  onManualUpdate,
}: {
  facts: BusinessBrainFactReadModel[]
  surface: BrainSurface
  title: string
  description: string
  loading: boolean
  actionBusy?: boolean
  onReviewAction?: (input: BusinessBrainFactReviewActionInput) => void
  onManualUpdate?: (input: BusinessBrainManualFactUpdateInput) => void
}) {
  const items = factsForSurface(facts, surface).filter((fact) => fact.status !== 'superseded')
  const proposed = items.filter((fact) => fact.status === 'proposed').length
  const degraded = items.filter((fact) => fact.status === 'degraded' || fact.status === 'conflict').length
  const active = items.filter((fact) => fact.status === 'active' || fact.status === 'confirmed').length
  const missingData = items.filter((fact) => {
    const summary = factSummary(fact)
    return fact.status === 'degraded' || fact.status === 'conflict' || !summary || fact.source_refs.length === 0
  })
  if (loading) {
    return (
      <div className="grid gap-3 md:grid-cols-2">
        <Skeleton className="h-36 rounded-xl" />
        <Skeleton className="h-36 rounded-xl" />
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-border/70 bg-background p-4">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h3 className="text-base font-semibold">{title}</h3>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">{description}</p>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <Badge variant="outline">{active} tayyor</Badge>
            {proposed ? <Badge variant="warning">{proposed} tekshirish</Badge> : null}
            {degraded ? <Badge variant="warning">{degraded} konflikt</Badge> : null}
          </div>
        </div>
      </section>

      {missingData.length ? (
        <section className="rounded-xl border border-amber-500/30 bg-amber-500/[0.04] p-4">
          <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <h3 className="text-sm font-semibold">To‘ldirish kerak</h3>
              <p className="mt-1 text-sm leading-6 text-muted-foreground">
                Bu faktlarda dalil, matn yoki konflikt bor. Tahrirlang, rad eting yoki dublikat bilan birlashtiring.
              </p>
            </div>
            <Badge variant="warning">{missingData.length}</Badge>
          </div>
          <div className="mt-3 grid gap-2">
            {missingData.slice(0, 4).map((fact) => (
              <div key={`missing-${fact.fact_id}`} className="grid gap-2 rounded-md border border-border/70 bg-background px-3 py-2 md:grid-cols-[minmax(0,1fr)_160px] md:items-center">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{factTitle(fact)}</p>
                  <div className="mt-1 flex flex-wrap gap-1.5 text-xs text-muted-foreground">
                    <span>Bo‘lim: {factOwnerLabel(fact)}</span>
                    <span>Manba: {factPrimarySourceLabel(fact)}</span>
                  </div>
                </div>
                <Badge variant="outline" className="w-fit md:justify-self-end">
                  Kerak: {factRepairActionLabel(fact, items.filter((item) => item.fact_id !== fact.fact_id && item.fact_type === fact.fact_type))}
                </Badge>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {items.length ? (
        <div className="grid gap-3 md:grid-cols-2">
          {items.map((fact) => (
            <BrainManagedFactCard
              key={fact.fact_id}
              fact={fact}
              mergeOptions={items.filter((item) => item.fact_id !== fact.fact_id && item.fact_type === fact.fact_type)}
              actionBusy={actionBusy}
              onReviewAction={onReviewAction}
              onManualUpdate={onManualUpdate}
            />
          ))}
        </div>
      ) : (
        <EmptyBrainState
          icon={BookOpen}
          title={`${title} hali bo‘sh`}
          description="Onboarding, manba qo‘shish, suhbat tarixi va sotuvchi tuzatishlari bu joyni to‘ldiradi."
        />
      )}
    </div>
  )
}

function BrainManagedFactCard({
  fact,
  mergeOptions,
  actionBusy,
  onReviewAction,
  onManualUpdate,
}: {
  fact: BusinessBrainFactReadModel
  mergeOptions: BusinessBrainFactReadModel[]
  actionBusy: boolean
  onReviewAction?: (input: BusinessBrainFactReviewActionInput) => void
  onManualUpdate?: (input: BusinessBrainManualFactUpdateInput) => void
}) {
  const copy = uz.workspaceUi.brain.review
  const [isEditing, setIsEditing] = useState(false)
  const originalText = factSummary(fact)
  const editableText = editableFactText(fact)
  const [value, setValue] = useState(editableText)
  const [mergeIntoRef, setMergeIntoRef] = useState('')
  const canSave = value.trim().length >= 2 && value.trim() !== editableText.trim()
  const isProposed = fact.status === 'proposed'
  const canManualEdit = Boolean(onManualUpdate) && ['active', 'confirmed', 'degraded', 'conflict'].includes(fact.status)
  const canMerge = isProposed && Boolean(onReviewAction) && mergeOptions.length > 0

  function saveManualUpdate() {
    if (!onManualUpdate || !canSave) return
    const now = Date.now()
    onManualUpdate({
      fact_id: `${fact.fact_id}:owner:${now}`,
      update_id: `owner-update:${fact.fact_id}:${now}`,
      fact_type: fact.fact_type,
      entity_ref: fact.entity_ref,
      value: {
        ...fact.value,
        ...factReviewPatch(fact, value.trim()),
      },
      confidence: Math.max(fact.confidence, 0.92),
      risk_tier: fact.risk_tier || 'low',
      source_refs: fact.source_refs.length ? fact.source_refs : [`owner:brain:${fact.fact_id}`],
      idempotency_key: `owner-update:${fact.fact_id}:${now}`,
      correlation_id: `brain-ui:${fact.fact_id}:${now}`,
      supersedes_fact_id: fact.fact_id,
    })
    setIsEditing(false)
  }

  return (
    <article className="rounded-xl border border-border/70 bg-background p-4">
      {fact.fact_type === 'conversation_pair_fact' ? (
        <ConversationPairBody fact={fact} fallbackText={originalText} />
      ) : (
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">{factTitle(fact)}</div>
          <p className="mt-2 line-clamp-4 text-sm leading-6 text-muted-foreground">{originalText || copy.needsText}</p>
        </div>
        <Badge variant={fact.status === 'active' || fact.status === 'confirmed' ? 'success' : fact.status === 'degraded' || fact.status === 'conflict' || fact.status === 'proposed' ? 'warning' : 'outline'}>
          {factStatusLabel(fact.status)}
        </Badge>
      </div>
      )}

      <div className="mt-4 flex flex-wrap gap-1.5">
        <Badge variant="outline">Bo‘lim: {factOwnerLabel(fact)}</Badge>
        <Badge variant="outline">{Math.round(fact.confidence * 100)}%</Badge>
        {visibleSourceLabels(fact).map((label) => (
          <Badge key={`${fact.fact_id}-${label}`} variant="outline">{label}</Badge>
        ))}
      </div>

      {(isProposed || canManualEdit) && (
        <div className="mt-4 border-t border-border/60 pt-3">
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-8 px-2.5 text-xs"
              disabled={actionBusy}
              onClick={() => {
                setValue(editableText)
                setIsEditing((state) => !state)
              }}
            >
              {copy.edit}
            </Button>
            {isProposed && onReviewAction ? (
              <>
                <Button
                  type="button"
                  size="sm"
                  className="h-8 px-2.5 text-xs"
                  disabled={actionBusy}
                  onClick={() => onReviewAction({ action: 'approve', target_ref: fact.fact_id })}
                >
                  {copy.approve}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-8 px-2.5 text-xs"
                  disabled={actionBusy}
                  onClick={() => onReviewAction({ action: 'reject', target_ref: fact.fact_id })}
                >
                  {copy.reject}
                </Button>
              </>
            ) : null}
          </div>

          {isEditing && (
            <div className="mt-3 grid gap-2">
              <Textarea
                value={value}
                onChange={(event: ChangeEvent<HTMLTextAreaElement>) => setValue(event.target.value)}
                className="min-h-24 resize-none text-sm"
                aria-label={copy.correctText}
              />
              <Button
                type="button"
                size="sm"
                className="h-8 justify-self-start px-2.5 text-xs"
                disabled={actionBusy || !canSave}
                onClick={() => {
                  if (isProposed && onReviewAction) {
                    onReviewAction({
                      action: 'edit',
                      target_ref: fact.fact_id,
                      value_patch: factReviewPatch(fact, value.trim()),
                    })
                    setIsEditing(false)
                    return
                  }
                  saveManualUpdate()
                }}
              >
                {isProposed ? copy.saveEdit : copy.saveActive}
              </Button>
            </div>
          )}

          {canMerge ? (
            <div className="mt-3 grid gap-2">
              <label className="block text-xs font-medium">
                {copy.mergeLabel}
                <select
                  value={mergeIntoRef}
                  onChange={(event) => setMergeIntoRef(event.target.value)}
                  className="mt-1 h-9 w-full rounded-md border border-border/70 bg-background px-3 text-sm font-normal outline-none focus:border-foreground/40"
                >
                  <option value="">{copy.mergePlaceholder}</option>
                  {mergeOptions.map((item) => (
                    <option key={item.fact_id} value={item.fact_id}>
                      {factTitle(item)}
                    </option>
                  ))}
                </select>
              </label>
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-8 justify-self-start px-2.5 text-xs"
                disabled={actionBusy || !mergeIntoRef}
                onClick={() => onReviewAction?.({
                  action: 'merge',
                  target_ref: fact.fact_id,
                  merge_into_ref: mergeIntoRef,
                })}
              >
                {copy.merge}
              </Button>
            </div>
          ) : null}
        </div>
      )}
    </article>
  )
}

function ConversationPairBody({
  fact,
  fallbackText,
}: {
  fact: BusinessBrainFactReadModel
  fallbackText: string
}) {
  const parts = conversationPairParts(fact)
  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold">Suhbatdan o‘rganilgan javob</div>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            Bu namuna agentga mijoz savoliga sotuvchi qanday ohangda javob berishini o‘rgatadi.
          </p>
        </div>
        <Badge variant={fact.status === 'active' || fact.status === 'confirmed' ? 'success' : fact.status === 'degraded' || fact.status === 'conflict' || fact.status === 'proposed' ? 'warning' : 'outline'}>
          {factStatusLabel(fact.status)}
        </Badge>
      </div>
      <div className="grid gap-2">
        <div className="rounded-lg border border-border/60 bg-foreground/[0.015] p-3">
          <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-muted-foreground">Mijoz yozgan</div>
          <p className="mt-1 whitespace-pre-wrap text-sm leading-6">{parts.customer || 'Mijoz matni topilmadi'}</p>
        </div>
        <div className="rounded-lg border border-border/60 bg-foreground/[0.015] p-3">
          <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-muted-foreground">Sotuvchi javobi</div>
          <p className="mt-1 whitespace-pre-wrap text-sm leading-6">{parts.seller || fallbackText || 'Sotuvchi javobi topilmadi'}</p>
        </div>
      </div>
      {parts.contextBefore.length ? (
        <p className="text-xs leading-5 text-muted-foreground">
          Oldingi xabarlar dalil sifatida saqlandi, lekin agent javob yozishda mijoz va sotuvchi juftligiga tayanadi.
        </p>
      ) : null}
    </div>
  )
}

export function BrainReviewQueue({
  facts,
  loading,
  actionBusy,
  onReviewAction,
}: {
  facts: BusinessBrainFactReadModel[]
  loading: boolean
  actionBusy: boolean
  onReviewAction: (input: BusinessBrainFactReviewActionInput) => void
}) {
  const copy = uz.workspaceUi.brain.review
  const items = facts.filter((fact) => {
    if (fact.status !== 'proposed') return false
    const surface = surfaceForFact(fact)
    return surface !== 'sources' && surface !== 'catalog'
  })

  if (loading) {
    return (
      <section className="rounded-lg border border-border/70 bg-background p-4">
        <Skeleton className="h-5 w-32" />
        <Skeleton className="mt-3 h-20 rounded-md" />
      </section>
    )
  }

  return (
    <section className="rounded-lg border border-border/70 bg-background p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">{copy.title}</h3>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">{copy.description}</p>
        </div>
        <Badge variant={items.length ? 'warning' : 'outline'}>{items.length}</Badge>
      </div>

      {items.length ? (
        <div className="mt-3 grid gap-2">
          {items.slice(0, 5).map((fact) => (
            <BrainReviewFactCard
              key={fact.fact_id}
              fact={fact}
              actionBusy={actionBusy}
              onReviewAction={onReviewAction}
            />
          ))}
          {items.length > 5 && (
            <p className="text-xs text-muted-foreground">{copy.more(items.length - 5)}</p>
          )}
        </div>
      ) : (
        <p className="mt-3 rounded-md border border-dashed border-border/70 px-3 py-2 text-xs leading-5 text-muted-foreground">
          {copy.empty}
        </p>
      )}
    </section>
  )
}

function BrainReviewFactCard({
  fact,
  actionBusy,
  onReviewAction,
}: {
  fact: BusinessBrainFactReadModel
  actionBusy: boolean
  onReviewAction: (input: BusinessBrainFactReviewActionInput) => void
}) {
  const copy = uz.workspaceUi.brain.review
  const [isEditing, setIsEditing] = useState(false)
  const originalText = factSummary(fact)
  const editableText = editableFactText(fact)
  const [value, setValue] = useState(editableText)
  const canSave = value.trim().length >= 2 && value.trim() !== editableText.trim()
  const surface = surfaceForFact(fact)

  return (
    <article className="rounded-md border border-border/70 px-3 py-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge variant="outline">{reviewSurfaceLabel(surface)}</Badge>
            <span className="text-[11px] text-muted-foreground">
              {Math.round(fact.confidence * 100)}% · {fact.source_refs.length} dalil
            </span>
          </div>
          <p className="mt-2 truncate text-sm font-medium">{factTitle(fact)}</p>
          <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">
            {originalText || copy.needsText}
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-7 shrink-0 px-2 text-xs"
          disabled={actionBusy}
          onClick={() => {
            setValue(editableText)
            setIsEditing((state) => !state)
          }}
        >
          {copy.edit}
        </Button>
      </div>

      {isEditing && (
        <div className="mt-3 grid gap-2">
          <Textarea
            value={value}
            onChange={(event: ChangeEvent<HTMLTextAreaElement>) => setValue(event.target.value)}
            className="min-h-20 resize-none text-sm"
            aria-label={copy.correctText}
          />
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-7 justify-self-start px-2 text-xs"
            disabled={actionBusy || !canSave}
            onClick={() => onReviewAction({
              action: 'edit',
              target_ref: fact.fact_id,
              value_patch: factReviewPatch(fact, value.trim()),
            })}
          >
            {copy.saveEdit}
          </Button>
        </div>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="sm"
          className="h-7 px-2 text-xs"
          disabled={actionBusy}
          onClick={() => onReviewAction({ action: 'approve', target_ref: fact.fact_id })}
        >
          {copy.approve}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-7 px-2 text-xs"
          disabled={actionBusy}
          onClick={() => onReviewAction({ action: 'reject', target_ref: fact.fact_id })}
        >
          {copy.reject}
        </Button>
      </div>
    </article>
  )
}

function reviewSurfaceLabel(surface: BrainSurface) {
  const labels = uz.workspaceUi.brain.review.labels as Record<BrainSurface, string>
  return labels[surface] ?? uz.workspaceUi.brain.review.fact
}

function factStatusLabel(status: string) {
  const labels = uz.workspaceUi.brain.review.statuses as Record<string, string>
  return labels[status] ?? status
}

function factReviewPatch(fact: BusinessBrainFactReadModel, value: string): Record<string, unknown> {
  if (fact.fact_type === 'conversation_pair_fact') return { seller_turn: value }
  if (fact.fact_type === 'correction_episode_fact') return { final_output: value }
  if (typeof fact.value.rule === 'string') return { rule: value }
  if (typeof fact.value.answer === 'string') return { answer: value }
  if (typeof fact.value.requirement === 'string') return { requirement: value }
  if (typeof fact.value.content === 'string') return { content: value }
  if (typeof fact.value.description === 'string') return { description: value }
  return { summary: value }
}

function OverviewMetric({
  label,
  value,
  helper,
  loading,
}: {
  label: string
  value: number
  helper: string
  loading: boolean
}) {
  return (
    <div className="rounded-xl border border-border/70 bg-background p-4">
      <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-muted-foreground">{label}</div>
      {loading ? <Skeleton className="mt-3 h-8 w-20" /> : <div className="mt-2 text-3xl font-semibold tabular-nums">{value}</div>}
      <p className="mt-1 text-xs leading-5 text-muted-foreground">{helper}</p>
    </div>
  )
}

function IntakeLane({
  icon: Icon,
  label,
  count,
  loading,
}: {
  icon: Icon
  label: string
  count: number
  loading: boolean
}) {
  return (
    <div className="rounded-lg border border-border/60 bg-foreground/[0.015] p-3">
      <div className="flex items-center justify-between gap-2">
        <Icon className="size-4 text-muted-foreground" weight="thin" />
        {loading ? <Skeleton className="h-5 w-8" /> : <span className="text-sm font-semibold tabular-nums">{count}</span>}
      </div>
      <div className="mt-3 text-sm font-medium">{label}</div>
    </div>
  )
}

function EmptyBrainState({
  icon: Icon,
  title,
  description,
}: {
  icon: Icon
  title: string
  description: string
}) {
  return (
    <div className="rounded-xl border border-dashed border-border/80 bg-foreground/[0.015] p-8 text-center">
      <div className="mx-auto flex size-10 items-center justify-center rounded-lg border border-border/70 bg-background">
        <Icon className="size-5" weight="thin" />
      </div>
      <h3 className="mt-4 text-base font-semibold">{title}</h3>
      <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-muted-foreground">{description}</p>
    </div>
  )
}
