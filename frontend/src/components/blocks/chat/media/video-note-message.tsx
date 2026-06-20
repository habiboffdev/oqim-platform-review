import { useEffect, useRef, useState } from 'react'
import { PlayCircle, PauseCircle } from '@phosphor-icons/react'
import { Skeleton } from '@/components/ui/skeleton'
import type { Message } from '@/lib/types'
import { uz } from '@/lib/uz'
import { useAudioPlayer } from '@/hooks/use-audio-player'
import { getFullMediaUrl, getPreviewMediaUrl } from './urls'

interface VideoNoteMeta {
  duration?: number
  length?: number
}

export function VideoNoteMessage({ message }: { message: Message }) {
  const [videoReady, setVideoReady] = useState(false)
  const [posterLoaded, setPosterLoaded] = useState(false)
  const [loadRequested, setLoadRequested] = useState(false)
  const [loadPercent, setLoadPercent] = useState(0)
  const fullUrl = getFullMediaUrl(message)
  const previewUrl = getPreviewMediaUrl(message)
  const [posterSrc, setPosterSrc] = useState(() => previewUrl ?? fullUrl ?? '')
  const videoRef = useRef<HTMLVideoElement>(null)
  const meta = message.media_metadata as VideoNoteMeta | undefined
  const size = Math.min(meta?.length ?? 240, 240)
  const { activeMessageId, isPlaying, playElement, registerElement, unregisterElement } = useAudioPlayer()
  const isThisPlaying = activeMessageId === message.id && isPlaying

  useEffect(() => {
    setVideoReady(false)
    setPosterLoaded(false)
    setLoadRequested(false)
    setLoadPercent(0)
    setPosterSrc(previewUrl ?? fullUrl ?? '')
    videoRef.current?.pause()
  }, [message.id, previewUrl, fullUrl])

  useEffect(() => {
    const video = videoRef.current
    if (!video || !fullUrl) return undefined

    registerElement(message.id, video)
    return () => unregisterElement(message.id)
  }, [fullUrl, message.id, registerElement, unregisterElement])

  const updateLoadPercent = () => {
    const video = videoRef.current
    if (!video || !Number.isFinite(video.duration) || video.duration <= 0 || video.buffered.length === 0) {
      return
    }
    const bufferedEnd = video.buffered.end(video.buffered.length - 1)
    const percent = Math.max(0, Math.min(100, Math.round((bufferedEnd / video.duration) * 100)))
    setLoadPercent(percent)
  }

  const handleToggle = () => {
    const video = videoRef.current
    if (!video || !fullUrl) return
    setLoadRequested(true)
    playElement(
      {
        messageId: message.id,
        url: fullUrl,
        duration: meta?.duration ?? 0,
        kind: 'video_note',
        label: uz.conversations.videoNote,
      },
      video,
    )
  }

  return (
    <div
      onClick={handleToggle}
      role="button"
      tabIndex={0}
      aria-label="Play video note"
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          handleToggle()
        }
      }}
      style={{ width: size, height: size, borderRadius: '50%', overflow: 'hidden', position: 'relative' }}
    >
      {!posterLoaded && !videoReady && <Skeleton className="absolute inset-0 rounded-full" />}
      {posterSrc && !videoReady && (
        <img
          src={posterSrc}
          alt=""
          className="absolute inset-0"
          style={{ width: '100%', height: '100%', objectFit: 'cover', zIndex: 1 }}
          onLoad={() => setPosterLoaded(true)}
          onError={() => {
            if (fullUrl && posterSrc !== fullUrl) {
              setPosterSrc(fullUrl)
              return
            }
            setPosterLoaded(false)
          }}
        />
      )}
      <video
        ref={videoRef}
        src={fullUrl ?? ''}
        poster={posterSrc}
        loop
        playsInline
        preload="metadata"
        onLoadedMetadata={updateLoadPercent}
        onProgress={updateLoadPercent}
        onCanPlay={() => {
          updateLoadPercent()
          setLoadPercent(100)
          setVideoReady(true)
        }}
        onPlaying={() => setLoadRequested(false)}
        onError={() => {
          setLoadRequested(false)
          if (fullUrl && posterSrc !== fullUrl) {
            setPosterSrc(fullUrl)
          }
        }}
        style={{
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          position: 'relative',
          zIndex: 0,
          opacity: videoReady ? 1 : 0,
        }}
      />
      <div
        className="tg-video-play-overlay"
        style={{ borderRadius: '50%', cursor: 'pointer', zIndex: 2 }}
      >
        {loadRequested && !videoReady ? (
          <span
            style={{
              color: 'rgba(255,255,255,0.95)',
              fontSize: '0.875rem',
              fontWeight: 600,
              textShadow: '0 1px 2px rgba(0,0,0,0.45)',
            }}
          >
            {Math.max(loadPercent, 0)}%
          </span>
        ) : isThisPlaying ? (
          <PauseCircle size={48} weight="thin" color="rgba(255,255,255,0.9)" />
        ) : (
          <PlayCircle size={48} weight="thin" color="rgba(255,255,255,0.9)" />
        )}
      </div>
    </div>
  )
}
