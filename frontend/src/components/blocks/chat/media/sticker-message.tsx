import { useEffect, useState } from 'react'
import { uz } from '@/lib/uz'
import type { Message } from '@/lib/types'
import { getFullMediaUrl, getPreviewMediaUrl } from './urls'

interface StickerMeta {
  emoji?: string
  is_animated?: boolean
  is_video?: boolean
}

interface StickerMessageProps {
  message: Message
}

export function StickerMessage({ message }: StickerMessageProps) {
  const [error, setError] = useState(false)
  const meta = message.media_metadata as StickerMeta | undefined

  // TGS (animated) and WEBM (video) stickers can't render as <img>.
  // Static WebP stickers have is_animated=false explicitly. If metadata is missing, use thumbnail to be safe.
  const isStatic = meta?.is_animated === false && meta?.is_video === false
  const fullUrl = getFullMediaUrl(message)
  const previewUrl = getPreviewMediaUrl(message)
  const stickerUrl = isStatic ? fullUrl : previewUrl

  useEffect(() => {
    setError(false)
  }, [message.id, fullUrl, previewUrl, isStatic])

  if (!stickerUrl || error) {
    if (meta?.emoji) {
      return <div className="tg-sticker-emoji">{meta.emoji}</div>
    }
    return (
      <div className="tg-sticker-emoji">
        <span style={{ fontSize: '0.875rem', color: 'var(--tg-incoming-meta)' }}>
          {uz.conversations.stickerMessage}
        </span>
      </div>
    )
  }

  return (
    <img
      src={stickerUrl}
      alt={meta?.emoji || uz.conversations.stickerMessage}
      className="tg-sticker"
      loading="lazy"
      onError={() => setError(true)}
    />
  )
}
