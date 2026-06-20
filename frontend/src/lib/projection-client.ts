import type { InfiniteData, QueryClient } from '@tanstack/react-query'

import { reconcileActiveTail, trimConversationPages } from '@/lib/active-tail-sync'
import { queryKeys } from '@/lib/query-keys'
import { buildProjectionSyncPlan } from '@/lib/sync-projections'
import type { Conversation, PaginatedConversations } from '@/lib/types'

export type ConversationStatePatch = Record<string, unknown>

function conversationTime(conversation: Conversation): number {
  const parsed = Date.parse(conversation.last_message_at || '')
  return Number.isFinite(parsed) ? parsed : 0
}

function sortByCanonicalTail(a: Conversation, b: Conversation): number {
  const byTime = conversationTime(b) - conversationTime(a)
  if (byTime !== 0) return byTime
  return b.id - a.id
}

export function patchConversationState(
  queryClient: QueryClient,
  conversationId: number,
  conversationState: ConversationStatePatch,
) {
  const patchConversation = (conversation: Conversation): Conversation => {
    if (conversation.id !== conversationId) return conversation
    const next: Conversation = { ...conversation }
    if (typeof conversationState.last_message_text === 'string') {
      next.last_message_text = conversationState.last_message_text
    }
    if (typeof conversationState.unread_count === 'number') {
      next.unread_count = conversationState.unread_count
    }
    if (typeof conversationState.last_message_at === 'string') {
      next.last_message_at = conversationState.last_message_at
    }
    if (typeof conversationState.latest_conversation_seq === 'number') {
      next.latest_conversation_seq = conversationState.latest_conversation_seq
    }
    if (typeof conversationState.latest_conversation_revision === 'number') {
      next.latest_conversation_revision = conversationState.latest_conversation_revision
    }
    return next
  }

  const shouldReposition = typeof conversationState.last_message_at === 'string'

  queryClient.setQueriesData<InfiniteData<PaginatedConversations>>(
    { queryKey: queryKeys.conversations.all },
    (old) => {
      if (!old?.pages) return old
      if (!shouldReposition) {
        return {
          ...old,
          pages: old.pages.map((page) => ({
            ...page,
            items: page.items.map(patchConversation),
          })),
        }
      }

      let patched: Conversation | undefined
      const pages = old.pages.map((page) => ({
        ...page,
        items: page.items.flatMap((conversation) => {
          if (conversation.id !== conversationId) return [conversation]
          patched = patchConversation(conversation)
          return []
        }),
      }))
      if (!patched) return old
      return trimConversationPages({
        ...old,
        pages: pages.map((page, index) =>
          index === 0
            ? { ...page, items: [patched as Conversation, ...page.items].sort(sortByCanonicalTail) }
            : page,
        ),
      })
    },
  )
  queryClient.setQueriesData<Conversation[]>(
    { queryKey: queryKeys.conversations.all },
    (old) => {
      if (!Array.isArray(old)) return old
      if (!shouldReposition) return old.map(patchConversation)
      let patched: Conversation | undefined
      const rest = old.flatMap((conversation) => {
        if (conversation.id !== conversationId) return [conversation]
        patched = patchConversation(conversation)
        return []
      })
      return patched ? [patched, ...rest].sort(sortByCanonicalTail) : old
    },
  )
  queryClient.setQueryData<Conversation>(
    queryKeys.conversations.detail(conversationId),
    (old) => old ? patchConversation(old) : old,
  )
}

export function applyProjectionSync(
  queryClient: QueryClient,
  syncData: Record<string, unknown>,
  scopedConversationId?: number,
): boolean {
  const plan = buildProjectionSyncPlan(syncData.projections, scopedConversationId)
  if (!plan) return false

  if (plan.messageTail) {
    void reconcileActiveTail(
      queryClient,
      plan.messageTail.conversationId,
      plan.messageTail.afterConversationSeq !== undefined
        ? { afterConversationSeq: plan.messageTail.afterConversationSeq }
        : undefined,
    )
  }
  if (plan.invalidateConversations) {
    queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all })
  }
  if (plan.invalidateLiveChats) {
    queryClient.invalidateQueries({ queryKey: queryKeys.liveChats })
  }
  if (plan.invalidateReplyInbox) {
    queryClient.invalidateQueries({ queryKey: queryKeys.sellerAgentReplyInbox })
  }
  plan.detailConversationIds.forEach((conversationId) => {
    queryClient.invalidateQueries({
      queryKey: queryKeys.conversations.detail(conversationId),
    })
  })
  plan.replyConversationIds.forEach((conversationId) => {
    queryClient.invalidateQueries({
      queryKey: queryKeys.sellerAgentReplies.byConversation(conversationId),
    })
  })
  if (plan.invalidateAllSellerAgentReplies) {
    queryClient.invalidateQueries({ queryKey: queryKeys.sellerAgentReplies.all })
  }

  return true
}
