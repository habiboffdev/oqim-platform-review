import { useState, type ChangeEvent, type ReactNode } from 'react'
import {
  ArrowLeft,
  ArrowRight,
  Check,
  FileText,
  GlobeHemisphereWest,
  Microphone,
  PencilSimpleLine,
} from '@phosphor-icons/react'
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
import { Textarea } from '@/components/ui/textarea'
import { readOnboardingFileSource, type OnboardingFileSource } from '@/lib/file-source'
import { cn } from '@/lib/utils'
import { uz } from '@/lib/uz'
import { transcribeOnboardingAudio } from './audio-transcription'
import {
  MESSAGE_VOLUME_OPTIONS,
  REPLY_MODE_OPTIONS,
  REPLY_TEAM_OPTIONS,
  TONE_OPTIONS,
} from './constants'
import { OptionGroup } from './option-group'
import { readSelectedOnboardingFile } from './source-items'
import type {
  MessageVolumeKey,
  ReplyModeKey,
  ReplyTeamKey,
  ToneKey,
} from './types'

export function PreferencesStep({
  messageVolume,
  replyTeamSize,
  tone,
  replyMode,
  ownerRules,
  agentWebsiteSource,
  agentFileSource,
  voiceSource,
  voiceFileSource,
  onMessageVolumeChange,
  onReplyTeamSizeChange,
  onToneChange,
  onReplyModeChange,
  onOwnerRulesChange,
  onAgentWebsiteSourceChange,
  onAgentFileSourceChange,
  onVoiceSourceChange,
  onVoiceFileSourceChange,
  onBack,
  onNext,
}: {
  messageVolume: MessageVolumeKey
  replyTeamSize: ReplyTeamKey
  tone: ToneKey
  replyMode: ReplyModeKey
  ownerRules: string
  agentWebsiteSource: string
  agentFileSource: OnboardingFileSource | null
  voiceSource: string
  voiceFileSource: OnboardingFileSource | null
  onMessageVolumeChange: (value: MessageVolumeKey) => void
  onReplyTeamSizeChange: (value: ReplyTeamKey) => void
  onToneChange: (value: ToneKey) => void
  onReplyModeChange: (value: ReplyModeKey) => void
  onOwnerRulesChange: (value: string) => void
  onAgentWebsiteSourceChange: (value: string) => void
  onAgentFileSourceChange: (value: OnboardingFileSource | null) => void
  onVoiceSourceChange: (value: string) => void
  onVoiceFileSourceChange: (value: OnboardingFileSource | null) => void
  onBack: () => void
  onNext: () => void
}) {
  const [showWebsite, setShowWebsite] = useState(Boolean(agentWebsiteSource.trim()))
  const [showVoice, setShowVoice] = useState(Boolean(voiceSource.trim() || voiceFileSource))
  const [audioTranscriptStatus, setAudioTranscriptStatus] = useState<'idle' | 'transcribing' | 'ready' | 'degraded'>('idle')
  const [audioTranscriptMessage, setAudioTranscriptMessage] = useState('')
  const agentSourceCount = [
    ownerRules.trim(),
    agentWebsiteSource.trim(),
    agentFileSource,
    voiceSource.trim(),
  ].filter(Boolean).length

  async function handleVoiceFile(file: File | undefined) {
    if (!file) {
      onVoiceFileSourceChange(null)
      setAudioTranscriptStatus('idle')
      setAudioTranscriptMessage('')
      return
    }
    try {
      const fileSource = await readOnboardingFileSource(file)
      onVoiceFileSourceChange(fileSource)
      setShowVoice(true)
      setAudioTranscriptStatus('transcribing')
      setAudioTranscriptMessage('Audio matnga aylantirilmoqda...')
      const transcript = await transcribeOnboardingAudio(fileSource)
      if (transcript?.transcript) {
        onVoiceSourceChange(transcript.transcript)
        setAudioTranscriptStatus('ready')
        setAudioTranscriptMessage('Matn tayyor. Keraksiz joyini tahrirlab davom eting.')
        return
      }
      setAudioTranscriptStatus('degraded')
      setAudioTranscriptMessage(transcript?.error_label ?? 'Audio tushunarli matn bermadi. Qo‘lda yozishingiz mumkin.')
    } catch {
      onVoiceFileSourceChange(null)
      setAudioTranscriptStatus('degraded')
      setAudioTranscriptMessage('Audio matnga aylantirilmadi. Qayta urinib ko‘ring yoki qo‘lda yozing.')
    }
  }

  return (
    <Card className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg py-0">
      <form
        className="flex h-full min-h-0 flex-col"
        onSubmit={(event) => {
          event.preventDefault()
          onNext()
        }}
      >
        <CardHeader className="shrink-0 px-5 py-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <CardTitle className="font-sans text-xl font-semibold tracking-tight">Agent manbalari</CardTitle>
              <CardDescription>AGENT.md, SKILL.md, qoida, sayt yoki ovoz. OQIM bularni agent fayliga aylantiradi.</CardDescription>
            </div>
            <span className="shrink-0 rounded-md border px-2 py-1 text-xs font-medium text-muted-foreground">
              {agentSourceCount} yangi
            </span>
          </div>
        </CardHeader>

        <CardContent className="grid min-h-0 flex-1 gap-3 overflow-y-auto px-5 pb-4">
          <div className="grid gap-2 sm:grid-cols-2">
            <Label
              htmlFor="agent-file-source"
              className={cn(
                'relative flex min-h-[3.75rem] cursor-pointer items-center gap-3 rounded-lg border border-border bg-background px-3.5 py-2.5 text-sm shadow-xs transition-colors hover:bg-muted/40',
                agentFileSource && 'border-foreground bg-muted/60',
              )}
            >
              <AgentSourceIcon active={Boolean(agentFileSource)}>
                <FileText />
              </AgentSourceIcon>
              <span className="grid min-w-0 flex-1 gap-1">
                <span className="text-sm font-semibold leading-5">Fayl</span>
                <span className="line-clamp-2 text-xs font-normal leading-4 text-muted-foreground">
                  {agentFileSource?.fileName ?? 'AGENT.md yoki SKILL.md'}
                </span>
              </span>
            </Label>
            <Input
              id="agent-file-source"
              type="file"
              aria-label="Agent fayli"
              accept=".md,.txt,.pdf,.png,.jpg,.jpeg,.webp,text/markdown,text/plain,application/pdf,image/*"
              className="sr-only"
              onChange={(event: ChangeEvent<HTMLInputElement>) => {
                void readSelectedOnboardingFile(event.target.files?.[0], onAgentFileSourceChange)
              }}
            />

            <AgentSourceButton
              icon={<GlobeHemisphereWest />}
              title="Sayt"
              detail={agentWebsiteSource.trim() ? 'sayt qo‘shildi' : 'agent qoidalari sahifasi'}
              active={showWebsite || Boolean(agentWebsiteSource.trim())}
              onClick={() => setShowWebsite((value) => !value)}
            />
            <AgentSourceButton
              icon={<PencilSimpleLine />}
              title="Qoidalar"
              detail={ownerRules.trim() ? 'qo‘lda yozildi' : 'qachon so‘rash, nimani qilmaslik'}
              active={Boolean(ownerRules.trim())}
              onClick={() => document.getElementById('owner-rules')?.focus()}
            />
            <AgentSourceButton
              icon={<Microphone />}
              title="Audio izoh"
              detail={voiceSource.trim() ? 'tahrirlanadigan matn tayyor' : voiceFileSource?.fileName ?? 'audio → matn'}
              active={showVoice || Boolean(voiceSource.trim() || voiceFileSource)}
              onClick={() => setShowVoice((value) => !value)}
            />
          </div>

          {showWebsite ? (
            <div className="grid gap-2">
              <Label htmlFor="agent-website-source">Agent sayti</Label>
              <Textarea
                id="agent-website-source"
                value={agentWebsiteSource}
                onChange={(event: ChangeEvent<HTMLTextAreaElement>) => onAgentWebsiteSourceChange(event.target.value)}
                placeholder="https://..."
                className="min-h-16 resize-none rounded-lg"
              />
            </div>
          ) : null}

          <section className="grid gap-2">
            <Label htmlFor="owner-rules">{uz.onboarding.ownerRules}</Label>
            <Textarea
              id="owner-rules"
              value={ownerRules}
              onChange={(event: ChangeEvent<HTMLTextAreaElement>) => onOwnerRulesChange(event.target.value)}
              placeholder={uz.onboarding.ownerRulesPlaceholder}
              className="min-h-24 resize-none rounded-lg"
            />
          </section>

          {showVoice ? (
            <section className="grid gap-2 rounded-lg border border-border/80 p-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <Label htmlFor="voice-source">{uz.onboarding.voiceSource}</Label>
                  <p className="mt-1 text-sm text-muted-foreground">
                    Audio faqat matn yozishni tezlashtiradi. Davom etsangiz, shu tahrirlangan matn oddiy agent manbasi sifatida o‘qiladi.
                  </p>
                </div>
                <Label
                  htmlFor="voice-audio-source"
                  className="inline-flex h-9 shrink-0 cursor-pointer items-center justify-center gap-2 rounded-md border border-input bg-background px-3 text-sm font-medium shadow-xs transition-colors hover:bg-accent hover:text-accent-foreground"
                >
                  <Microphone className="size-4" />
                  Audio → matn
                </Label>
                <Input
                  id="voice-audio-source"
                  type="file"
                  aria-label={uz.onboarding.voiceAudioSource}
                  accept="audio/*"
                  className="sr-only"
                  onChange={(event: ChangeEvent<HTMLInputElement>) => {
                    void handleVoiceFile(event.target.files?.[0])
                  }}
                />
              </div>
              <Textarea
                id="voice-source"
                aria-label={uz.onboarding.voiceSource}
                value={voiceSource}
                onChange={(event: ChangeEvent<HTMLTextAreaElement>) => onVoiceSourceChange(event.target.value)}
                placeholder={uz.onboarding.voiceSourcePlaceholder}
                className="min-h-20 resize-none rounded-lg"
              />
              {audioTranscriptStatus !== 'idle' ? (
                <p className={audioTranscriptStatus === 'degraded' ? 'text-xs text-orange-600' : 'text-xs text-muted-foreground'}>
                  {audioTranscriptMessage}
                </p>
              ) : null}
            </section>
          ) : null}

          <section className="grid gap-3 rounded-lg border border-border/80 p-3">
            <div>
              <h2 className="text-sm font-semibold">Agent qanday ishlaydi</h2>
              <p className="mt-1 text-sm text-muted-foreground">Bu tanlovlar keyin Agentlar sahifasida o‘zgaradi.</p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <OptionGroup
                label={uz.onboarding.messageVolume}
                options={MESSAGE_VOLUME_OPTIONS}
                value={messageVolume}
                onChange={onMessageVolumeChange}
              />
              <OptionGroup
                label={uz.onboarding.replyTeamSize}
                options={REPLY_TEAM_OPTIONS}
                value={replyTeamSize}
                onChange={onReplyTeamSizeChange}
              />
              <OptionGroup
                label={uz.onboarding.sellerTone}
                options={TONE_OPTIONS}
                value={tone}
                onChange={onToneChange}
              />
              <OptionGroup
                label={uz.onboarding.agentMode}
                options={REPLY_MODE_OPTIONS}
                value={replyMode}
                onChange={onReplyModeChange}
              />
            </div>
          </section>
        </CardContent>

        <CardFooter className="shrink-0 justify-between border-t px-5 py-3">
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

function AgentSourceButton({
  icon,
  title,
  detail,
  active,
  onClick,
}: {
  icon: ReactNode
  title: string
  detail: string
  active: boolean
  onClick: () => void
}) {
  return (
    <Button
      type="button"
      variant="outline"
      className={cn(
                'relative !h-[3.75rem] justify-start gap-3 rounded-lg px-3.5 py-2.5 text-left',
                active && 'border-foreground bg-muted/60',
              )}
      onClick={onClick}
    >
      <AgentSourceIcon active={active}>{icon}</AgentSourceIcon>
      <span className="grid min-w-0 flex-1 gap-1">
        <span className="text-sm font-semibold leading-5">{title}</span>
        <span className="line-clamp-2 whitespace-normal break-words text-xs font-normal leading-4 text-muted-foreground">{detail}</span>
      </span>
    </Button>
  )
}

function AgentSourceIcon({
  active,
  children,
}: {
  active: boolean
  children: ReactNode
}) {
  return (
    <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted text-foreground [&_svg]:size-5">
      {active ? <Check /> : children}
    </span>
  )
}
