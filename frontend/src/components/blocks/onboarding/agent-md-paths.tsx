import { useRef, useState, type ChangeEvent } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { uz } from '@/lib/uz'
import {
  MicIcon,
  SparkleIcon,
  StopIcon,
  UploadIcon,
  type IconComponent,
} from '@/components/icons/doc-icons'
import { useGenerateAgentMd } from '@/hooks/use-onboarding-documents'
import { transcribeOnboardingAudio } from './audio-transcription'
import { readOnboardingFileSource } from '@/lib/file-source'

type AgentPath = 'defaults' | 'speak' | 'upload'

const PATHS: ReadonlyArray<{ key: AgentPath; label: string; icon: IconComponent }> = [
  { key: 'defaults', label: uz.onboarding.documents.agentPaths.tabDefaults, icon: SparkleIcon },
  { key: 'speak', label: uz.onboarding.documents.agentPaths.tabSpeak, icon: MicIcon },
  { key: 'upload', label: uz.onboarding.documents.agentPaths.tabUpload, icon: UploadIcon },
]

interface AgentMdPathsProps {
  agentId: number | undefined
  alreadyGenerated?: boolean
}

// AGENT.md shaping rail shown above the Agent-tab section cards. Three paths feed
// the same `owner_input` string into the per-agent generator: Defaults sends an
// empty string (template + BUSINESS.md), Speak transcribes a recording into an
// editable instruction, Upload reads pasted/.md/.txt text. When no agent exists
// yet the calm empty state holds the surface until the generate flow bootstraps it.
export function AgentMdPaths({ agentId, alreadyGenerated = false }: AgentMdPathsProps) {
  const t = uz.onboarding.documents.agentPaths
  const [path, setPath] = useState<AgentPath>('defaults')
  const generate = useGenerateAgentMd(agentId)
  const generateLabel = alreadyGenerated ? t.regenerate : t.generate

  if (!agentId) {
    return (
      <div className="grid place-items-center rounded-lg border border-dashed border-border px-6 py-8 text-center">
        <div className="max-w-sm">
          <p className="text-sm font-semibold text-foreground">{t.emptyTitle}</p>
          <p className="mt-1.5 text-sm text-muted-foreground">{t.emptyBody}</p>
        </div>
      </div>
    )
  }

  const isGenerating = generate.isPending
  const runGenerate = (ownerInput: string) => generate.mutate(ownerInput)

  return (
    <section className="grid gap-3 rounded-lg border border-border bg-card px-4 py-3.5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-foreground">
            {alreadyGenerated ? t.titleReady : t.title}
          </p>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {alreadyGenerated ? t.subtitleReady : t.subtitle}
          </p>
        </div>
        <div
          role="tablist"
          aria-label={t.title}
          className="inline-flex shrink-0 items-center gap-0.5 rounded-lg border border-border bg-muted/50 p-0.5"
        >
          {PATHS.map((option) => {
            const isActive = option.key === path
            const Icon = option.icon
            return (
              <button
                key={option.key}
                type="button"
                role="tab"
                aria-selected={isActive}
                onClick={() => setPath(option.key)}
                className={cn(
                  'inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm font-medium transition-colors',
                  isActive
                    ? 'bg-background text-foreground shadow-xs'
                    : 'text-muted-foreground hover:text-foreground',
                )}
              >
                <Icon className="size-4" />
                {option.label}
              </button>
            )
          })}
        </div>
      </div>

      {path === 'defaults' ? (
        <DefaultsPath isGenerating={isGenerating} onGenerate={() => runGenerate('')} generateLabel={generateLabel} />
      ) : path === 'speak' ? (
        <SpeakPath isGenerating={isGenerating} onGenerate={runGenerate} generateLabel={generateLabel} />
      ) : (
        <UploadPath isGenerating={isGenerating} onGenerate={runGenerate} generateLabel={generateLabel} />
      )}
    </section>
  )
}

function GenerateButton({
  isGenerating,
  disabled,
  onClick,
  label,
}: {
  isGenerating: boolean
  disabled?: boolean
  onClick: () => void
  label?: string
}) {
  const t = uz.onboarding.documents.agentPaths
  return (
    <Button
      type="button"
      size="sm"
      className="w-fit"
      disabled={isGenerating || disabled}
      onClick={onClick}
    >
      <SparkleIcon className="size-4" />
      {isGenerating ? t.generating : (label ?? t.generate)}
    </Button>
  )
}

function DefaultsPath({
  isGenerating,
  onGenerate,
  generateLabel,
}: {
  isGenerating: boolean
  onGenerate: () => void
  generateLabel?: string
}) {
  const t = uz.onboarding.documents.agentPaths
  return (
    <div className="grid gap-2.5">
      <p className="text-sm text-muted-foreground">{t.defaultsBody}</p>
      <GenerateButton isGenerating={isGenerating} onClick={onGenerate} label={generateLabel} />
    </div>
  )
}

type SpeakStatus = 'idle' | 'recording' | 'transcribing' | 'degraded'

function SpeakPath({
  isGenerating,
  onGenerate,
  generateLabel,
}: {
  isGenerating: boolean
  onGenerate: (ownerInput: string) => void
  generateLabel?: string
}) {
  const t = uz.onboarding.documents.agentPaths
  const [status, setStatus] = useState<SpeakStatus>('idle')
  const [ownerInput, setOwnerInput] = useState('')
  const [micUnavailable, setMicUnavailable] = useState(false)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<BlobPart[]>([])

  const transcribe = async (file: File) => {
    setStatus('transcribing')
    try {
      const fileSource = await readOnboardingFileSource(file)
      const result = await transcribeOnboardingAudio(fileSource)
      const transcript = result?.transcript?.trim()
      if (result?.status === 'ready' && transcript) {
        setOwnerInput((current) => (current.trim() ? `${current.trim()}\n\n${transcript}` : transcript))
        setStatus('idle')
        return
      }
      setStatus('degraded')
    } catch {
      setStatus('degraded')
    }
  }

  const startRecording = async () => {
    if (
      typeof navigator === 'undefined' ||
      !navigator.mediaDevices?.getUserMedia ||
      typeof MediaRecorder === 'undefined'
    ) {
      setMicUnavailable(true)
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const recorder = new MediaRecorder(stream)
      chunksRef.current = []
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data)
      }
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' })
        const file = new File([blob], `oqim-agent-ovoz-${Date.now()}.webm`, { type: blob.type })
        recorder.stream.getTracks().forEach((track) => track.stop())
        void transcribe(file)
      }
      recorderRef.current = recorder
      recorder.start()
      setStatus('recording')
    } catch {
      setMicUnavailable(true)
    }
  }

  const stopRecording = () => {
    const recorder = recorderRef.current
    if (!recorder || recorder.state === 'inactive') return
    recorder.stop()
  }

  const isRecording = status === 'recording'
  const isTranscribing = status === 'transcribing'

  return (
    <div className="grid gap-2.5">
      <p className="text-sm text-muted-foreground">{t.speakBody}</p>
      {micUnavailable ? (
        <p className="text-sm text-muted-foreground">{t.speakUnavailable}</p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            className={cn(
              'w-fit',
              isRecording &&
                'border-destructive bg-destructive text-destructive-foreground hover:bg-destructive/90 hover:text-destructive-foreground',
            )}
            disabled={isTranscribing || isGenerating}
            onClick={isRecording ? stopRecording : startRecording}
          >
            {isRecording ? <StopIcon className="size-4" /> : <MicIcon className="size-4" />}
            {isRecording ? t.speakStop : t.speakStart}
          </Button>
          <span className="min-h-5 text-xs text-muted-foreground">
            {isRecording ? t.speakRecording : isTranscribing ? t.speakTranscribing : null}
            {status === 'degraded' ? t.speakDegraded : null}
          </span>
        </div>
      )}
      <OwnerInputField value={ownerInput} onChange={setOwnerInput} />
      <GenerateButton
        isGenerating={isGenerating}
        disabled={isTranscribing || !ownerInput.trim()}
        onClick={() => onGenerate(ownerInput.trim())}
        label={generateLabel}
      />
    </div>
  )
}

function UploadPath({
  isGenerating,
  onGenerate,
  generateLabel,
}: {
  isGenerating: boolean
  onGenerate: (ownerInput: string) => void
  generateLabel?: string
}) {
  const t = uz.onboarding.documents.agentPaths
  const [ownerInput, setOwnerInput] = useState('')

  const handleFile = async (file: File | undefined) => {
    if (!file) return
    try {
      const text = await file.text()
      const next = text.trim()
      if (!next) return
      setOwnerInput((current) => (current.trim() ? `${current.trim()}\n\n${next}` : next))
    } catch {
      toast.error(t.uploadReadError)
    }
  }

  return (
    <div className="grid gap-2.5">
      <p className="text-sm text-muted-foreground">{t.uploadBody}</p>
      <OwnerInputField value={ownerInput} onChange={setOwnerInput} placeholder={t.uploadBody} />
      <div className="flex flex-wrap items-center gap-2">
        <label
          htmlFor="agent-md-upload"
          className="inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-md border border-input bg-background px-3 text-sm font-medium shadow-xs transition-colors hover:bg-accent hover:text-accent-foreground"
        >
          <UploadIcon className="size-4" />
          {t.uploadFile}
        </label>
        <input
          id="agent-md-upload"
          type="file"
          accept=".md,.txt,text/markdown,text/plain"
          aria-label={t.uploadFile}
          className="sr-only"
          onChange={(event: ChangeEvent<HTMLInputElement>) => {
            void handleFile(event.target.files?.[0])
            event.target.value = ''
          }}
        />
        <span className="text-xs text-muted-foreground">{t.uploadPdfNote}</span>
      </div>
      <GenerateButton
        isGenerating={isGenerating}
        disabled={!ownerInput.trim()}
        onClick={() => onGenerate(ownerInput.trim())}
        label={generateLabel}
      />
    </div>
  )
}

function OwnerInputField({
  value,
  onChange,
  placeholder,
}: {
  value: string
  onChange: (value: string) => void
  placeholder?: string
}) {
  const t = uz.onboarding.documents.agentPaths
  return (
    <Textarea
      aria-label={t.ownerInputLabel}
      value={value}
      onChange={(event: ChangeEvent<HTMLTextAreaElement>) => onChange(event.target.value)}
      placeholder={placeholder ?? t.ownerInputPlaceholder}
      className="min-h-20 resize-none rounded-lg"
    />
  )
}
