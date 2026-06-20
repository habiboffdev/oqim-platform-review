import { useMemo } from 'react'

import { PauseCircle, PlayCircle } from '@phosphor-icons/react'

import { uz } from '@/lib/uz'
import { useAudioPlayer } from '@/hooks/use-audio-player'
import { decodeWaveform } from './decode-waveform'
import { Waveform } from './waveform'
import type { Message } from '@/lib/types'
import { getFullMediaUrl } from './urls'

interface VoiceMeta {
  duration?: number
  waveform?: number[]
}

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

export function VoiceMessage({ message }: { message: Message }) {
  const meta = message.media_metadata as VoiceMeta | undefined
  const durationSec = meta?.duration ?? 0
  const fullUrl = getFullMediaUrl(message) ?? ''

  const samples = useMemo(() => decodeWaveform(meta?.waveform), [meta?.waveform])

  const { activeMessageId, isPlaying, currentTime, duration, play, pause, seek } =
    useAudioPlayer()
  const isActive = activeMessageId === message.id
  const isThisPlaying = isActive && isPlaying
  const progress = isActive && duration > 0 ? currentTime / duration : 0

  const displaySeconds = isActive ? currentTime : durationSec
  const displayTime = formatDuration(displaySeconds)

  const handleToggle = () => {
    if (!fullUrl) return
    if (isThisPlaying) {
      pause()
    } else {
      play(message.id, fullUrl, durationSec, 'voice', uz.conversations.voice)
    }
  }

  const handleSeek = (ratio: number) => {
    const targetDuration = isActive ? duration : durationSec
    if (!isActive) {
      play(message.id, fullUrl, durationSec)
    }
    seek(ratio * targetDuration)
  }

  const isOwn = message.sender_type !== 'customer'
  const playedColor = isOwn ? 'var(--tg-own-meta)' : 'var(--tg-primary)'
  const unplayedColor = isOwn ? 'rgba(79,174,78,0.3)' : 'rgba(51,144,236,0.3)'
  const iconColor = isOwn ? 'var(--tg-own-meta)' : 'var(--tg-primary)'

  return (
    <div className="tg-voice-player">
      <button
        onClick={handleToggle}
        aria-label={
          isThisPlaying ? uz.conversations.pauseVoice : uz.conversations.playVoice
        }
      >
        {isThisPlaying ? (
          <PauseCircle size={32} weight="thin" color={iconColor} />
        ) : (
          <PlayCircle size={32} weight="thin" color={iconColor} />
        )}
      </button>
      <div className="tg-waveform-container">
        <Waveform
          samples={samples}
          progress={progress}
          playedColor={playedColor}
          unplayedColor={unplayedColor}
          onSeek={handleSeek}
        />
      </div>
      <span className="tg-voice-duration">{displayTime}</span>
    </div>
  )
}
