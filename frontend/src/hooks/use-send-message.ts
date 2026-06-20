import {
  useMutation,
  useQueryClient,
  type InfiniteData,
} from '@tanstack/react-query'

import { api } from '@/lib/api-client'
import { queryKeys } from '@/lib/query-keys'
import type { Message, PaginatedMessages } from '@/lib/types'

function createClientMessageUuid() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `send-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function buildOptimisticMessage(
  conversationId: number,
  content: string,
  clientMessageUuid: string,
): Message {
  return {
    id: -Date.now(),
    conversation_id: conversationId,
    sender_type: 'seller',
    content,
    channel: 'telegram_dm',
    is_read: true,
    created_at: new Date().toISOString(),
    client_message_uuid: clientMessageUuid,
    delivery_state: 'pending',
  }
}

function buildUnknownLocalMessage(
  conversationId: number,
  content: string,
  clientMessageUuid: string,
): Message {
  return {
    ...buildOptimisticMessage(conversationId, content, clientMessageUuid),
    delivery_state: 'unknown',
    delivery_runtime: {
      schema_version: 'delivery_runtime.v1',
      state: 'unknown',
      customer_status: 'uncertain',
      next_action: 'reconcile',
      is_terminal: false,
      requires_reconciliation: true,
      can_retry: false,
      attempt_count: 1,
      max_attempts: 3,
      retry_budget_remaining: 2,
      last_error: 'send_response_lost',
      unknown_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    },
  }
}

function isApiErrorLike(error: unknown): error is { status: number; data?: unknown } {
  return !!error
    && typeof error === 'object'
    && typeof (error as { status?: unknown }).status === 'number'
}

function isPreflightUnavailable(error: { data?: unknown }): boolean {
  const detail = (error.data as { detail?: unknown } | undefined)?.detail
  return typeof detail === 'string'
    && detail.toLowerCase().includes('message delivery temporarily unavailable')
}

function isAmbiguousSendFailure(error: unknown): boolean {
  if (error instanceof TypeError) return true
  if (!isApiErrorLike(error)) return false
  if (error.status < 500) return false
  return !isPreflightUnavailable(error)
}

export function useSendMessage() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (variables: {
      conversationId: number
      content: string
      clientMessageUuid?: string
    }) => {
      variables.clientMessageUuid ??= createClientMessageUuid()
      let response: Message
      try {
        response = await api.post<Message>(`/api/conversations/${variables.conversationId}/send-message`, {
          content: variables.content,
          client_message_uuid: variables.clientMessageUuid,
        })
      } catch (error) {
        if (!isAmbiguousSendFailure(error)) {
          throw error
        }
        response = buildUnknownLocalMessage(
          variables.conversationId,
          variables.content,
          variables.clientMessageUuid,
        )
      }
      return {
        response,
        clientMessageUuid: variables.clientMessageUuid,
        conversationId: variables.conversationId,
      }
    },
    onMutate: async (variables) => {
      variables.clientMessageUuid ??= createClientMessageUuid()
      const optimisticMessage = buildOptimisticMessage(
        variables.conversationId,
        variables.content,
        variables.clientMessageUuid,
      )

      await queryClient.cancelQueries({ queryKey: queryKeys.messages.list(variables.conversationId) })
      const messagesSnapshot = queryClient.getQueryData<InfiniteData<PaginatedMessages>>(
        queryKeys.messages.list(variables.conversationId),
      )

      queryClient.setQueryData<InfiniteData<PaginatedMessages>>(
        queryKeys.messages.list(variables.conversationId),
        (old) => {
          if (!old?.pages?.length) {
            return {
              pages: [{ items: [optimisticMessage], has_older: false }],
              pageParams: [undefined],
            }
          }

          const lastIdx = old.pages.length - 1
          return {
            ...old,
            pages: old.pages.map((page, index) =>
              index === lastIdx
                ? { ...page, items: [...page.items, optimisticMessage] }
                : page,
            ),
          }
        },
      )

      return {
        conversationId: variables.conversationId,
        clientMessageUuid: variables.clientMessageUuid,
        messagesSnapshot,
      }
    },
    onSuccess: ({ response, conversationId, clientMessageUuid }) => {
      queryClient.setQueryData<InfiniteData<PaginatedMessages>>(
        queryKeys.messages.list(conversationId),
        (old) => {
          if (!old?.pages?.length) {
            return {
              pages: [{ items: [response], has_older: false }],
              pageParams: [undefined],
            }
          }

          let replaced = false
          const pages = old.pages.map((page) => ({
            ...page,
            items: page.items.map((message) => {
              if (message.client_message_uuid === clientMessageUuid) {
                replaced = true
                return response
              }
              return message
            }),
          }))

          if (replaced) {
            return { ...old, pages }
          }

          const lastIdx = old.pages.length - 1
          return {
            ...old,
            pages: old.pages.map((page, index) =>
              index === lastIdx
                ? {
                    ...page,
                    items: page.items.some((message) => message.id === response.id)
                      ? page.items
                      : [...page.items, response],
                  }
                : page,
            ),
          }
        },
      )

      queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all })
      queryClient.invalidateQueries({ queryKey: queryKeys.conversations.detail(conversationId) })
    },
    onError: (_error, variables, context) => {
      if (context) {
        queryClient.setQueryData(
          queryKeys.messages.list(context.conversationId),
          context.messagesSnapshot,
        )
      } else {
        queryClient.invalidateQueries({ queryKey: queryKeys.messages.list(variables.conversationId) })
      }
    },
  })
}
