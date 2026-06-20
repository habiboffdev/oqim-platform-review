import {
  Check,
  CloudArrowUp,
  File as FileIcon,
  FileText,
  GlobeHemisphereWest,
  Microphone,
  TelegramLogo,
  X,
} from '@phosphor-icons/react'
import { useEffect, useState, type ChangeEvent } from 'react'
import type React from 'react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { api } from '@/lib/api-client'
import {
  formatBytes,
  readOnboardingFileSource,
  type OnboardingFileSource,
} from '@/lib/file-source'
import { uz } from '@/lib/uz'

interface TelegramChannelOption {
  id: number | string
  name: string
  username?: string
  member_count?: number | null
  is_own?: boolean
  is_broadcast?: boolean
}

interface SourceLearningPanelProps {
  sourceNotes: string
  ownerRules: string
  websiteSource: string
  telegramChannelSource: string
  telegramStartDate?: string
  telegramEndDate?: string
  fileSource: OnboardingFileSource | null
  voiceSource: string
  voiceFileSource: OnboardingFileSource | null
  onSourceNotesChange: (value: string) => void
  onOwnerRulesChange: (value: string) => void
  onWebsiteSourceChange: (value: string) => void
  onTelegramChannelSourceChange: (value: string) => void
  onTelegramStartDateChange?: (value: string) => void
  onTelegramEndDateChange?: (value: string) => void
  onFileSourceChange: (value: OnboardingFileSource | null) => void
  onVoiceSourceChange: (value: string) => void
  onVoiceFileSourceChange: (value: OnboardingFileSource | null) => void
}

type SourceBucket = 'brain' | 'agent'
type SourceMode = 'telegram_channel' | 'website' | 'file' | 'manual' | 'voice'

function splitSources(value: string) {
  return value
    .split(/[\n,]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

export function SourceLearningPanel({
  sourceNotes,
  ownerRules,
  websiteSource,
  telegramChannelSource,
  telegramStartDate = '',
  telegramEndDate = '',
  fileSource,
  voiceSource,
  voiceFileSource,
  onSourceNotesChange,
  onOwnerRulesChange,
  onWebsiteSourceChange,
  onTelegramChannelSourceChange,
  onTelegramStartDateChange,
  onTelegramEndDateChange,
  onFileSourceChange,
  onVoiceSourceChange,
  onVoiceFileSourceChange,
}: SourceLearningPanelProps) {
  const websiteCount = splitSources(websiteSource).length
  const channelCount = splitSources(telegramChannelSource).length
  const addedSources: Array<{ label: string; value: string }> = []
  if (channelCount) {
    addedSources.push({ label: uz.onboarding.telegramChannelSource, value: uz.onboarding.sourcesCount(channelCount) })
  }
  if (websiteCount) {
    addedSources.push({ label: uz.onboarding.websiteSource, value: uz.onboarding.sourcesCount(websiteCount) })
  }
  if (fileSource) {
    addedSources.push({ label: uz.onboarding.fileSource, value: fileSource.fileName })
  }
  if (voiceSource.trim() || voiceFileSource || ownerRules.trim() || sourceNotes.trim()) {
    addedSources.push({
      label: uz.onboarding.sellerMemorySource,
      value: voiceFileSource?.fileName || uz.onboarding.sellerMemoryReady,
    })
  }
  const readyCount = addedSources.length
  const [bucket, setBucket] = useState<SourceBucket>('brain')
  const [sourceMode, setSourceMode] = useState<SourceMode>('telegram_channel')
  const sourceOptions: Array<{
    id: SourceMode
    label: string
    description: string
    glyph: React.ReactNode
    ready: boolean
  }> = [
    {
      id: 'telegram_channel',
      label: uz.onboarding.telegramChannelSource,
      description: uz.onboarding.telegramChannelSourceHint,
      glyph: <TelegramLogo size={16} weight="thin" />,
      ready: Boolean(telegramChannelSource.trim()),
    },
    {
      id: 'website',
      label: uz.onboarding.websiteSource,
      description: uz.onboarding.websiteSourceHint,
      glyph: <GlobeHemisphereWest size={16} weight="thin" />,
      ready: Boolean(websiteSource.trim()),
    },
    {
      id: 'file',
      label: uz.onboarding.fileSource,
      description: uz.onboarding.fileSourceHint,
      glyph: <FileText size={16} weight="thin" />,
      ready: Boolean(fileSource),
    },
    {
      id: 'manual',
      label: bucket === 'brain' ? uz.onboarding.manualBrainSource : uz.onboarding.ownerRules,
      description: bucket === 'brain' ? uz.onboarding.manualBrainHint : uz.onboarding.ownerRulesHint,
      glyph: <FileText size={16} weight="thin" />,
      ready: bucket === 'brain' ? Boolean(sourceNotes.trim()) : Boolean(ownerRules.trim()),
    },
    {
      id: 'voice',
      label: uz.onboarding.voiceSource,
      description: uz.onboarding.voiceAudioSourceHint,
      glyph: <Microphone size={16} weight="thin" />,
      ready: Boolean(voiceSource.trim() || voiceFileSource),
    },
  ]

  const handleFileChange = async (file: File | undefined, setter: (value: OnboardingFileSource | null) => void) => {
    if (!file) {
      setter(null)
      return
    }
    try {
      setter(await readOnboardingFileSource(file))
    } catch {
      setter(null)
      toast.error(uz.onboarding.fileReadFailed)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <section className="rounded-2xl border border-border/70 bg-background p-4 sm:p-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="text-base font-semibold">{uz.onboarding.sources}</div>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
              {uz.onboarding.sourcesHint}
            </p>
          </div>
          <Badge variant="outline" className="w-fit">
            {readyCount ? `${readyCount} tayyor` : uz.onboarding.sourceManualBadge}
          </Badge>
        </div>

        <div className="mt-5 grid gap-2 sm:grid-cols-2">
          {([
            ['brain', uz.onboarding.brainDataTitle, uz.onboarding.brainDataHint],
            ['agent', uz.onboarding.agentDataTitle, uz.onboarding.agentDataHint],
          ] as const).map(([id, title, description]) => (
            <button
              key={id}
              type="button"
              aria-pressed={bucket === id}
              onClick={() => {
                setBucket(id)
                if (id === 'agent' && sourceMode !== 'manual' && sourceMode !== 'voice') {
                  setSourceMode('manual')
                }
                if (id === 'brain' && sourceMode === 'voice') {
                  setSourceMode('telegram_channel')
                }
              }}
              className={`rounded-xl border px-4 py-3 text-left transition-colors ${
                bucket === id
                  ? 'border-foreground bg-foreground text-background'
                  : 'border-border/70 bg-muted/25 hover:bg-muted/60'
              }`}
            >
              <span className="block text-sm font-semibold">{title}</span>
              <span className={`mt-1 block text-xs leading-5 ${bucket === id ? 'text-background/70' : 'text-muted-foreground'}`}>
                {description}
              </span>
            </button>
          ))}
        </div>

        <div className="mt-5 grid gap-4">
          <div
            className="grid gap-2 sm:grid-cols-2"
            aria-label={uz.onboarding.sourceTypePicker}
            data-testid="onboarding-source-type-picker"
          >
            {sourceOptions.map((option) => (
              <button
                key={option.id}
                type="button"
                aria-pressed={sourceMode === option.id}
                onClick={() => setSourceMode(option.id)}
                className={`flex min-h-16 items-start justify-between gap-3 rounded-xl border px-3 py-3 text-left text-sm transition-colors ${
                  sourceMode === option.id
                    ? 'border-foreground bg-foreground text-background'
                    : 'border-border/70 bg-background text-muted-foreground hover:bg-muted/60 hover:text-foreground'
                }`}
              >
                <span className="flex min-w-0 items-start gap-2">
                  <span className={`mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg ${sourceMode === option.id ? 'bg-background/10' : 'bg-muted'}`}>
                    {option.glyph}
                  </span>
                  <span className="min-w-0">
                    <span className="block font-medium leading-5">{option.label}</span>
                    <span className={`mt-0.5 line-clamp-2 block text-xs leading-5 ${sourceMode === option.id ? 'text-background/70' : 'text-muted-foreground'}`}>
                      {option.description}
                    </span>
                  </span>
                </span>
                {option.ready ? <Check size={16} weight="thin" className="mt-1 shrink-0" /> : null}
              </button>
            ))}
          </div>

          <div className="min-w-0" data-testid="onboarding-source-editor">
            {sourceMode === 'website' && (
            <SourcePanel
              title={uz.onboarding.websiteSource}
              detail={uz.onboarding.websiteManyHint}
              status={websiteSource.trim() ? uz.onboarding.sourceReady : uz.onboarding.sourceOptional}
            >
              <Label htmlFor="website-source" className="text-xs font-medium">
                {uz.onboarding.websiteSource}
              </Label>
              <Textarea
                id="website-source"
                value={websiteSource}
                onChange={(event: React.ChangeEvent<HTMLTextAreaElement>) => onWebsiteSourceChange(event.target.value)}
                placeholder={uz.onboarding.websiteSourcePlaceholder}
                className="min-h-28 resize-none"
              />
            </SourcePanel>
            )}

            {sourceMode === 'telegram_channel' && (
            <SourcePanel
              title={uz.onboarding.telegramChannelSource}
              detail={uz.onboarding.telegramChannelSourceHint}
              status={telegramChannelSource.trim() ? uz.onboarding.sourceReady : uz.onboarding.sourceOptional}
            >
              <TelegramChannelPicker
                value={telegramChannelSource}
                onChange={onTelegramChannelSourceChange}
                startDate={telegramStartDate}
                endDate={telegramEndDate}
                onStartDateChange={onTelegramStartDateChange}
                onEndDateChange={onTelegramEndDateChange}
              />
            </SourcePanel>
            )}

            {sourceMode === 'file' && (
            <SourcePanel
              title={uz.onboarding.fileSource}
              detail={uz.onboarding.fileSourceHint}
              status={fileSource ? uz.onboarding.sourceReady : uz.onboarding.sourceOptional}
            >
              <UploadDropzone
                inputId="file-source"
                title={uz.onboarding.fileSource}
                hint="PDF, rasm, CSV, XLSX, TXT"
                accept=".pdf,.png,.jpg,.jpeg,.webp,.heic,.txt,.csv,.xlsx,.xlsm,.md,application/pdf,image/*,text/plain,text/csv,text/markdown,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel"
                fileSource={fileSource}
                onFileChange={(file) => void handleFileChange(file, onFileSourceChange)}
                onClear={() => onFileSourceChange(null)}
              />
            </SourcePanel>
            )}

            {sourceMode === 'manual' && (
            <SourcePanel
              title={bucket === 'brain' ? uz.onboarding.manualBrainSource : uz.onboarding.ownerRules}
              detail={bucket === 'brain' ? uz.onboarding.manualBrainHint : uz.onboarding.ownerRulesHint}
              status={(bucket === 'brain' ? sourceNotes.trim() : ownerRules.trim()) ? uz.onboarding.sourceReady : uz.onboarding.sourceOptional}
            >
              {bucket === 'brain' ? (
                <>
              <Label htmlFor="source-notes" className="text-xs font-medium">
                {uz.onboarding.sources}
              </Label>
              <Textarea
                id="source-notes"
                value={sourceNotes}
                onChange={(event: React.ChangeEvent<HTMLTextAreaElement>) => onSourceNotesChange(event.target.value)}
                placeholder={uz.onboarding.sourcesPlaceholder}
                className="min-h-20 resize-none rounded-lg border-border/70 bg-background text-sm"
              />
                </>
              ) : (
                <>
              <Label htmlFor="owner-rules" className="text-xs font-medium">
                {uz.onboarding.ownerRules}
              </Label>
              <Textarea
                id="owner-rules"
                value={ownerRules}
                onChange={(event: React.ChangeEvent<HTMLTextAreaElement>) => onOwnerRulesChange(event.target.value)}
                placeholder={uz.onboarding.ownerRulesPlaceholder}
                className="min-h-20 resize-none rounded-lg border-border/70 bg-background text-sm"
              />
                </>
              )}
            </SourcePanel>
            )}

            {sourceMode === 'voice' && (
            <SourcePanel
              title={uz.onboarding.voiceSource}
              detail={uz.onboarding.voiceAudioSourceHint}
              status={voiceFileSource || voiceSource.trim() ? uz.onboarding.sourceReady : uz.onboarding.sourceOptional}
            >
              <Label htmlFor="voice-source" className="text-xs font-medium">
                {uz.onboarding.voiceSource}
              </Label>
              <Textarea
                id="voice-source"
                value={voiceSource}
                onChange={(event: React.ChangeEvent<HTMLTextAreaElement>) => onVoiceSourceChange(event.target.value)}
                placeholder={uz.onboarding.voiceSourcePlaceholder}
                className="min-h-20 resize-none rounded-lg border-border/70 bg-background text-sm"
              />
              <UploadDropzone
                inputId="voice-audio-source"
                title={uz.onboarding.voiceAudioSource}
                hint="Audio"
                accept="audio/*"
                fileSource={voiceFileSource}
                onFileChange={(file) => void handleFileChange(file, onVoiceFileSourceChange)}
                onClear={() => onVoiceFileSourceChange(null)}
              />
              <VoiceRecorder onRecorded={(file) => void handleFileChange(file, onVoiceFileSourceChange)} />
            </SourcePanel>
            )}
          </div>
        </div>
      </section>

      {addedSources.length ? (
        <div className="rounded-lg border border-border/70 bg-background px-3 py-2">
          <div className="text-xs font-medium text-muted-foreground">{uz.onboarding.addedSources}</div>
          <div className="mt-2 grid gap-1.5">
            {addedSources.map((source) => (
              <div key={`${source.label}:${source.value}`} className="flex items-center justify-between gap-3 text-sm">
                <span>{source.label}</span>
                <span className="truncate text-muted-foreground">{source.value}</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  )
}

function TelegramChannelPicker({
  value,
  onChange,
  startDate = '',
  endDate = '',
  onStartDateChange,
  onEndDateChange,
}: {
  value: string
  onChange: (value: string) => void
  startDate?: string
  endDate?: string
  onStartDateChange?: (value: string) => void
  onEndDateChange?: (value: string) => void
}) {
  const [channels, setChannels] = useState<TelegramChannelOption[]>([])
  const [loading, setLoading] = useState(false)
  const [loaded, setLoaded] = useState(false)

  async function loadChannels() {
    setLoading(true)
    try {
      const payload = await api.get<{ channels: TelegramChannelOption[]; count: number }>('/api/telegram/channels')
      setChannels(payload.channels ?? [])
      setLoaded(true)
    } catch {
      toast.error('Kanallar ro‘yxati kelmadi. Kanal username yoki linkini yozing.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="grid gap-3">
      <div className="grid gap-2">
        <Label htmlFor="telegram-channel-source" className="text-xs font-medium">
          {uz.onboarding.telegramChannelSource}
        </Label>
        <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
          <Textarea
            id="telegram-channel-source"
            value={value}
            onChange={(event: ChangeEvent<HTMLTextAreaElement>) => onChange(event.target.value)}
            placeholder={uz.onboarding.telegramChannelSourcePlaceholder}
            className="min-h-24 resize-none"
          />
          <Button type="button" variant="outline" className="h-11 sm:self-start" onClick={() => void loadChannels()} disabled={loading}>
            {loading ? 'Tekshirilmoqda' : 'Kanallar'}
          </Button>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          <label className="grid gap-1 text-xs font-medium">
            {uz.onboarding.telegramDateFrom}
            <Input
              type="date"
              value={startDate}
              onChange={(event: ChangeEvent<HTMLInputElement>) => onStartDateChange?.(event.target.value)}
            />
          </label>
          <label className="grid gap-1 text-xs font-medium">
            {uz.onboarding.telegramDateTo}
            <Input
              type="date"
              value={endDate}
              onChange={(event: ChangeEvent<HTMLInputElement>) => onEndDateChange?.(event.target.value)}
            />
          </label>
        </div>
        <p className="text-xs leading-5 text-muted-foreground">{uz.onboarding.telegramManyHint}</p>
      </div>

      {loaded && (
        <div className="max-h-44 overflow-auto rounded-lg border border-border/70 bg-background">
          {channels.length ? channels.map((channel) => {
            const selectedValue = channel.username ? `@${channel.username}` : String(channel.id)
            const selected = splitSources(value).includes(selectedValue)
            return (
              <button
                key={String(channel.id)}
                type="button"
                onClick={() => {
                  const current = splitSources(value)
                  onChange(Array.from(new Set([...current, selectedValue])).join('\n'))
                }}
                className={`flex w-full items-center justify-between gap-3 border-b border-border/50 px-3 py-2 text-left last:border-b-0 ${selected ? 'bg-foreground text-background' : 'hover:bg-foreground/[0.04]'}`}
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium">{channel.name}</span>
                  <span className={`block truncate text-xs ${selected ? 'text-background/70' : 'text-muted-foreground'}`}>
                    {channel.username ? `@${channel.username}` : String(channel.id)}
                    {channel.member_count ? ` · ${channel.member_count} a’zo` : ''}
                  </span>
                </span>
                {channel.is_own && (
                  <Badge variant={selected ? 'secondary' : 'outline'}>Sizniki</Badge>
                )}
              </button>
            )
          }) : (
            <div className="px-3 py-3 text-sm text-muted-foreground">
              Kanal topilmadi. Username yoki linkni qo‘lda yozing.
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function VoiceRecorder({
  onRecorded,
}: {
  onRecorded: (file: File) => void
}) {
  const [recorder, setRecorder] = useState<MediaRecorder | null>(null)
  const [recording, setRecording] = useState(false)

  useEffect(() => () => {
    recorder?.stream.getTracks().forEach((track) => track.stop())
  }, [recorder])

  async function toggleRecording() {
    if (recording && recorder) {
      recorder.stop()
      setRecording(false)
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const nextRecorder = new MediaRecorder(stream)
      const chunks: BlobPart[] = []
      nextRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunks.push(event.data)
      }
      nextRecorder.onstop = () => {
        const blob = new Blob(chunks, { type: nextRecorder.mimeType || 'audio/webm' })
        const file = new File([blob], `oqim-qoida-${Date.now()}.webm`, { type: blob.type })
        onRecorded(file)
        stream.getTracks().forEach((track) => track.stop())
      }
      nextRecorder.start()
      setRecorder(nextRecorder)
      setRecording(true)
    } catch {
      toast.error('Mikrofon ochilmadi. Audio fayl yuklang yoki matn yozing.')
    }
  }

  if (typeof navigator === 'undefined' || !navigator.mediaDevices) return null

  return (
    <Button
      type="button"
      variant={recording ? 'default' : 'outline'}
      className="mt-3 w-full"
      onClick={() => void toggleRecording()}
    >
      <Microphone size={16} weight="thin" />
      {recording ? 'To‘xtatish' : 'Mikrofondan yozish'}
    </Button>
  )
}

function SourcePanel({
  title,
  detail,
  status,
  children,
}: {
  title: string
  detail: string
  status: string
  children: React.ReactNode
}) {
  return (
    <section className="flex flex-col gap-3 rounded-xl border border-border/70 bg-muted/20 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-medium">{title}</div>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{detail}</p>
        </div>
        <Badge variant="outline" className="shrink-0">
          {status}
        </Badge>
      </div>
      {children}
    </section>
  )
}

function UploadDropzone({
  inputId,
  title,
  hint,
  accept,
  fileSource,
  onFileChange,
  onClear,
}: {
  inputId: string
  title: string
  hint: string
  accept: string
  fileSource: OnboardingFileSource | null
  onFileChange: (file: File | undefined) => void
  onClear: () => void
}) {
  return (
    <div>
      <label className="block cursor-pointer rounded-xl border-2 border-dashed border-border/70 bg-background px-4 py-5 text-center transition-colors hover:border-foreground/40 hover:bg-muted/60">
        <input
          id={inputId}
          aria-label={title}
          type="file"
          accept={accept}
          className="sr-only"
          onChange={(event) => onFileChange(event.target.files?.[0])}
        />
        <div className="mx-auto flex size-9 items-center justify-center rounded-full bg-foreground/[0.06]">
          <CloudArrowUp size={18} weight="thin" />
        </div>
        <div className="mt-2 text-sm">{title}</div>
        <div className="mt-1 font-mono text-[10px] uppercase tracking-[0.2em] text-muted-foreground">
          {hint}
        </div>
      </label>

      {fileSource ? (
        <div className="mt-3 flex items-center gap-3 rounded-lg border border-border/60 bg-background/80 px-3 py-2.5">
          <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-foreground/[0.06]">
            <FileGlyph fileName={fileSource.fileName} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-baseline justify-between gap-2">
              <span className="truncate text-sm">
                {fileSource.fileName} · {formatBytes(fileSource.byteSize)}
              </span>
            </div>
            <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-muted">
              <div className="h-full bg-emerald-500" style={{ width: '100%' }} />
            </div>
          </div>
          <button
            type="button"
            onClick={onClear}
            className="grid size-6 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-foreground/[0.05] hover:text-foreground"
            aria-label="Manbani olib tashlash"
          >
            <X size={14} weight="thin" />
          </button>
        </div>
      ) : null}
    </div>
  )
}

function FileGlyph({ fileName }: { fileName: string }) {
  const lower = fileName.toLowerCase()
  if (lower.endsWith('.pdf') || lower.endsWith('.md') || lower.endsWith('.txt') || lower.endsWith('.csv')) {
    return <FileText size={16} weight="thin" className="opacity-70" />
  }
  return <FileIcon size={16} weight="thin" className="opacity-70" />
}
