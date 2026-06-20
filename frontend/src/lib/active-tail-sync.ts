import {
  type InfiniteData,
  type QueryClient,
} from '@tanstack/react-query'

import { api } from '@/lib/api-client'
import { queryKeys } from '@/lib/query-keys'
import { recordPaginatedMessagesCursor } from '@/lib/sync-session'
import type {
  Message,
  MessageTextEntity,
  PaginatedConversations,
  PaginatedMessages,
} from '@/lib/types'

export function trimConversationPages(
  data: InfiniteData<PaginatedConversations>,
): InfiniteData<PaginatedConversations> {
  const trimmedPages: PaginatedConversations[] = []
  const trimmedPageParams: unknown[] = []

  for (const [index, page] of data.pages.entries()) {
    trimmedPages.push(page)
    trimmedPageParams.push(data.pageParams[index])
    if (!page.next_cursor) break
  }

  return {
    ...data,
    pages: trimmedPages,
    pageParams: trimmedPageParams,
  }
}

export function mergeAuthoritativeTail(
  old: InfiniteData<PaginatedMessages> | undefined,
  latestTail: PaginatedMessages,
): InfiniteData<PaginatedMessages> {
  if (!old?.pages?.length) {
    return {
      pages: [latestTail],
      pageParams: [undefined],
    }
  }

  const lastPageIndex = old.pages.length - 1
  const localTail = old.pages[lastPageIndex]
  const authoritativeClientUuids = new Set(
    latestTail.items
      .map((message) => message.client_message_uuid)
      .filter((uuid): uuid is string => !!uuid),
  )
  const unresolvedLocalSends = localTail.items.filter((message) =>
    isUnresolvedLocalSend(message)
    && !authoritativeClientUuids.has(message.client_message_uuid!),
  )

  return {
    ...old,
    pages: old.pages.map((page, index) =>
      index === lastPageIndex
        ? {
            ...latestTail,
            items: [...latestTail.items, ...unresolvedLocalSends],
          }
        : page,
    ),
  }
}

export function mergeAuthoritativeDelta(
  old: InfiniteData<PaginatedMessages> | undefined,
  delta: PaginatedMessages,
): InfiniteData<PaginatedMessages> {
  if (!old?.pages?.length) {
    return {
      pages: [delta],
      pageParams: [undefined],
    }
  }

  const lastPageIndex = old.pages.length - 1
  const localTail = old.pages[lastPageIndex]
  const authoritativeIds = new Set(delta.items.map((message) => message.id))
  const authoritativeClientUuids = new Set(
    delta.items
      .map((message) => message.client_message_uuid)
      .filter((uuid): uuid is string => !!uuid),
  )
  const retained = localTail.items.filter((message) =>
    !authoritativeIds.has(message.id)
    && (!message.client_message_uuid || !authoritativeClientUuids.has(message.client_message_uuid)),
  )
  const unresolvedOptimistic = retained.filter(isUnresolvedLocalSend)
  const settledExisting = retained.filter((message) =>
    !isUnresolvedLocalSend(message),
  )

  return {
    ...old,
    pages: old.pages.map((page, index) =>
      index === lastPageIndex
        ? {
            ...page,
            latest_conversation_seq: delta.latest_conversation_seq ?? page.latest_conversation_seq,
            latest_conversation_revision:
              delta.latest_conversation_revision ?? page.latest_conversation_revision,
            items: [...settledExisting, ...delta.items, ...unresolvedOptimistic],
          }
        : page,
    ),
  }
}

export function hasMessageId(
  data: InfiniteData<PaginatedMessages> | undefined,
  messageId: number,
): boolean {
  return !!data?.pages?.some((page) =>
    page.items.some((message) => message.id === messageId),
  )
}

function isUnresolvedLocalSend(message: Message): boolean {
  if (!message.client_message_uuid || message.sender_type === 'customer') return false
  if (message.id < 0) return true

  const deliveryStatus = message.delivery_runtime?.customer_status
  return message.delivery_state === 'pending'
    || message.delivery_state === 'unknown'
    || deliveryStatus === 'sending'
    || deliveryStatus === 'uncertain'
}

function latestConversationRevision(
  page: PaginatedMessages,
  message: Message,
  options?: { latestConversationRevision?: number },
): number | undefined {
  return options?.latestConversationRevision
    ?? message.conversation_revision
    ?? page.latest_conversation_revision
    ?? undefined
}

export function appendMissingLiveMessageToTail(
  old: InfiniteData<PaginatedMessages> | undefined,
  message: Message,
  options?: { latestConversationRevision?: number },
): InfiniteData<PaginatedMessages> | undefined {
  if (!old?.pages?.length) return old
  const lastPageIndex = old.pages.length - 1

  return {
    ...old,
    pages: old.pages.map((page, index) =>
      index === lastPageIndex
        ? {
            ...page,
            latest_conversation_seq: message.conversation_seq ?? page.latest_conversation_seq,
            latest_conversation_revision: latestConversationRevision(page, message, options),
            items: [...page.items, message],
          }
        : page,
    ),
  }
}

export function upsertLiveMessageInPages(
  old: InfiniteData<PaginatedMessages> | undefined,
  message: Message,
  options?: { latestConversationRevision?: number },
): InfiniteData<PaginatedMessages> | undefined {
  if (!old?.pages?.length) return old
  if (hasMessageId(old, message.id)) return old

  const hasMatchingClientUuid = !!message.client_message_uuid
    && old.pages.some((page) =>
      page.items.some((item) => item.client_message_uuid === message.client_message_uuid),
    )
  if (!hasMatchingClientUuid) {
    return appendMissingLiveMessageToTail(old, message, options)
  }

  const lastPageIndex = old.pages.length - 1
  return {
    ...old,
    pages: old.pages.map((page, index) =>
      index === lastPageIndex
        ? {
            ...page,
            latest_conversation_seq: message.conversation_seq ?? page.latest_conversation_seq,
            latest_conversation_revision: latestConversationRevision(page, message, options),
            items: page.items.map((item) =>
              item.client_message_uuid === message.client_message_uuid ? message : item,
            ),
          }
        : page,
    ),
  }
}

export function normalizeTextEntities(raw: unknown): MessageTextEntity[] | undefined {
  if (!Array.isArray(raw)) return undefined

  const entities = raw
    .filter((entity): entity is Record<string, unknown> => !!entity && typeof entity === 'object')
    .map((entity): MessageTextEntity | null => {
      const offset = Number(entity.offset)
      const length = Number(entity.length)
      if (!Number.isFinite(offset) || !Number.isFinite(length)) return null
      const documentId = entity.document_id ? String(entity.document_id) : undefined
      return {
        type: String(entity.type || ''),
        offset,
        length,
        ...(documentId ? { document_id: documentId } : {}),
      }
    })
    .filter((entity): entity is MessageTextEntity => !!entity && entity.type.length > 0)

  return entities.length ? entities : []
}

export async function reconcileActiveTail(
  queryClient: QueryClient,
  conversationId: number,
  options?: { afterConversationSeq?: number },
) {
  const query = typeof options?.afterConversationSeq === 'number'
    ? `/api/conversations/${conversationId}/messages?limit=200&after_conversation_seq=${options.afterConversationSeq}`
    : `/api/conversations/${conversationId}/messages?limit=50`
  const latestTail = await api.get<PaginatedMessages>(query)
  if (!latestTail || !Array.isArray(latestTail.items)) return
  recordPaginatedMessagesCursor(conversationId, latestTail)

  queryClient.setQueryData<InfiniteData<PaginatedMessages>>(
    queryKeys.messages.list(conversationId),
    (old) => (
      typeof options?.afterConversationSeq === 'number'
        ? mergeAuthoritativeDelta(old, latestTail)
        : mergeAuthoritativeTail(old, latestTail)
    ),
  )
}
