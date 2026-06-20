import { Check, Checks, Clock, WarningCircle } from '@phosphor-icons/react'
import { uz } from '@/lib/uz'
import { cn } from '@/lib/utils'
import { getRenderableMediaType, isMediaPlaceholder } from '@/lib/media-ui-state'
import type { Message } from '@/lib/types'
import { LinkedText } from './linked-text'
import { MediaContent } from './media/media-content'
import { ReplyPreview } from './reply-preview'
import { ForwardHeader } from './forward-header'

interface MessageBubbleProps {
  message: Message
  position: 'first' | 'middle' | 'last' | 'single'
  messageMap: Map<number, { message: Message; chatItemIndex: number }>
  onScrollToMessage?: (telegramMsgId: number) => void
  isHighlighted?: boolean
  onPhotoClick?: (messageId: number) => void
}

function formatTime(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function ReadStatus({ message }: { message: Message }) {
  if (message.sender_type === 'customer') return null
  const customerStatus = message.delivery_runtime?.customer_status
  if (message.delivery_state === 'failed' || customerStatus === 'failed') {
    return (
      <span className="tg-delivery-state tg-delivery-state-failed">
        <span className="tg-delivery-text">{uz.conversations.failed}</span>
        <WarningCircle
          size={16}
          weight="thin"
          className="tg-failed-tick"
          role="img"
          aria-label={uz.conversations.failed}
        />
      </span>
    )
  }
  if (message.delivery_state === 'unknown' || customerStatus === 'uncertain') {
    return (
      <span className="tg-delivery-state">
        <span className="tg-delivery-text">{uz.conversations.deliveryUncertain}</span>
        <Clock
          size={16}
          weight="thin"
          className="tg-unknown-tick"
          role="img"
          aria-label={uz.conversations.deliveryUncertain}
        />
      </span>
    )
  }
  if (message.delivery_state === 'pending' || customerStatus === 'sending') {
    return (
      <span className="tg-delivery-state">
        <span className="tg-delivery-text">{uz.conversations.sending}</span>
        <Clock
          size={16}
          weight="thin"
          className="tg-pending-tick"
          role="img"
          aria-label={uz.conversations.sending}
        />
      </span>
    )
  }
  if (message.is_read) {
    return <Checks size={16} weight="thin" className="tg-read-tick" role="img" aria-label={uz.conversations.read} />
  }
  if (message.delivery_state === 'confirmed' || customerStatus === 'sent') {
    return <Check size={16} weight="thin" className="tg-sent-tick" role="img" aria-label={uz.conversations.sent} />
  }
  return <Check size={16} weight="thin" className="tg-sent-tick" role="img" aria-label={uz.conversations.sent} />
}

export function MessageBubble({ message, position, messageMap, onScrollToMessage, isHighlighted, onPhotoClick }: MessageBubbleProps) {
  const isOwn = message.sender_type !== 'customer'
  const showTail = position === 'last' || position === 'single'
  const effectiveMediaType = getRenderableMediaType(message)

  const radiusClass = cn(
    'tg-bubble',
    isOwn ? 'tg-bubble-own' : 'tg-bubble-incoming',
    position === 'first' && 'tg-bubble-first',
    position === 'middle' && 'tg-bubble-middle',
    position === 'last' && 'tg-bubble-last',
    position === 'single' && 'tg-bubble-single',
    (effectiveMediaType === 'sticker' || effectiveMediaType === 'video_note') && 'tg-bubble-borderless',
    isHighlighted && 'tg-bubble-highlight',
  )

  return (
    <div className={cn('tg-message', isOwn && 'tg-message-own')}>
      <div className={radiusClass}>
        {message.forward_from_name && (
          <ForwardHeader fromName={message.forward_from_name} />
        )}

        {message.reply_to_msg_id && (
          <ReplyPreview
            replyToMsgId={message.reply_to_msg_id}
            messageMap={messageMap}
            onScrollToMessage={onScrollToMessage}
            isOwn={isOwn}
          />
        )}

        {message.media_type && <MediaContent message={message} onPhotoClick={onPhotoClick} />}

        {message.content && !isMediaPlaceholder(message) && (
          <span className="tg-text"><LinkedText text={message.content} textEntities={message.text_entities} /></span>
        )}

        <span className="tg-meta">
          {message.edited_at && (
            <span className="tg-edited">{uz.conversations.edited}</span>
          )}
          <span className="tg-time">{formatTime(message.telegram_timestamp ?? message.created_at)}</span>
          <ReadStatus message={message} />
        </span>
      </div>

      {showTail && (
        <svg className={cn('tg-tail', isOwn ? 'tg-tail-own' : 'tg-tail-incoming')} width="9" height="18" viewBox="0 0 9 18">
          {isOwn ? (
            <path d="M0 0 C0 8, 9 12, 9 18 L0 18 Z" fill="var(--tg-own-bg)" />
          ) : (
            <path d="M9 0 C9 8, 0 12, 0 18 L9 18 Z" fill="var(--tg-incoming-bg)" />
          )}
        </svg>
      )}
    </div>
  )
}
