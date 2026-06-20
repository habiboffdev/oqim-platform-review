import { uz } from '@/lib/uz'
import type { Message } from '@/lib/types'

interface ReplyPreviewProps {
  replyToMsgId: number
  messageMap: Map<number, { message: Message; chatItemIndex: number }>
  onScrollToMessage?: (telegramMsgId: number) => void
  isOwn: boolean
}

function getMediaTypeLabel(mediaType: string): string {
  // Internal (normalized) media types
  const labels: Record<string, string> = {
    photo: uz.conversations.photo,
    voice: uz.conversations.voice,
    video: uz.conversations.videoMessage,
    sticker: uz.conversations.stickerMessage,
    document: uz.conversations.document,
    gif: uz.conversations.gifMessage,
    contact: uz.conversations.contactMessage,
    location: uz.conversations.locationMessage,
    audio: uz.conversations.audioMessage,
  }
  // Check internal labels first, then raw Telegram class names (from GramJS className)
  return labels[mediaType]
    ?? uz.conversations.mediaLabels[mediaType]
    ?? uz.conversations.mediaFallback
}

export function ReplyPreview({
  replyToMsgId,
  messageMap,
  onScrollToMessage,
}: ReplyPreviewProps) {
  const original = messageMap.get(replyToMsgId)

  let senderName: string | null = null
  let previewText: string | null = null

  if (original) {
    const msg = original.message
    senderName =
      msg.sender_type === 'customer'
        ? uz.customer.types.customer
        : uz.conversations.you

    if (msg.content && msg.content.length > 0) {
      previewText =
        msg.content.length > 80
          ? msg.content.slice(0, 80) + '...'
          : msg.content
    } else if (msg.media_type) {
      previewText = getMediaTypeLabel(msg.media_type)
    }
  } else {
    previewText = uz.conversations.replyTo
  }

  return (
    <div
      className="tg-reply-bar"
      onClick={(e) => {
        e.stopPropagation()
        onScrollToMessage?.(replyToMsgId)
      }}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          e.stopPropagation()
          onScrollToMessage?.(replyToMsgId)
        }
      }}
      aria-label={uz.conversations.replyTo}
    >
      {senderName && <div className="tg-reply-sender">{senderName}</div>}
      {previewText && <div className="tg-reply-text">{previewText}</div>}
    </div>
  )
}
