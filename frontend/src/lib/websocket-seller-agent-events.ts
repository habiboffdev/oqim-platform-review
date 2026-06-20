import type { QueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { queryKeys } from '@/lib/query-keys'
import { uz } from '@/lib/uz'
import type { SellerAgentReply } from '@/lib/types'

type TimerMap = Map<number, ReturnType<typeof setTimeout>>

export type ApplySellerAgentWebSocketEventOptions = {
  queryClient: QueryClient
  data: Record<string, unknown>
  shimmerTimers: TimerMap
  clearShimmer: (conversationId: number) => void
}

function upsertSellerAgentReply(
  queryClient: QueryClient,
  conversationId: number,
  reply: SellerAgentReply,
  options: { appendWhenMissing: boolean },
) {
  queryClient.setQueryData<SellerAgentReply[]>(
    queryKeys.sellerAgentReplies.byConversation(conversationId),
    (old) => {
      if (!old) return options.appendWhenMissing ? [reply] : old
      const exists = old.some((item) => item.id === reply.id)
      if (exists) return old.map((item) => (item.id === reply.id ? reply : item))
      return options.appendWhenMissing ? [...old, reply] : old
    },
  )
}

function refreshSellerAgentReplyProjection(
  queryClient: QueryClient,
  conversationId: number | undefined,
  data: Record<string, unknown>,
  options: { appendWhenMissing: boolean },
) {
  if (!conversationId) return
  if (data.ai_reply) {
    upsertSellerAgentReply(queryClient, conversationId, data.ai_reply as SellerAgentReply, options)
    return
  }
  queryClient.invalidateQueries({
    queryKey: queryKeys.sellerAgentReplies.byConversation(conversationId),
  })
}

export function applySellerAgentWebSocketEvent({
  queryClient,
  data,
  shimmerTimers,
  clearShimmer,
}: ApplySellerAgentWebSocketEventOptions): boolean {
  const type = data.type as string
  const conversationId = data.conversation_id as number | undefined

  switch (type) {
    case 'ai_thinking': {
      if (conversationId) {
        queryClient.setQueryData<Set<number>>(queryKeys.shimmer, (old) => {
          const set = new Set(old)
          set.add(conversationId)
          return set
        })
        const timer = setTimeout(() => {
          clearShimmer(conversationId)
        }, 15_000)
        shimmerTimers.set(conversationId, timer)
      }
      return true
    }

    case 'ai_thinking_failed': {
      if (conversationId) clearShimmer(conversationId)
      return true
    }

    case 'ai_reply_created': {
      if (conversationId) clearShimmer(conversationId)
      refreshSellerAgentReplyProjection(queryClient, conversationId, data, { appendWhenMissing: true })
      queryClient.invalidateQueries({ queryKey: queryKeys.sellerAgentReplyInbox })
      return true
    }

    case 'ai_reply_updated':
    case 'ai_reply_dismissed': {
      refreshSellerAgentReplyProjection(queryClient, conversationId, data, { appendWhenMissing: false })
      queryClient.invalidateQueries({ queryKey: queryKeys.sellerAgentReplyInbox })
      return true
    }

    case 'ai_reply_approved': {
      refreshSellerAgentReplyProjection(queryClient, conversationId, data, { appendWhenMissing: false })
      queryClient.invalidateQueries({ queryKey: queryKeys.sellerAgentReplyInbox })
      queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all })
      return true
    }

    case 'ai_reply_sent': {
      refreshSellerAgentReplyProjection(queryClient, conversationId, data, { appendWhenMissing: false })
      queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all })
      return true
    }

    case 'delivery_failed': {
      if (conversationId) {
        queryClient.invalidateQueries({
          queryKey: queryKeys.sellerAgentReplies.byConversation(conversationId),
        })
      }
      toast.error(uz.sellerAgentReplies.deliveryFailed)
      return true
    }

    case 'delivery_unknown': {
      if (conversationId) {
        queryClient.invalidateQueries({
          queryKey: queryKeys.sellerAgentReplies.byConversation(conversationId),
        })
      }
      toast.warning(uz.sellerAgentReplies.deliveryUnknown)
      return true
    }

    case 'auto_reply_scheduled': {
      const { customer_name } = data as {
        customer_name?: string
      }
      toast(uz.autonomy.toast(customer_name || ''), {
        duration: 3000,
      })
      queryClient.invalidateQueries({ queryKey: queryKeys.sellerAgentReplyInbox })
      return true
    }

    case 'auto_reply_sent': {
      queryClient.invalidateQueries({ queryKey: queryKeys.sellerAgentReplyInbox })
      return true
    }

  }

  return false
}
