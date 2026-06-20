import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import {
  ArrowLeft,
  ArrowRight,
  Check,
  FileText,
  GlobeHemisphereWest,
  Microphone,
  StopCircle,
  TelegramLogo,
} from '@phosphor-icons/react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { api } from '@/lib/api-client'
import { formatBytes, readOnboardingFileSource, type OnboardingFileSource } from '@/lib/file-source'
import { cn } from '@/lib/utils'
import { uz } from '@/lib/uz'
import { transcribeOnboardingAudio } from './audio-transcription'
import { readSelectedOnboardingFile, splitSourceLines } from './source-items'
import type { TelegramChannelOption } from './types'

export function SourcesStep({
  sourceNotes,
  websiteSource,
  telegramChannelSource,
  telegramStartDate,
  telegramEndDate,
  fileSource,
  onSourceNotesChange,
  onWebsiteSourceChange,
  onTelegramChannelSourceChange,
  onTelegramStartDateChange,
  onTelegramEndDateChange,
  onFileSourceChange,
  onBack,
  onNext,
}: {
  sourceNotes: string
  websiteSource: string
  telegramChannelSource: string
  telegramStartDate: string
  telegramEndDate: string
  fileSource: OnboardingFileSource | null
  onSourceNotesChange: (value: string) => void
  onWebsiteSourceChange: (value: string) => void
  onTelegramChannelSourceChange: (value: string) => void
  onTelegramStartDateChange: (value: string) => void
  onTelegramEndDateChange: (value: string) => void
  onFileSourceChange: (value: OnboardingFileSource | null) => void
  onBack: () => void
  onNext: () => void
}) {
  const [showWebsite, setShowWebsite] = useState(Boolean(websiteSource.trim()))
  const [showManual, setShowManual] = useState(Boolean(sourceNotes.trim()))
  const [channels, setChannels] = useState<TelegramChannelOption[]>([])
  const [channelsLoading, setChannelsLoading] = useState(false)
  const [channelsLoaded, setChannelsLoaded] = useState(false)
  const [channelScope, setChannelScope] = useState<'own' | 'all'>('own')
  const [showChannelInput, setShowChannelInput] = useState(Boolean(telegramChannelSource.trim()))
  const [isRecording, setIsRecording] = useState(false)
  const [audioTranscriptStatus, setAudioTranscriptStatus] = useState<'idle' | 'transcribing' | 'ready' | 'degraded'>('idle')
  const [audioTranscriptMessage, setAudioTranscriptMessage] = useState('')
  const [lastTranscriptFileName, setLastTranscriptFileName] = useState('')
  const [transcribingFileSource, setTranscribingFileSource] = useState<OnboardingFileSource | null>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const audioChunksRef = useRef<BlobPart[]>([])

  useEffect(() => {
    let cancelled = false
    async function loadChannels() {
      setChannelsLoading(true)
      try {
        const payload = await api.get<{ channels?: TelegramChannelOption[] }>('/api/telegram/channels')
        if (!cancelled) {
          setChannels(payload.channels ?? [])
          setChannelsLoaded(true)
        }
      } catch {
        if (!cancelled) {
          setChannels([])
          setChannelsLoaded(true)
        }
      } finally {
        if (!cancelled) setChannelsLoading(false)
      }
    }
    void loadChannels()
    return () => {
      cancelled = true
      mediaRecorderRef.current?.stream.getTracks().forEach((track) => track.stop())
    }
  }, [])

  useEffect(() => {
    if (sourceNotes.trim()) setShowManual(true)
  }, [sourceNotes])

  const visibleChannels = channels.filter((channel) => channelScope === 'all' || channel.is_own)
  const selectedChannels = splitSourceLines(telegramChannelSource)
  const draftSourceCount = [
    sourceNotes.trim(),
    websiteSource.trim(),
    telegramChannelSource.trim(),
    fileSource,
  ].filter(Boolean).length

  const appendTranscriptToNotes = (transcript: string) => {
    const nextTranscript = transcript.trim()
    if (!nextTranscript) return
    const current = sourceNotes.trim()
    onSourceNotesChange(current ? `${current}\n\n${nextTranscript}` : nextTranscript)
  }

  const toggleChannel = (channel: TelegramChannelOption) => {
    const nextValue = channel.username ? `@${channel.username}` : String(channel.id)
    const selected = selectedChannels.includes(nextValue)
    const next = selected
      ? selectedChannels.filter((item) => item !== nextValue)
      : [...selectedChannels, nextValue]
    onTelegramChannelSourceChange(Array.from(new Set(next)).join('\n'))
  }

  const handleBrainVoiceFile = async (file: File | undefined) => {
    if (!file) {
      setTranscribingFileSource(null)
      setAudioTranscriptStatus('idle')
      setAudioTranscriptMessage('')
      setLastTranscriptFileName('')
      return
    }
    try {
      const fileSource = await readOnboardingFileSource(file)
      setTranscribingFileSource(fileSource)
      setLastTranscriptFileName(fileSource.fileName)
      setShowManual(true)
      setAudioTranscriptStatus('transcribing')
      setAudioTranscriptMessage('Audio matnga aylantirilmoqda...')
      const transcript = await transcribeOnboardingAudio(fileSource)
      if (transcript?.transcript) {
        appendTranscriptToNotes(transcript.transcript)
        setTranscribingFileSource(null)
        setAudioTranscriptStatus('ready')
        setAudioTranscriptMessage('Matn qo‘lda yozish maydoniga qo‘shildi. Audio alohida o‘qitilmaydi.')
        return
      }
      setTranscribingFileSource(null)
      setAudioTranscriptStatus('degraded')
      setAudioTranscriptMessage(transcript?.error_label ?? 'Audio tushunarli matn bermadi. Qo‘lda yozishingiz mumkin.')
    } catch {
      setTranscribingFileSource(null)
      setAudioTranscriptStatus('degraded')
      setAudioTranscriptMessage('Audio matnga aylantirilmadi. Qayta urinib ko‘ring yoki qo‘lda yozing.')
    }
  }

  const startRecording = async () => {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      toast.error('Brauzer mikrofon yozishni qo‘llamayapti')
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const recorder = new MediaRecorder(stream)
      audioChunksRef.current = []
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) audioChunksRef.current.push(event.data)
      }
      recorder.onstop = () => {
        const blob = new Blob(audioChunksRef.current, { type: recorder.mimeType || 'audio/webm' })
        const file = new File([blob], `oqim-ovoz-${Date.now()}.webm`, { type: blob.type })
        setShowManual(true)
        void handleBrainVoiceFile(file)
        recorder.stream.getTracks().forEach((track) => track.stop())
      }
      mediaRecorderRef.current = recorder
      recorder.start()
      setIsRecording(true)
    } catch {
      toast.error('Mikrofon ruxsati berilmadi')
    }
  }

  const stopRecording = () => {
    const recorder = mediaRecorderRef.current
    if (!recorder || recorder.state === 'inactive') return
    recorder.stop()
    setIsRecording(false)
  }

  return (
    <Card className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg py-0">
      <form
        onSubmit={(event) => {
          event.preventDefault()
          onNext()
        }}
        className="flex h-full min-h-0 flex-col"
      >
        <CardHeader className="px-5 py-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <CardTitle className="font-sans text-xl font-semibold tracking-tight">Manba qo‘shish</CardTitle>
              <CardDescription>Fayl, sayt, kanal yoki matn. Audio avval tahrirlanadigan matnga aylanadi.</CardDescription>
            </div>
            <span className="shrink-0 rounded-md border px-2 py-1 text-xs font-medium text-muted-foreground">
              {draftSourceCount} yangi
            </span>
          </div>
        </CardHeader>
        <CardContent className="grid min-h-0 flex-1 gap-3 overflow-y-auto px-5 pb-4">
          <div className="grid gap-2 sm:grid-cols-2">
            <SourceButton
              icon={<FileText />}
              title="Fayl"
              description={fileSource ? `${fileSource.fileName} · ${formatBytes(fileSource.byteSize)}` : 'PDF, Excel, rasm, hujjat'}
              active={Boolean(fileSource)}
            >
              <Label htmlFor="file-source" className="absolute inset-0 cursor-pointer">
                <span className="sr-only">Fayl tanlash</span>
              </Label>
              <input
                id="file-source"
                type="file"
                aria-label={uz.onboarding.fileSource}
                accept=".pdf,.png,.jpg,.jpeg,.webp,.heic,.txt,.csv,.xlsx,.xlsm,.md,application/pdf,image/*,text/plain,text/csv,text/markdown,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel"
                className="sr-only"
                onChange={(event: ChangeEvent<HTMLInputElement>) => {
                  void readSelectedOnboardingFile(event.target.files?.[0], onFileSourceChange)
                }}
              />
            </SourceButton>
            <SourceButton
              icon={<GlobeHemisphereWest />}
              title="Sayt"
              description={websiteSource.trim() ? 'Sayt qo‘shildi' : 'URL manba qo‘shish'}
              active={showWebsite || Boolean(websiteSource.trim())}
              onClick={() => setShowWebsite((value) => !value)}
            />
            <SourceButton
              icon={<Microphone />}
              title="Ovozdan matn"
              description={audioTranscriptStatus === 'transcribing'
                ? 'matnga aylanmoqda'
                : sourceNotes.trim()
                  ? 'matn maydonida'
                  : 'audio → tahrirlanadigan matn'}
              active={showManual || audioTranscriptStatus !== 'idle'}
              onClick={() => setShowManual((value) => !value)}
            />
            <SourceButton
              icon={<FileText />}
              title="Matn"
              description={sourceNotes.trim() ? 'qo‘lda yozildi' : 'qoida, narx, eslatma'}
              active={showManual || Boolean(sourceNotes.trim())}
              onClick={() => setShowManual((value) => !value)}
            />
          </div>

          {showWebsite ? (
            <div className="grid gap-2">
              <Label htmlFor="website-source" className="text-sm font-medium">
                {uz.onboarding.websiteSource}
              </Label>
              <Textarea
                id="website-source"
                aria-label={uz.onboarding.websiteSource}
                value={websiteSource}
                onChange={(event: ChangeEvent<HTMLTextAreaElement>) => onWebsiteSourceChange(event.target.value)}
                placeholder="https://kompaniya.uz&#10;https://kurs-sahifasi.uz"
                className="min-h-16 resize-none rounded-lg"
              />
            </div>
          ) : null}

          {showManual ? (
            <section className="grid gap-2 rounded-lg border border-border/80 p-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <Label htmlFor="source-notes">Qo‘lda yozish</Label>
                  <p className="mt-1 text-sm text-muted-foreground">
                    Audio, skrinshotdan eslatma yoki qisqa biznes konteksti shu yerda tahrirlanadi.
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    className={cn(
                      'rounded-full',
                      isRecording && 'border-destructive bg-destructive text-destructive-foreground hover:bg-destructive/90 hover:text-destructive-foreground',
                    )}
                    aria-label={isRecording ? 'Ovoz yozishni to‘xtatish' : 'Ovoz yozish'}
                    onClick={isRecording ? stopRecording : startRecording}
                  >
                    {isRecording ? <StopCircle className="size-4" /> : <Microphone className="size-4" />}
                  </Button>
                  <Label
                    htmlFor="brain-voice-file"
                    className="inline-flex h-9 cursor-pointer items-center justify-center rounded-md border border-input bg-background px-3 text-sm font-medium shadow-xs transition-colors hover:bg-accent hover:text-accent-foreground"
                  >
                    Audio → matn
                  </Label>
                  <input
                    id="brain-voice-file"
                    type="file"
                    accept="audio/*"
                    className="sr-only"
                    onChange={(event: ChangeEvent<HTMLInputElement>) => {
                      void handleBrainVoiceFile(event.target.files?.[0])
                    }}
                  />
                </div>
              </div>
              <Textarea
                id="source-notes"
                aria-label={uz.onboarding.sources}
                value={sourceNotes}
                onChange={(event: ChangeEvent<HTMLTextAreaElement>) => onSourceNotesChange(event.target.value)}
                placeholder={uz.onboarding.sourcesPlaceholder}
                className="min-h-20 resize-none rounded-lg"
              />
              <div className="flex min-h-5 flex-wrap items-center gap-2 text-xs text-muted-foreground">
                {isRecording ? <span className="text-destructive">Yozilyapti...</span> : null}
                {audioTranscriptStatus !== 'idle' ? (
                  <span className={audioTranscriptStatus === 'degraded' ? 'text-orange-600' : ''}>
                    {(transcribingFileSource?.fileName ?? lastTranscriptFileName) ? `${transcribingFileSource?.fileName ?? lastTranscriptFileName}: ` : ''}
                    {audioTranscriptMessage}
                  </span>
                ) : null}
              </div>
            </section>
          ) : null}

          <section className="grid gap-3 rounded-lg border border-border/80 p-3">
            <div className="flex items-center justify-between gap-3">
              <div>
                <Label>Telegram kanallari</Label>
                <p className="mt-1 text-sm text-muted-foreground">Kanal tanlang yoki username yozing.</p>
              </div>
              <Tabs value={channelScope} onValueChange={(value) => setChannelScope(value as 'own' | 'all')}>
                <TabsList className="h-9 rounded-lg">
                  <TabsTrigger value="own" className="rounded-md">O‘zimniki</TabsTrigger>
                  <TabsTrigger value="all" className="rounded-md">Hammasi</TabsTrigger>
                </TabsList>
              </Tabs>
            </div>

            <div className="grid max-h-[11.75rem] gap-2 overflow-y-auto pr-1">
              {channelsLoading ? (
                <div className="rounded-lg border border-dashed border-border px-3 py-8 text-center text-sm text-muted-foreground">
                  Kanallar o‘qilmoqda...
                </div>
              ) : channelsLoaded && visibleChannels.length > 0 ? (
                visibleChannels.slice(0, 3).map((channel) => {
                  const value = channel.username ? `@${channel.username}` : String(channel.id)
                  const selected = selectedChannels.includes(value)
                  return (
                    <Button
                      key={String(channel.id)}
                      type="button"
                      variant="outline"
                      className={cn(
                        '!h-auto min-h-11 w-full justify-start rounded-lg px-3 py-2.5',
                        selected && 'border-foreground bg-foreground text-background hover:bg-foreground/90 hover:text-background',
                      )}
                      onClick={() => toggleChannel(channel)}
                    >
                      <span className={cn('grid size-7 shrink-0 place-items-center rounded-md bg-muted', selected && 'bg-background/10')}>
                        <TelegramLogo className="size-4" />
                      </span>
                      <span className="min-w-0 flex-1 text-left">
                        <span className="block truncate font-medium">{channel.username ? `@${channel.username}` : channel.name}</span>
                        <span className="block truncate text-xs opacity-70">
                          {channel.member_count ? `${channel.member_count.toLocaleString('uz-UZ')} ta xabar` : channel.name}
                        </span>
                      </span>
                      <span className={cn('size-2 rounded-full bg-muted-foreground/30', selected && 'bg-emerald-500')} />
                    </Button>
                  )
                })
              ) : (
                <Textarea
                  id="telegram-channel-source"
                  aria-label={uz.onboarding.telegramChannelSource}
                  value={telegramChannelSource}
                  onChange={(event: ChangeEvent<HTMLTextAreaElement>) => onTelegramChannelSourceChange(event.target.value)}
                  placeholder="@kanal_nomi&#10;@ikkinchi_kanal"
                  className="min-h-16 resize-none rounded-lg"
                />
              )}
            </div>

            {channelsLoaded && visibleChannels.length > 0 ? (
              <div className="grid gap-2">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="w-fit px-0 text-muted-foreground hover:bg-transparent hover:text-foreground"
                  onClick={() => setShowChannelInput((value) => !value)}
                >
                  {showChannelInput ? 'Kanal maydonini yopish' : 'Kanal username bilan qo‘shish'}
                </Button>
                {showChannelInput ? (
                  <Textarea
                    id="telegram-channel-source"
                    aria-label={uz.onboarding.telegramChannelSource}
                    value={telegramChannelSource}
                    onChange={(event: ChangeEvent<HTMLTextAreaElement>) => onTelegramChannelSourceChange(event.target.value)}
                    placeholder="@kanal_nomi&#10;@ikkinchi_kanal"
                    className="min-h-12 resize-none rounded-lg"
                  />
                ) : null}
              </div>
            ) : null}

            <div className="grid gap-2">
              <Label>Qaysi davrni o‘qish kerak?</Label>
              <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] sm:items-center">
                <Input
                  type="date"
                  value={telegramStartDate}
                  aria-label={uz.onboarding.telegramDateFrom}
                  onChange={(event: ChangeEvent<HTMLInputElement>) => onTelegramStartDateChange(event.target.value)}
                  className="rounded-lg"
                />
                <span className="hidden text-muted-foreground sm:block">-</span>
                <Input
                  type="date"
                  value={telegramEndDate}
                  aria-label={uz.onboarding.telegramDateTo}
                  onChange={(event: ChangeEvent<HTMLInputElement>) => onTelegramEndDateChange(event.target.value)}
                  className="rounded-lg"
                />
              </div>
            </div>
          </section>

        </CardContent>
        <CardFooter className="justify-between border-t px-5 py-3">
          <Button variant="ghost" type="button" onClick={onBack}>
            <ArrowLeft />
            {uz.onboarding.back}
          </Button>
          <Button type="submit" disabled={audioTranscriptStatus === 'transcribing'}>
            {audioTranscriptStatus === 'transcribing' ? 'Audio matnga aylanmoqda' : uz.onboarding.businessContinue}
            <ArrowRight />
          </Button>
        </CardFooter>
      </form>
    </Card>
  )
}

function SourceButton({
  icon,
  title,
  description,
  active,
  children,
  onClick,
}: {
  icon: React.ReactNode
  title: string
  description: string
  active: boolean
  children?: React.ReactNode
  onClick?: () => void
}) {
  return (
    <Button
      type="button"
      variant="outline"
      className={cn(
        'relative !h-[3.75rem] flex-row justify-start gap-3 rounded-lg px-3.5 py-2.5 text-left',
        active && 'border-foreground bg-muted/60',
      )}
      onClick={onClick}
    >
      <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted text-foreground [&_svg]:size-5">
        {active ? <Check /> : icon}
      </span>
      <span className="grid min-w-0 flex-1 gap-1">
        <span className="text-sm font-semibold leading-5">{title}</span>
        <span className="line-clamp-2 whitespace-normal break-words text-xs font-normal leading-4 text-muted-foreground">{description}</span>
      </span>
      {children}
    </Button>
  )
}
