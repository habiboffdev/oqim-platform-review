import {
  type InfiniteData,
  type QueryClient,
} from '@tanstack/react-query'

import {
  appendMissingLiveMessageToTail,
  hasMessageId,
  reconcileActiveTail,
  trimConversationPages,
  upsertLiveMessageInPages,
} from '@/lib/active-tail-sync'
import { normalizeLiveMessagePayload } from '@/lib/live-message'
import { queryKeys } from '@/lib/query-keys'
import { recordMessageCursor } from '@/lib/sync-session'
import { clearTypingIndicator } from '@/lib/websocket-runtime-events'
import type {
  Conversation,
  Message,
  PaginatedConversations,
  PaginatedMessages,
} from '@/lib/types'

type TimerMap = Map<number, ReturnType<typeof setTimeout>>

export type ApplyMessageWebSocketEventOptions = {
  queryClient: QueryClient
  data: Record<string, unknown>
  activeConversationId?: number
  typingTimers: TimerMap
}

function appendMessageToCache(
  queryClient: QueryClient,
  conversationId: number,
  message: Message,
  options?: { latestConversationRevision?: number },
) {
  recordMessageCursor(conversationId, {
    ...message,
    conversation_revision: options?.latestConversationRevision ?? message.conversation_revision,
  })

  const queryKey = queryKeys.messages.list(conversationId)
  const state = queryClient.getQueryState(queryKey)

  queryClient.setQueryData<InfiniteData<PaginatedMessages>>(
    queryKey,
    (old) => upsertLiveMessageInPages(old, message, {
      latestConversationRevision: options?.latestConversationRevision,
    }),
  )

  if (state?.fetchStatus === 'fetching') {
    setTimeout(() => {
      const freshData = queryClient.getQueryData<InfiniteData<PaginatedMessages>>(queryKey)
      if (!freshData) return
      if (!hasMessageId(freshData, message.id)) {
        queryClient.setQueryData<InfiniteData<PaginatedMessages>>(
          queryKey,
          (old) => appendMissingLiveMessageToTail(old, message, {
            latestConversationRevision: options?.latestConversationRevision,
          }),
        )
      }
    }, 500)
  }
}

function applyConversationProjection(
  queryClient: QueryClient,
  data: Record<string, unknown>,
) {
  if (!data.conversation) {
    queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all })
    return
  }

  const updatedConversation = { ...(data.conversation as Conversation) }
  if (data.message) {
    const rawMessage = data.message as Record<string, unknown>
    const messageAt = typeof rawMessage.telegram_timestamp === 'string'
      ? rawMessage.telegram_timestamp
      : typeof rawMessage.created_at === 'string'
        ? rawMessage.created_at
        : null
    const messageTime = parseConversationTime(messageAt)
    const projectionTime = parseConversationTime(updatedConversation.last_message_at)
    const messageIsProjectionTail = messageTime !== null && (
      projectionTime === null || messageTime >= projectionTime
    )
    if (messageIsProjectionTail && typeof rawMessage.content === 'string' && rawMessage.content.trim()) {
      updatedConversation.last_message_text = rawMessage.content.slice(0, 100)
    }
    if (messageIsProjectionTail && messageAt) {
      updatedConversation.last_message_at = messageAt
    }
  }

  queryClient.setQueriesData<InfiniteData<PaginatedConversations>>(
    { queryKey: queryKeys.conversations.all },
    (old) => {
      if (!old?.pages) return old
      return mergeConversationProjection(old, updatedConversation)
    },
  )

  queryClient.setQueriesData<Conversation[]>(
    { queryKey: queryKeys.conversations.all },
    (old) => {
      if (!old || !Array.isArray(old)) return old
      return mergeConversationArray(old, updatedConversation)
    },
  )

  queryClient.invalidateQueries({
    queryKey: queryKeys.conversations.all,
    predicate: (query) =>
      query.queryKey.length >= 3
      && query.queryKey[1] === 'list'
      && Object.keys((query.queryKey[2] as Record<string, unknown>) || {}).length > 0,
  })
}

function parseConversationTime(value: string | null | undefined): number | null {
  if (!value) return null
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : null
}

function compareConversationProjection(a: Conversation, b: Conversation): number {
  const aTime = parseConversationTime(a.last_message_at) ?? parseConversationTime(a.created_at) ?? 0
  const bTime = parseConversationTime(b.last_message_at) ?? parseConversationTime(b.created_at) ?? 0
  if (aTime !== bTime) return bTime - aTime
  return b.id - a.id
}

function belongsInLoadedWindow(
  loaded: Conversation[],
  updatedConversation: Conversation,
  hasExisting: boolean,
): boolean {
  if (hasExisting || loaded.length === 0) return true
  const lastLoaded = loaded[loaded.length - 1]
  return compareConversationProjection(updatedConversation, lastLoaded) <= 0
}

function mergeConversationArray(
  old: Conversation[],
  updatedConversation: Conversation,
): Conversation[] {
  const hasExisting = old.some((conversation) => conversation.id === updatedConversation.id)
  if (!belongsInLoadedWindow(old, updatedConversation, hasExisting)) return old
  return [
    ...old.filter((conversation) => conversation.id !== updatedConversation.id),
    updatedConversation,
  ].sort(compareConversationProjection)
}

function mergeConversationProjection(
  old: InfiniteData<PaginatedConversations>,
  updatedConversation: Conversation,
): InfiniteData<PaginatedConversations> {
  const loaded = old.pages.flatMap((page) => page.items)
  const hasExisting = loaded.some((conversation) => conversation.id === updatedConversation.id)
  if (!belongsInLoadedWindow(loaded, updatedConversation, hasExisting)) return old

  const sorted = [
    ...loaded.filter((conversation) => conversation.id !== updatedConversation.id),
    updatedConversation,
  ].sort(compareConversationProjection)
  const pageSizes = old.pages.map((page) => page.items.length)
  if (!hasExisting && pageSizes.length > 0) {
    pageSizes[0] += 1
  }

  let cursor = 0
  return trimConversationPages({
    ...old,
    pages: old.pages.map((page, index) => {
      const size = pageSizes[index] ?? 0
      const items = sorted.slice(cursor, cursor + size)
      cursor += size
      return { ...page, items }
    }),
  })
}

export function applyMessageWebSocketEvent({
  queryClient,
  data,
  activeConversationId,
  typingTimers,
}: ApplyMessageWebSocketEventOptions): boolean {
  if (data.type !== 'new_message') return false

  const conversationId = data.conversation_id as number | undefined
  applyConversationProjection(queryClient, data)

  if (conversationId && data.message) {
    const rawMessage = data.message as Record<string, unknown>
    const fullMessage = normalizeLiveMessagePayload(rawMessage, conversationId)
    appendMessageToCache(queryClient, conversationId, fullMessage, {
      latestConversationRevision: fullMessage.conversation_revision,
    })

    if (conversationId === activeConversationId) {
      void reconcileActiveTail(queryClient, conversationId)
    }

    clearTypingIndicator(queryClient, typingTimers, conversationId)
  }

  queryClient.invalidateQueries({ queryKey: queryKeys.liveChats })
  return true
}
