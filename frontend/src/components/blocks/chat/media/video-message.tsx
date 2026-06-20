import { useState } from 'react'
import { PlayCircle } from '@phosphor-icons/react'
import { uz } from '@/lib/uz'
import { cn } from '@/lib/utils'
import { Skeleton } from '@/components/ui/skeleton'
import type { Message } from '@/lib/types'
import { getFullMediaUrl, getPreviewMediaUrl } from './urls'

interface VideoMeta {
  width?: number
  height?: number
  duration?: number
}

interface VideoMessageProps {
  message: Message
}

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

export function VideoMessage({ message }: VideoMessageProps) {
  const [loaded, setLoaded] = useState(false)
  const [posterSrc, setPosterSrc] = useState(() => getPreviewMediaUrl(message) ?? '')
  const meta = message.media_metadata as VideoMeta | undefined
  const hasSize = meta?.width && meta?.height
  const fullUrl = getFullMediaUrl(message)

  const containerStyle: React.CSSProperties = hasSize
    ? { aspectRatio: `${meta!.width} / ${meta!.height}`, maxWidth: Math.min(meta!.width!, 320) }
    : { minHeight: 200, maxWidth: 320 }

  return (
    <div className="tg-media" style={containerStyle}>
      <div
        className="tg-video-container"
        onClick={() => fullUrl && window.open(fullUrl, '_blank')}
        role="button"
        aria-label={uz.conversations.videoMessage}
      >
        {!loaded && <Skeleton className="absolute inset-0 rounded-none" />}
        <img
          src={posterSrc}
          alt=""
          loading="lazy"
          className={cn('tg-media-img', loaded && 'tg-media-loaded')}
          onLoad={() => setLoaded(true)}
          onError={() => {
            if (fullUrl && posterSrc !== fullUrl) {
              setPosterSrc(fullUrl)
              return
            }
            setLoaded(true)
          }}
        />
        <div className="tg-video-play-overlay">
          <PlayCircle size={48} weight="thin" color="rgba(255,255,255,0.9)" />
        </div>
        {meta?.duration != null && (
          <span className="tg-duration-badge">{formatDuration(meta.duration)}</span>
        )}
      </div>
    </div>
  )
}
