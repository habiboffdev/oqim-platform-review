import type { QueryClient } from '@tanstack/react-query'

import { reconcileActiveTail } from '@/lib/active-tail-sync'
import { queryKeys } from '@/lib/query-keys'
import {
  applyMarkReadEvent,
  applyMessageDeletedEvent,
  applyMessageEditedEvent,
  applyReadInboxEvent,
  applyReadOutboxEvent,
  applyTypingEvent,
} from '@/lib/websocket-runtime-events'

type TimerMap = Map<number, ReturnType<typeof setTimeout>>

export type ApplyProjectionWebSocketEventOptions = {
  queryClient: QueryClient
  data: Record<string, unknown>
  activeConversationId?: number
  typingTimers: TimerMap
}

export function applyProjectionWebSocketEvent({
  queryClient,
  data,
  activeConversationId,
  typingTimers,
}: ApplyProjectionWebSocketEventOptions): boolean {
  const type = data.type as string
  const conversationId = data.conversation_id as number | undefined

  switch (type) {
    case 'mark_read': {
      applyMarkReadEvent(queryClient, data)
      return true
    }

    case 'conversation_updated': {
      queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all })
      if (conversationId) {
        queryClient.invalidateQueries({
          queryKey: queryKeys.conversations.detail(conversationId),
        })
      }
      return true
    }

    case 'conversation_hydration_updated': {
      queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all })
      if (conversationId) {
        queryClient.invalidateQueries({
          queryKey: queryKeys.conversations.detail(conversationId),
        })
        queryClient.invalidateQueries({
          queryKey: queryKeys.messages.list(conversationId),
        })
      }
      return true
    }

    case 'ingestion_progress': {
      queryClient.invalidateQueries({ queryKey: queryKeys.customers })
      queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.catalog })
      queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.facts })
      queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.sources })
      return true
    }

    case 'learning_signal': {
      queryClient.invalidateQueries({ queryKey: queryKeys.agents.all })
      return true
    }

    case 'typing': {
      applyTypingEvent(queryClient, typingTimers, data)
      return true
    }

    case 'read_outbox': {
      applyReadOutboxEvent(queryClient, data)
      return true
    }

    case 'read_inbox': {
      applyReadInboxEvent(queryClient, data)
      return true
    }

    case 'message_edited': {
      if (conversationId) {
        const foundMessage = applyMessageEditedEvent(queryClient, data)
        if (!foundMessage && conversationId === activeConversationId) {
          void reconcileActiveTail(queryClient, conversationId)
        }
      }
      queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all })
      queryClient.invalidateQueries({ queryKey: queryKeys.liveChats })
      return true
    }

    case 'message_deleted': {
      if (conversationId) {
        const foundMessage = applyMessageDeletedEvent(queryClient, data)
        if (!foundMessage && conversationId === activeConversationId) {
          void reconcileActiveTail(queryClient, conversationId)
        }
      }
      queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all })
      queryClient.invalidateQueries({ queryKey: queryKeys.liveChats })
      return true
    }
  }

  return false
}
