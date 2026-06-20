import './chat-viewer.css'
// eslint-disable-next-line no-restricted-imports
import { useMemo, useState, useRef, useCallback, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { VirtuosoHandle } from 'react-virtuoso'
import { toast } from 'sonner'
import { useInfiniteMessages } from '@/hooks/use-infinite-messages'
import { useMessageLookup } from '@/hooks/use-message-lookup'
import { useHydrateConversation } from '@/hooks/use-conversations'
import { uz } from '@/lib/uz'
import { cn } from '@/lib/utils'
import { Skeleton } from '@/components/ui/skeleton'
import { Button } from '@/components/ui/button'
import { MessageList, buildChatItems, START_INDEX } from './message-list'
import { PhotoLightbox } from './photo-lightbox'
import { ScrollToBottomFab } from './scroll-to-bottom-fab'
import { TypingIndicator } from './typing-indicator'
import { ChatPlaybackBar } from './chat-playback-bar'
import { getFullMediaUrl } from './media/urls'
import { getRenderableMediaType } from '@/lib/media-ui-state'
import { useAudioPlayer, type PlaybackItem } from '@/hooks/use-audio-player'

interface ChatViewerProps {
  conversationId: number | undefined
}

export function ChatViewer({ conversationId }: ChatViewerProps) {
  const {
    mutate: hydrateConversation,
    isPending: isHydrationRequestPending,
  } = useHydrateConversation()
  const {
    data,
    dataUpdatedAt,
    isLoading,
    isError,
    refetch,
    hasPreviousPage,
    isFetchingPreviousPage,
    fetchPreviousPage,
  } = useInfiniteMessages(conversationId, {
    enabled: !!conversationId,
  })

  const [atBottom, setAtBottom] = useState(true)
  const virtuosoRef = useRef<VirtuosoHandle>(null)
  const scrollerElementRef = useRef<HTMLElement | null>(null)

  const handleAtBottomChange = useCallback((bottom: boolean) => {
    setAtBottom(bottom)
  }, [])

  // --- FAB visibility with 500ms delay ---
  const [showFab, setShowFab] = useState(false)
  const showFabTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // NOTE: This useEffect is acceptable -- synchronizes with a timeout side effect, not deriving data
  // eslint-disable-next-line no-restricted-imports
  useEffect(() => {
    if (showFabTimerRef.current) clearTimeout(showFabTimerRef.current)
    if (!atBottom) {
      showFabTimerRef.current = setTimeout(() => setShowFab(true), 500)
    } else {
      setShowFab(false)
    }
    return () => {
      if (showFabTimerRef.current) clearTimeout(showFabTimerRef.current)
    }
  }, [atBottom])

  const scrollToTail = useCallback((behavior: ScrollBehavior = 'auto') => {
    const virtuosoBehavior: 'auto' | 'smooth' = behavior === 'smooth' ? 'smooth' : 'auto'
    virtuosoRef.current?.scrollToIndex({ index: 'LAST', align: 'end', behavior: virtuosoBehavior })
    const scroller = scrollerElementRef.current
    if (scroller) {
      scroller.scrollTo({ top: scroller.scrollHeight, behavior })
    }
  }, [])

  const scrollToBottom = useCallback(() => {
    scrollToTail('smooth')
  }, [scrollToTail])

  const messages = useMemo(
    () => data?.pages.flatMap((p) => p.items) ?? [],
    [data?.pages],
  )
  const latestMessageId = messages.at(-1)?.id ?? null
  const historyGap = data?.pages[0]?.history_gap ?? null
  const hydration = data?.pages[0]?.hydration ?? null
  const hydrationState = hydration?.state
  const isHydrationPending = hydrationState === 'queued'
    || hydrationState === 'running'
    || hydrationState === 'deferred'
  const hydrationHasPersistedMessages = Number(hydration?.persisted_count ?? 0) > 0

  // --- Photo lightbox slides + index map (single useMemo to prevent mismatch) ---
  const { photoSlides, photoIndexMap } = useMemo(() => {
    const slides: { src: string }[] = []
    const indexMap = new Map<number, number>()
    let idx = 0
    for (const m of messages) {
      const fullUrl = getFullMediaUrl(m)
      if (getRenderableMediaType(m) === 'photo' && fullUrl) {
        slides.push({ src: fullUrl })
        indexMap.set(m.id, idx++)
      }
    }
    return { photoSlides: slides, photoIndexMap: indexMap }
  }, [messages])

  const openLightbox = useCallback((messageId: number) => {
    const idx = photoIndexMap.get(messageId)
    if (idx !== undefined) {
      setLightboxIndex(idx)
      setLightboxOpen(true)
    }
  }, [photoIndexMap])

  // --- Highlight state for scroll-to-original ---
  const [highlightedMessageId, setHighlightedMessageId] = useState<number | null>(null)
  const highlightTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // --- Lightbox state ---
  const [lightboxOpen, setLightboxOpen] = useState(false)
  const [lightboxIndex, setLightboxIndex] = useState(0)
  const setPlaybackQueue = useAudioPlayer((state) => state.setQueue)

  // --- Message lookup map for reply-to-original scroll ---
  const chatItems = useMemo(
    () => buildChatItems(messages),
    [messages],
  )
  const messageListKey = `${conversationId ?? 'none'}:${latestMessageId ?? 'empty'}`
  const messageMap = useMessageLookup(chatItems)
  const firstItemIndex = START_INDEX - chatItems.length

  const playbackQueue = useMemo<PlaybackItem[]>(() => {
    return messages.flatMap((message) => {
      const mediaType = getRenderableMediaType(message)
      const fullUrl = getFullMediaUrl(message)

      if (!fullUrl || (mediaType !== 'voice' && mediaType !== 'audio' && mediaType !== 'video_note')) {
        return []
      }

      const meta = message.media_metadata as {
        duration?: number
        file_name?: string
      } | undefined

      const label = mediaType === 'audio'
        ? (meta?.file_name || uz.conversations.audioMessage)
        : mediaType === 'video_note'
          ? uz.conversations.videoNote
          : uz.conversations.voice

      return [{
        messageId: message.id,
        url: fullUrl,
        duration: meta?.duration ?? 0,
        kind: mediaType,
        label,
      }]
    })
  }, [messages])

  // eslint-disable-next-line no-restricted-imports
  useEffect(() => {
    setPlaybackQueue(playbackQueue)
    return () => setPlaybackQueue([])
  }, [playbackQueue, setPlaybackQueue])

  const scrollToMessage = useCallback((telegramMsgId: number) => {
    const target = messageMap.get(telegramMsgId)
    if (!target) {
      toast.info(uz.conversations.messageNotFound)
      return
    }
    const virtualIndex = firstItemIndex + target.chatItemIndex
    virtuosoRef.current?.scrollToIndex({
      index: virtualIndex,
      align: 'center',
      behavior: 'smooth',
    })
    // Cancel previous highlight timer
    if (highlightTimeoutRef.current) clearTimeout(highlightTimeoutRef.current)
    setHighlightedMessageId(target.message.id)
    highlightTimeoutRef.current = setTimeout(() => setHighlightedMessageId(null), 2000)
  }, [messageMap, firstItemIndex])

  // --- Typing indicator -- read from query cache ---
  const { data: typingData } = useQuery<{ isTyping: boolean; timestamp: number }>({
    queryKey: ['typing', conversationId],
    queryFn: () => ({ isTyping: false, timestamp: 0 }),
    enabled: !!conversationId,
    staleTime: Infinity,
  })
  const isTyping = typingData?.isTyping ?? false

  const hydratedConversationRef = useRef<number | null>(null)
  const lastHydrationProjectionRequestKeyRef = useRef<string | null>(null)
  const initialScrollDoneRef = useRef(false)
  const tailConvergenceUntilRef = useRef(0)
  const lastTailScrollKeyRef = useRef<string | null>(null)

  // --- Reset state when conversation changes ---
  // eslint-disable-next-line no-restricted-imports
  useEffect(() => {
    initialScrollDoneRef.current = false
    tailConvergenceUntilRef.current = Date.now() + 3_000
    lastTailScrollKeyRef.current = null
    lastHydrationProjectionRequestKeyRef.current = null
    setHighlightedMessageId(null)
    if (highlightTimeoutRef.current) clearTimeout(highlightTimeoutRef.current)
    setLightboxOpen(false)
    if (conversationId) {
      if (hydratedConversationRef.current !== conversationId) {
        hydratedConversationRef.current = conversationId
        hydrateConversation(conversationId)
      }
      void refetch()
    }
  }, [conversationId, hydrateConversation, refetch])

  // The message read model can later discover that Telegram dialog state is
  // ahead while the same chat stays mounted. Treat that projection as the
  // authority and enqueue hydration even without a route remount.
  // eslint-disable-next-line no-restricted-imports
  useEffect(() => {
    if (
      !conversationId
      || !hydration?.needed
      || hydration.can_retry === false
      || hydrationState !== 'idle'
      || isHydrationRequestPending
    ) return

    const requestKey = [
      conversationId,
      hydrationState,
      hydration.attempt_count ?? 0,
      hydration.next_attempt_at ?? 'now',
      hydration.updated_at ?? 'no-runtime-row',
    ].join(':')
    if (lastHydrationProjectionRequestKeyRef.current === requestKey) return

    lastHydrationProjectionRequestKeyRef.current = requestKey
    hydrateConversation(conversationId)
  }, [
    conversationId,
    hydrateConversation,
    hydration?.attempt_count,
    hydration?.can_retry,
    hydration?.needed,
    hydration?.next_attempt_at,
    hydration?.updated_at,
    hydrationState,
    isHydrationRequestPending,
  ])

  // --- Keep opened chats anchored to the canonical tail while cached pages converge ---
  // eslint-disable-next-line no-restricted-imports
  useEffect(() => {
    if (!conversationId || messages.length === 0) return

    const tailScrollKey = `${conversationId}:${messages.length}:${latestMessageId ?? 'none'}:${dataUpdatedAt}`
    if (lastTailScrollKeyRef.current === tailScrollKey) return

    const isConvergingFreshTail = Date.now() < tailConvergenceUntilRef.current
    if (initialScrollDoneRef.current && !isConvergingFreshTail && !atBottom) {
      return
    }

    initialScrollDoneRef.current = true
    lastTailScrollKeyRef.current = tailScrollKey
    // Double-deferred: Virtuoso needs one frame to render, then another to measure.
    const scrollNow = () => {
      virtuosoRef.current?.scrollToIndex({ index: 'LAST', align: 'end', behavior: 'auto' })
      const scroller = scrollerElementRef.current
      if (scroller) {
        scroller.scrollTo({ top: scroller.scrollHeight, behavior: 'auto' })
      }
    }
    const timer = setTimeout(() => {
      scrollNow()
    }, 100)
    const settleTimer = setTimeout(() => {
      scrollNow()
    }, 350)
    return () => {
      clearTimeout(timer)
      clearTimeout(settleTimer)
    }
  }, [atBottom, conversationId, dataUpdatedAt, latestMessageId, messages.length])

  // Websocket is the fast path, but opened chats must still converge when the
  // socket is reconnecting while the durable Telegram hydration job completes.
  // A ready hydration row with persisted messages is also a convergence signal:
  // it means the backend has rows and the local query cache still needs to catch up.
  // eslint-disable-next-line no-restricted-imports
  useEffect(() => {
    if (
      !conversationId
      || messages.length > 0
      || (!isHydrationPending && !hydrationHasPersistedMessages)
    ) return

    void refetch()
    const timer = window.setInterval(() => {
      void refetch()
    }, 1_500)

    return () => window.clearInterval(timer)
  }, [
    conversationId,
    hydrationHasPersistedMessages,
    isHydrationPending,
    messages.length,
    refetch,
  ])

  // No conversation selected
  if (!conversationId) {
    return (
      <div className="tg-chat-empty">
        <p className="text-muted-foreground text-sm">{uz.conversations.selectChat}</p>
      </div>
    )
  }

  // Initial loading skeleton
  if (isLoading) {
    return (
      <div className="tg-chat-container">
        <div className="px-4 pt-3 text-xs text-muted-foreground">
          {uz.conversations.loadingMore}
        </div>
        <div className="flex flex-col gap-3 p-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className={cn('flex', i % 3 === 0 ? 'justify-end' : 'justify-start')}>
              <Skeleton className={cn('h-10 rounded-xl', i % 3 === 0 ? 'w-48' : 'w-64')} />
            </div>
          ))}
        </div>
      </div>
    )
  }

  // Error state
  if (isError) {
    return (
      <div className="tg-chat-empty">
        <div className="flex flex-col items-center gap-3">
          <p className="text-muted-foreground text-sm">{uz.telegram.loadError}</p>
          <Button variant="outline" size="sm" onClick={() => refetch()}>
            {uz.telegram.retryLoad}
          </Button>
        </div>
      </div>
    )
  }

  // Empty conversation
  if (messages.length === 0) {
    const isIdleRecoveryNeeded = hydration?.needed
      && hydration.can_retry !== false
      && hydrationState === 'idle'
    const isHydrating = isHydrationRequestPending
      || isHydrationPending
      || isIdleRecoveryNeeded
    if (isHydrating) {
      return (
        <div className="tg-chat-empty">
          <div className="flex max-w-xs flex-col items-center gap-3 text-center">
            <Skeleton className="size-10 rounded-full" />
            <p className="text-sm font-medium">Telegramdan xabarlar yuklanmoqda...</p>
            <p className="text-xs leading-5 text-muted-foreground">
              Chat ro'yxatidagi preview bor, xabarlar esa canonical bazaga tushyapti.
            </p>
          </div>
        </div>
      )
    }
    if (hydrationState === 'failed') {
      return (
        <div className="tg-chat-empty">
          <div className="flex max-w-xs flex-col items-center gap-3 text-center">
            <p className="text-sm font-medium">Xabarlarni yuklab bo'lmadi</p>
            <p className="text-xs leading-5 text-muted-foreground">
              {hydration?.last_error || "Telegram ulanishini tekshirib, qayta urinib ko'ring."}
            </p>
            <Button variant="outline" size="sm" onClick={() => conversationId && hydrateConversation(conversationId)}>
              {uz.telegram.retryLoad}
            </Button>
          </div>
        </div>
      )
    }
    return (
      <div className="tg-chat-empty">
        <p className="text-muted-foreground text-sm">{uz.conversations.noMessages}</p>
      </div>
    )
  }

  // Main chat view
  return (
    <div
      className="tg-chat-container"
      data-conversation-id={conversationId}
      data-message-count={messages.length}
      data-latest-message-id={latestMessageId ?? undefined}
    >
      {historyGap && (
        <div className="pointer-events-none absolute left-1/2 top-3 z-10 w-[min(32rem,calc(100%-2rem))] -translate-x-1/2 rounded-full border border-amber-200/80 bg-amber-50/95 px-4 py-2 text-center text-xs font-medium text-amber-900 shadow-sm">
          {uz.conversations.historyGap}
        </div>
      )}
      <MessageList
        key={messageListKey}
        chatItems={chatItems}
        hasOlder={hasPreviousPage ?? false}
        isLoadingOlder={isFetchingPreviousPage}
        onLoadOlder={() => fetchPreviousPage()}
        onAtBottomChange={handleAtBottomChange}
        virtuosoRef={virtuosoRef}
        scrollerElementRef={scrollerElementRef}
        scrollToMessage={scrollToMessage}
        messageMap={messageMap}
        highlightedMessageId={highlightedMessageId}
        onPhotoClick={openLightbox}
        tailVersion={messageListKey}
      />
      <ChatPlaybackBar />
      {isTyping && <TypingIndicator />}
      <ScrollToBottomFab
        visible={showFab}
        unreadCount={0}
        onClick={scrollToBottom}
      />
      <PhotoLightbox
        open={lightboxOpen}
        index={lightboxIndex}
        slides={photoSlides}
        onClose={() => setLightboxOpen(false)}
        onIndexChange={setLightboxIndex}
      />
    </div>
  )
}
