import {
  type InfiniteData,
  type QueryClient,
} from '@tanstack/react-query'

import { trimConversationPages } from '@/lib/active-tail-sync'
import { normalizeTextEntities } from '@/lib/active-tail-sync'
import { queryKeys } from '@/lib/query-keys'
import { recordConversationSyncCursor } from '@/lib/sync-session'
import type {
  LiveChat,
  MessageTextEntity,
  PaginatedConversations,
  PaginatedMessages,
} from '@/lib/types'

type TimerMap = Map<number, ReturnType<typeof setTimeout>>

export function clearTypingIndicator(
  queryClient: QueryClient,
  typingTimers: TimerMap,
  conversationId: number,
) {
  clearTimeout(typingTimers.get(conversationId))
  typingTimers.delete(conversationId)
  queryClient.setQueryData(['typing', conversationId], {
    isTyping: false,
    timestamp: Date.now(),
  })
}

export function applyTypingEvent(
  queryClient: QueryClient,
  typingTimers: TimerMap,
  data: Record<string, unknown>,
) {
  const conversationId = data.conversation_id as number
  const isTyping = data.is_typing as boolean

  queryClient.setQueryData(['typing', conversationId], {
    isTyping,
    timestamp: Date.now(),
  })

  if (isTyping) {
    clearTimeout(typingTimers.get(conversationId))
    const timer = setTimeout(() => {
      queryClient.setQueryData(['typing', conversationId], {
        isTyping: false,
        timestamp: Date.now(),
      })
      typingTimers.delete(conversationId)
    }, 5000)
    typingTimers.set(conversationId, timer)
    return
  }

  clearTimeout(typingTimers.get(conversationId))
  typingTimers.delete(conversationId)
}

export function applyMarkReadEvent(
  queryClient: QueryClient,
  data: Record<string, unknown>,
) {
  const conversationId = data.conversation_id as number | undefined
  const unreadCount = (data.unread_count ?? 0) as number
  if (!conversationId) return

  updateConversationUnreadCount(queryClient, conversationId, unreadCount)
  queryClient.invalidateQueries({ queryKey: queryKeys.liveChats })
}

export function applyReadInboxEvent(
  queryClient: QueryClient,
  data: Record<string, unknown>,
) {
  const conversationId = data.conversation_id as number | undefined
  const unreadCount = (data.unread_count ?? 0) as number
  if (!conversationId) return

  updateConversationUnreadCount(queryClient, conversationId, unreadCount)
  queryClient.invalidateQueries({ queryKey: queryKeys.liveChats })
}

export function applyReadOutboxEvent(
  queryClient: QueryClient,
  data: Record<string, unknown>,
) {
  const { conversation_id, read_outbox_max_id } = data as {
    conversation_id: number
    read_outbox_max_id: number
  }

  queryClient.setQueriesData<LiveChat[]>(
    { queryKey: queryKeys.liveChats },
    (old) => old?.map((chat) =>
      chat.conversation_id === conversation_id
        ? { ...chat, read_outbox_max_id }
        : chat,
    ),
  )
}

export function applyMessageEditedEvent(
  queryClient: QueryClient,
  data: Record<string, unknown>,
): boolean {
  const {
    conversation_id,
    message_id,
    content,
    edited_at,
    conversation_revision,
    text_entities,
  } = data as {
    conversation_id: number
    message_id: number
    content: string
    edited_at: string | null
    conversation_revision?: number
    text_entities?: MessageTextEntity[]
  }
  if (!conversation_id) return false

  let foundMessage = false
  recordConversationSyncCursor(conversation_id, {
    conversationRevision: conversation_revision,
  })
  queryClient.setQueryData<InfiniteData<PaginatedMessages>>(
    queryKeys.messages.list(conversation_id),
    (old) => {
      if (!old) return old
      return {
        ...old,
        pages: old.pages.map((page) => ({
          ...page,
          latest_conversation_revision:
            conversation_revision ?? page.latest_conversation_revision,
          items: page.items.map((message) => {
            if (message.id !== message_id) return message
            foundMessage = true
            return {
              ...message,
              content,
              text_entities: normalizeTextEntities(text_entities),
              edited_at: edited_at ?? undefined,
            }
          }),
        })),
      }
    },
  )
  return foundMessage
}

export function applyMessageDeletedEvent(
  queryClient: QueryClient,
  data: Record<string, unknown>,
): boolean {
  const { conversation_id, message_id, conversation_revision } = data as {
    conversation_id: number
    message_id: number
    conversation_revision?: number
  }
  if (!conversation_id) return false

  let foundMessage = false
  recordConversationSyncCursor(conversation_id, {
    conversationRevision: conversation_revision,
  })
  queryClient.setQueryData<InfiniteData<PaginatedMessages>>(
    queryKeys.messages.list(conversation_id),
    (old) => {
      if (!old) return old
      return {
        ...old,
        pages: old.pages.map((page) => ({
          ...page,
          latest_conversation_revision:
            conversation_revision ?? page.latest_conversation_revision,
          items: page.items.map((message) => {
            if (message.id !== message_id) return message
            foundMessage = true
            return {
              ...message,
              content: '[deleted]',
            }
          }),
        })),
      }
    },
  )
  return foundMessage
}

function updateConversationUnreadCount(
  queryClient: QueryClient,
  conversationId: number,
  unreadCount: number,
) {
  queryClient.setQueriesData<InfiniteData<PaginatedConversations>>(
    { queryKey: queryKeys.conversations.all },
    (old) => {
      if (!old) return old
      return trimConversationPages({
        ...old,
        pages: old.pages.map((page) => ({
          ...page,
          items: page.items.map((conversation) =>
            conversation.id === conversationId
              ? { ...conversation, unread_count: unreadCount }
              : conversation,
          ),
        })),
      })
    },
  )
}
