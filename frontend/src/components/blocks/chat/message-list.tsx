import { type RefObject, useCallback, useEffect, useRef } from 'react'
import { Virtuoso, type VirtuosoHandle } from 'react-virtuoso'
import { Spinner } from '@/components/ui/spinner'
import { uz } from '@/lib/uz'
import { cn } from '@/lib/utils'
import { MessageBubble } from './message-bubble'
import { DateSeparator, formatDateSeparator } from './date-separator'
import { UnreadDivider } from './unread-divider'
import { MediaGroup } from './media/media-group'
import { getRenderableMediaType } from '@/lib/media-ui-state'
import type { Message } from '@/lib/types'

// --- Types ---

export type ChatItem =
  | { type: 'message'; message: Message; position: 'first' | 'middle' | 'last' | 'single' }
  | { type: 'date-separator'; date: string; label: string }
  | { type: 'unread-divider'; count: number }
  | { type: 'media-group'; messages: Message[]; isOwn: boolean }

interface VirtuosoContext {
  scrollToMessage: (telegramMsgId: number) => void
  messageMap: Map<number, { message: Message; chatItemIndex: number }>
  highlightedMessageId: number | null
  onPhotoClick: (messageId: number) => void
}

interface MessageListProps {
  chatItems: ChatItem[]
  hasOlder: boolean
  isLoadingOlder: boolean
  onLoadOlder: () => void
  onAtBottomChange: (atBottom: boolean) => void
  virtuosoRef: RefObject<VirtuosoHandle | null>
  scrollerElementRef: RefObject<HTMLElement | null>
  scrollToMessage: (telegramMsgId: number) => void
  messageMap: Map<number, { message: Message; chatItemIndex: number }>
  highlightedMessageId: number | null
  onPhotoClick: (messageId: number) => void
  tailVersion: string
}

// --- Helpers ---

/** Use telegram_timestamp (real send time) when available, fall back to created_at (import time) */
function msgTime(msg: Message): string {
  return msg.telegram_timestamp ?? msg.created_at
}

// --- Constants ---

export const START_INDEX = 1_000_000

// --- Build flat chat items with date separators (O(n) single pass) ---

export function buildChatItems(
  messages: Message[],
  firstUnreadMessageId?: number,
  unreadCount?: number,
): ChatItem[] {
  const items: ChatItem[] = []
  let lastDateStr = ''
  let unreadDividerInserted = false

  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i]
    const dateStr = new Date(msgTime(msg)).toDateString()

    if (dateStr !== lastDateStr) {
      items.push({
        type: 'date-separator',
        date: msgTime(msg),
        label: formatDateSeparator(msgTime(msg)),
      })
      lastDateStr = dateStr
    }

    // Unread divider -- insert once before the first unread customer message
    if (
      !unreadDividerInserted
      && firstUnreadMessageId
      && unreadCount
      && unreadCount > 0
      && msg.id === firstUnreadMessageId
    ) {
      items.push({ type: 'unread-divider', count: unreadCount })
      unreadDividerInserted = true
    }

    const prevSameSender = i > 0
      && messages[i - 1].sender_type === msg.sender_type
      && new Date(msgTime(messages[i - 1])).toDateString() === dateStr
    const nextSameSender = i < messages.length - 1
      && messages[i + 1].sender_type === msg.sender_type
      && new Date(msgTime(messages[i + 1])).toDateString() === new Date(msgTime(msg)).toDateString()

    let position: 'first' | 'middle' | 'last' | 'single'
    if (!prevSameSender && !nextSameSender) position = 'single'
    else if (!prevSameSender) position = 'first'
    else if (!nextSameSender) position = 'last'
    else position = 'middle'

    items.push({ type: 'message', message: msg, position })
  }

  // Post-process: merge consecutive messages with same grouped_id into media-group items
  const result: ChatItem[] = []
  let i = 0
  while (i < items.length) {
    const item = items[i]
    if (
      item.type === 'message'
      && item.message.grouped_id
      && getRenderableMediaType(item.message)
    ) {
      const groupId = item.message.grouped_id
      const groupMsgs: Message[] = [item.message]
      let j = i + 1
      while (j < items.length) {
        const next = items[j]
        if (
          next.type === 'message'
          && next.message.grouped_id === groupId
          && getRenderableMediaType(next.message)
        ) {
          groupMsgs.push(next.message)
          j++
        } else {
          break
        }
      }
      if (groupMsgs.length > 1) {
        result.push({
          type: 'media-group',
          messages: groupMsgs,
          isOwn: groupMsgs[0].sender_type !== 'customer',
        })
        i = j
      } else {
        result.push(item)
        i++
      }
    } else {
      result.push(item)
      i++
    }
  }

  return result
}

export function computeChatItemKey(item: ChatItem): string {
  switch (item.type) {
    case 'message':
      return `message:${item.message.id}`
    case 'date-separator':
      return `date:${item.date}:${item.label}`
    case 'unread-divider':
      return `unread:${item.count}`
    case 'media-group':
      return `media-group:${item.messages.map((message) => message.id).join('-')}`
    default:
      return 'chat-item'
  }
}

// --- Item renderer (defined at module scope to prevent re-creation) ---

const ItemContent = (_index: number, item: ChatItem, context: VirtuosoContext) => {
  if (item.type === 'date-separator') {
    return (
      <div style={{ padding: '4px 0' }}>
        <DateSeparator label={item.label} />
      </div>
    )
  }

  if (item.type === 'unread-divider') {
    return (
      <div style={{ padding: '4px 0' }}>
        <UnreadDivider count={item.count} />
      </div>
    )
  }

  if (item.type === 'media-group') {
    return (
      <div
        className={cn('tg-message', item.isOwn && 'tg-message-own')}
        style={{ paddingTop: '6px', paddingBottom: '2px' }}
      >
        <div className={cn('tg-bubble', item.isOwn ? 'tg-bubble-own' : 'tg-bubble-incoming', 'tg-bubble-single')}>
          <MediaGroup messages={item.messages} onPhotoClick={context.onPhotoClick} />
          <span className="tg-meta">
            <span className="tg-time">
              {new Date(msgTime(item.messages[item.messages.length - 1]))
                .toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </span>
          </span>
        </div>
      </div>
    )
  }

  const isGroupStart = item.position === 'first' || item.position === 'single'

  return (
    <div style={{ paddingTop: isGroupStart ? '6px' : '0', paddingBottom: '2px' }}>
      <MessageBubble
        message={item.message}
        position={item.position}
        messageMap={context.messageMap}
        onScrollToMessage={context.scrollToMessage}
        isHighlighted={item.message.id === context.highlightedMessageId}
        onPhotoClick={context.onPhotoClick}
      />
    </div>
  )
}

// --- Header component (loading spinner during pagination) ---

function ListHeader({ isLoading, hasOlder }: { isLoading: boolean; hasOlder: boolean }) {
  if (isLoading) {
    return (
      <div className="tg-load-more">
        <Spinner />
      </div>
    )
  }
  if (!hasOlder) {
    return (
      <div className="tg-all-loaded">
        <span>{uz.conversations.allMessagesLoaded}</span>
      </div>
    )
  }
  return null
}

// --- Main component ---

export function MessageList({
  chatItems,
  hasOlder,
  isLoadingOlder,
  onLoadOlder,
  onAtBottomChange,
  virtuosoRef,
  scrollerElementRef,
  scrollToMessage,
  messageMap,
  highlightedMessageId,
  onPhotoClick,
  tailVersion,
}: MessageListProps) {
  const firstItemIndex = START_INDEX - chatItems.length
  const olderPaginationArmedRef = useRef(false)

  const maybeLoadOlder = useCallback(() => {
    if (olderPaginationArmedRef.current && hasOlder && !isLoadingOlder) onLoadOlder()
  }, [hasOlder, isLoadingOlder, onLoadOlder])

  const handleAtBottomChange = useCallback((atBottom: boolean) => {
    if (atBottom) olderPaginationArmedRef.current = true
    onAtBottomChange(atBottom)
  }, [onAtBottomChange])

  const forceScrollToTail = useCallback(() => {
    virtuosoRef.current?.scrollToIndex({ index: 'LAST', align: 'end', behavior: 'auto' })
    const scroller = scrollerElementRef.current
      ?? document.querySelector<HTMLElement>('[data-virtuoso-scroller]')
    if (scroller) {
      scroller.scrollTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight)
    }
  }, [scrollerElementRef, virtuosoRef])

  useEffect(() => {
    let observer: ResizeObserver | null = null
    const attachObserver = () => {
      const scroller = scrollerElementRef.current
        ?? document.querySelector<HTMLElement>('[data-virtuoso-scroller]')
      const observed = scroller?.firstElementChild instanceof HTMLElement
        ? scroller.firstElementChild
        : scroller
      if (!observed || !('ResizeObserver' in window)) return

      observer = new ResizeObserver(() => {
        forceScrollToTail()
      })
      observer.observe(observed)
    }

    const timers = [80, 360, 1_200, 2_400, 4_800].map((delay) =>
      window.setTimeout(forceScrollToTail, delay),
    )
    const observerTimer = window.setTimeout(attachObserver, 0)
    const stopObserverTimer = window.setTimeout(() => observer?.disconnect(), 5_000)

    return () => {
      timers.forEach(window.clearTimeout)
      window.clearTimeout(observerTimer)
      window.clearTimeout(stopObserverTimer)
      observer?.disconnect()
    }
  }, [forceScrollToTail, scrollerElementRef, tailVersion])

  return (
    <Virtuoso
      ref={virtuosoRef}
      scrollerRef={(element) => {
        scrollerElementRef.current = element instanceof HTMLElement ? element : null
      }}
      style={{ height: '100%' }}
      context={{ scrollToMessage, messageMap, highlightedMessageId, onPhotoClick }}
      data={chatItems}
      computeItemKey={(_index, item) => computeChatItemKey(item)}
      firstItemIndex={firstItemIndex}
      initialTopMostItemIndex={{ index: 'LAST', align: 'end' }}
      alignToBottom
      startReached={() => {
        maybeLoadOlder()
      }}
      atTopStateChange={(atTop) => {
        // Backup trigger for startReached-fires-once bug (react-virtuoso #1177)
        if (atTop) maybeLoadOlder()
      }}
      followOutput={(isAtBottom) => (isAtBottom ? 'smooth' : false)}
      atBottomStateChange={handleAtBottomChange}
      atBottomThreshold={50}
      skipAnimationFrameInResizeObserver
      increaseViewportBy={{ top: 400, bottom: 0 }}
      itemContent={ItemContent}
      components={{
        Header: () => <ListHeader isLoading={isLoadingOlder} hasOlder={hasOlder} />,
      }}
    />
  )
}
