import { useRef, useEffect, useState } from 'react'
import { cn } from '@/lib/utils'
import { Skeleton } from '@/components/ui/skeleton'
import type { Message } from '@/lib/types'
import { getFullMediaUrl } from './urls'

interface GifMeta {
  width?: number
  height?: number
}

interface GifMessageProps {
  message: Message
}

export function GifMessage({ message }: GifMessageProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [loaded, setLoaded] = useState(false)
  const meta = message.media_metadata as GifMeta | undefined
  const hasSize = meta?.width && meta?.height
  const fullUrl = getFullMediaUrl(message)

  const containerStyle: React.CSSProperties = hasSize
    ? { aspectRatio: `${meta!.width} / ${meta!.height}`, maxWidth: Math.min(meta!.width!, 320) }
    : { minHeight: 200, maxWidth: 320 }

  useEffect(() => {
    setLoaded(false)
    const video = videoRef.current
    if (!video || !fullUrl) return
    video.pause()
    video.currentTime = 0
    video.load()
  }, [message.id, fullUrl])

  // IntersectionObserver for visibility-gated autoplay (DOM side-effect, not data fetching)
  useEffect(() => {
    const video = videoRef.current
    if (!video) return undefined

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          video.play().catch(() => {})
        } else {
          video.pause()
        }
      },
      { threshold: 0.5 },
    )
    observer.observe(video)
    return () => observer.disconnect()
  }, [message.id, fullUrl])

  return (
    <div className="tg-media" style={containerStyle}>
      {!loaded && <Skeleton className="absolute inset-0 rounded-none" />}
      <video
        ref={videoRef}
        src={fullUrl ?? ''}
        autoPlay
        loop
        muted
        playsInline
        className={cn('tg-media-img', loaded && 'tg-media-loaded')}
        onLoadedData={() => setLoaded(true)}
      />
      <span className="tg-gif-badge">GIF</span>
    </div>
  )
}
