import { PauseCircle, PlayCircle } from '@phosphor-icons/react'

import type { Message } from '@/lib/types'
import { uz } from '@/lib/uz'
import { useAudioPlayer } from '@/hooks/use-audio-player'
import { getFullMediaUrl } from './urls'

interface AudioMeta {
  duration?: number
  file_name?: string
}

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

export function AudioMessage({ message }: { message: Message }) {
  const meta = message.media_metadata as AudioMeta | undefined
  const durationSec = meta?.duration ?? 0
  const fullUrl = getFullMediaUrl(message) ?? ''
  const title = meta?.file_name || uz.conversations.audioMessage

  const { activeMessageId, isPlaying, currentTime, duration, play, pause } = useAudioPlayer()
  const isActive = activeMessageId === message.id
  const isThisPlaying = isActive && isPlaying
  const progress = isActive && duration > 0 ? currentTime / duration : 0

  const displaySeconds = isActive ? currentTime : durationSec
  const displayTime = formatDuration(displaySeconds)

  const handleToggle = () => {
    if (!fullUrl) return
    if (isThisPlaying) {
      pause()
      return
    }
    play(message.id, fullUrl, durationSec, 'audio', title)
  }

  const isOwn = message.sender_type !== 'customer'
  const playedColor = isOwn ? 'var(--tg-own-meta)' : 'var(--tg-primary)'
  const unplayedColor = isOwn ? 'rgba(79,174,78,0.2)' : 'rgba(51,144,236,0.2)'
  const iconColor = isOwn ? 'var(--tg-own-meta)' : 'var(--tg-primary)'

  return (
    <div className="tg-audio-player">
      <button
        onClick={handleToggle}
        aria-label={isThisPlaying ? uz.conversations.pauseVoice : uz.conversations.playVoice}
      >
        {isThisPlaying ? (
          <PauseCircle size={32} weight="thin" color={iconColor} />
        ) : (
          <PlayCircle size={32} weight="thin" color={iconColor} />
        )}
      </button>
      <div className="tg-audio-body">
        <span className="tg-audio-title">{title}</span>
        <div className="tg-audio-progress" aria-hidden="true">
          <div
            className="tg-audio-progress-fill"
            style={{
              width: `${Math.max(0, Math.min(100, progress * 100))}%`,
              background: playedColor,
              boxShadow: `0 0 0 1px ${unplayedColor} inset`,
            }}
          />
        </div>
      </div>
      <span className="tg-voice-duration">{displayTime}</span>
    </div>
  )
}
