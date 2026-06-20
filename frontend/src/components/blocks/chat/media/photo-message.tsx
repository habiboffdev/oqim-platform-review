import { useEffect, useMemo, useState } from 'react'
import { cn } from '@/lib/utils'
import { Skeleton } from '@/components/ui/skeleton'
import type { Message } from '@/lib/types'
import { getFullMediaUrl, getPreviewMediaUrl } from './urls'

interface PhotoMeta {
  width?: number
  height?: number
}

interface PhotoMessageProps {
  message: Message
  onPhotoClick?: (messageId: number) => void
}

export function PhotoMessage({ message, onPhotoClick }: PhotoMessageProps) {
  const [loaded, setLoaded] = useState(false)
  const [useFullFallback, setUseFullFallback] = useState(false)
  const meta = message.media_metadata as PhotoMeta | undefined
  const hasSize = meta?.width && meta?.height
  const fullUrl = getFullMediaUrl(message)
  const previewUrl = getPreviewMediaUrl(message) ?? ''
  const src = useMemo(() => {
    if (useFullFallback && fullUrl) {
      return fullUrl
    }
    return previewUrl
  }, [fullUrl, previewUrl, useFullFallback])

  useEffect(() => {
    setLoaded(false)
    setUseFullFallback(false)
  }, [message.id, previewUrl, fullUrl])

  const containerStyle: React.CSSProperties = hasSize
    ? { aspectRatio: `${meta!.width} / ${meta!.height}`, maxWidth: Math.min(meta!.width!, 320) }
    : { minHeight: 200, maxWidth: 320 }

  return (
    <div
      className="tg-media"
      style={{ ...containerStyle, cursor: onPhotoClick ? 'pointer' : undefined }}
      onClick={() => onPhotoClick?.(message.id)}
    >
      {!loaded && <Skeleton className="absolute inset-0 rounded-none" />}
      <img
        src={src}
        alt=""
        loading="lazy"
        className={cn('tg-media-img', loaded && 'tg-media-loaded')}
        onLoad={() => setLoaded(true)}
        onError={() => {
          if (fullUrl && !useFullFallback && previewUrl && fullUrl !== previewUrl) {
            setUseFullFallback(true)
            return
          }
          setLoaded(true)
        }}
      />
    </div>
  )
}
