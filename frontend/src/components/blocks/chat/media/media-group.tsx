import { useEffect, useMemo, useState } from 'react'
import { Skeleton } from '@/components/ui/skeleton'
import type { Message } from '@/lib/types'
import { getFullMediaUrl, getPreviewMediaUrl } from './urls'

function AlbumItem({ message, onPhotoClick }: { message: Message; onPhotoClick?: (id: number) => void }) {
  const [loaded, setLoaded] = useState(false)
  const [useFullFallback, setUseFullFallback] = useState(false)
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

  return (
    <div
      className="tg-album-item"
      onClick={() => onPhotoClick?.(message.id)}
      style={{ cursor: onPhotoClick ? 'pointer' : undefined }}
    >
      {!loaded && <Skeleton className="absolute inset-0 rounded-none" />}
      <img
        src={src}
        alt=""
        loading="lazy"
        className={loaded ? 'tg-media-loaded' : ''}
        style={{ width: '100%', height: '100%', objectFit: 'cover' }}
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

interface MediaGroupProps {
  messages: Message[]
  onPhotoClick?: (messageId: number) => void
}

export function MediaGroup({ messages, onPhotoClick }: MediaGroupProps) {
  const count = messages.length

  let layoutClass = 'tg-album-grid'
  if (count === 1) layoutClass += ' tg-album-1'
  else if (count === 2) layoutClass += ' tg-album-2'
  else if (count === 3) layoutClass += ' tg-album-3'
  else if (count === 4) layoutClass += ' tg-album-4'
  else layoutClass += ' tg-album-5plus'

  return (
    <div className={layoutClass} style={{ maxWidth: 320 }}>
      {messages.map((msg) => (
        <AlbumItem key={msg.id} message={msg} onPhotoClick={onPhotoClick} />
      ))}
    </div>
  )
}
