import { PauseCircle, PlayCircle, SkipBack, SkipForward } from '@phosphor-icons/react'

import { useAudioPlayer } from '@/hooks/use-audio-player'
import { uz } from '@/lib/uz'

function formatDuration(seconds: number): string {
  const safeSeconds = Number.isFinite(seconds) && seconds > 0 ? seconds : 0
  const minutes = Math.floor(safeSeconds / 60)
  const remainingSeconds = Math.floor(safeSeconds % 60)
  return `${minutes}:${String(remainingSeconds).padStart(2, '0')}`
}

const PLAYBACK_RATES = [1, 1.25, 1.5, 2] as const

function getNextPlaybackRate(currentRate: number): number {
  const currentIndex = PLAYBACK_RATES.findIndex((rate) => Math.abs(rate - currentRate) < 0.001)
  return PLAYBACK_RATES[(currentIndex + 1 + PLAYBACK_RATES.length) % PLAYBACK_RATES.length]
}

export function ChatPlaybackBar() {
  const {
    queue,
    activeMessageId,
    activeKind,
    activeLabel,
    isPlaying,
    currentTime,
    duration,
    playbackRate,
    pause,
    resume,
    seek,
    setPlaybackRate,
    playNext,
    playPrevious,
  } = useAudioPlayer()

  if (!activeMessageId || !activeKind) {
    return null
  }

  const currentIndex = queue.findIndex((item) => item.messageId === activeMessageId)
  const displayIndex = currentIndex >= 0 ? currentIndex + 1 : 1
  const canGoPrevious = currentIndex > 0
  const canGoNext = currentIndex >= 0 && currentIndex < queue.length - 1
  const progressMax = duration > 0 ? duration : 1

  return (
    <div className="tg-playback-bar" role="region" aria-label={uz.conversations.nowPlaying}>
      <div className="tg-playback-header">
        <div className="tg-playback-copy">
          <span className="tg-playback-kicker">{uz.conversations.nowPlaying}</span>
          <span className="tg-playback-title">{activeLabel}</span>
        </div>
        <div className="tg-playback-meta">
          <span className="tg-playback-counter">{displayIndex} / {queue.length}</span>
          <button
            type="button"
            className="tg-playback-rate"
            onClick={() => setPlaybackRate(getNextPlaybackRate(playbackRate))}
            aria-label={uz.conversations.playbackSpeed}
          >
            {playbackRate}x
          </button>
        </div>
      </div>

      <div className="tg-playback-controls">
        <button
          type="button"
          className="tg-playback-icon"
          onClick={playPrevious}
          disabled={!canGoPrevious}
          aria-label={uz.conversations.previousTrack}
        >
          <SkipBack size={20} weight="thin" />
        </button>
        <button
          type="button"
          className="tg-playback-main"
          onClick={() => (isPlaying ? pause() : resume())}
          aria-label={isPlaying ? uz.conversations.pauseVoice : uz.conversations.playVoice}
        >
          {isPlaying ? (
            <PauseCircle size={30} weight="thin" />
          ) : (
            <PlayCircle size={30} weight="thin" />
          )}
        </button>
        <button
          type="button"
          className="tg-playback-icon"
          onClick={playNext}
          disabled={!canGoNext}
          aria-label={uz.conversations.nextTrack}
        >
          <SkipForward size={20} weight="thin" />
        </button>

        <span className="tg-playback-time">{formatDuration(currentTime)}</span>
        <input
          type="range"
          min={0}
          max={progressMax}
          step={0.1}
          value={Math.min(currentTime, progressMax)}
          onChange={(event) => seek(Number(event.target.value))}
          aria-label={uz.conversations.mediaSeek}
          className="tg-playback-slider"
        />
        <span className="tg-playback-time">{formatDuration(duration)}</span>
      </div>
    </div>
  )
}
