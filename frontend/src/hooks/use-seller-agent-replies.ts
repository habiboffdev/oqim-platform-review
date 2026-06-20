import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@/lib/api-client'
import { queryKeys } from '@/lib/query-keys'
import { uz } from '@/lib/uz'
import type { SellerAgentReply, SellerAgentReplyTrace } from '@/lib/types'

type SellerAgentReplyBlockReason =
  | 'awaiting_media_hydration'
  | 'existing_ai_reply'
  | 'seller_replied'
  | 'superseded_by_newer_customer_message'
  | 'trigger_deleted'
  | 'trigger_missing'

const SELLER_AGENT_REPLY_BLOCK_REASONS: readonly SellerAgentReplyBlockReason[] = [
  'awaiting_media_hydration',
  'existing_ai_reply',
  'seller_replied',
  'superseded_by_newer_customer_message',
  'trigger_deleted',
  'trigger_missing',
]

function extractSellerAgentReplyBlockReason(error: unknown): SellerAgentReplyBlockReason | null {
  if (!error || typeof error !== 'object') return null
  const detail = (error as { data?: { detail?: unknown } }).data?.detail
  if (typeof detail !== 'string') return null
  return SELLER_AGENT_REPLY_BLOCK_REASONS.find((reason) => detail.includes(reason)) ?? null
}

export function useLatestSellerAgentReply(conversationId: number | undefined) {
  const { data: pendingReply } = useQuery({
    queryKey: queryKeys.sellerAgentReplies.byConversation(conversationId!),
    queryFn: () => api.get<SellerAgentReply[]>(`/api/conversations/${conversationId}/ai-replies`),
    enabled: !!conversationId,
    staleTime: 30_000,
    refetchInterval: 10_000, // Polling fallback when WebSocket is unavailable (#80)
    select: (replies) => replies.find((r) => (
      r.status === 'draft' ||
      r.status === 'delivery_failed' ||
      r.status === 'delivery_unknown'
    )) ?? null,
  })
  return pendingReply ?? null
}

export function useApproveSellerAgentReply() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (replyId: number) =>
      api.post<SellerAgentReply>(`/api/ai-replies/${replyId}/approve`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.sellerAgentReplies.all })
      queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all })
      toast.success(uz.compose.send)
    },
    onError: () => { toast.error(uz.common.error) },
  })
}

export function useEditSellerAgentReply() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ replyId, content }: { replyId: number; content: string }) =>
      api.post<SellerAgentReply>(`/api/ai-replies/${replyId}/edit`, { content }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.sellerAgentReplies.all })
      queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all })
      queryClient.invalidateQueries({ queryKey: queryKeys.sellerAgentReplyInbox })
      // Backend now sends via sidecar — show sent or delivery-failed toast (#135)
      if (data.status === 'delivery_failed') {
        toast.error(uz.sellerAgentReplies.deliveryFailed)
      } else if (data.status === 'delivery_unknown') {
        toast.warning(uz.sellerAgentReplies.deliveryUnknown)
      } else {
        toast.success(uz.compose.sent)
      }
    },
    onError: () => { toast.error(uz.common.error) },
  })
}

export function useDismissSellerAgentReply() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ replyId, reason }: { replyId: number; reason?: string }) =>
      api.post<SellerAgentReply>(`/api/ai-replies/${replyId}/reject`, reason ? { override_reason: reason } : undefined),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.sellerAgentReplies.all })
      queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all })
      toast.success(uz.compose.dismissLearning)
    },
    onError: () => { toast.error(uz.common.error) },
  })
}

export function useRegenerateSellerAgentReply() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ replyId, instruction }: { replyId: number; instruction?: string }) =>
      api.post<SellerAgentReply>(
        `/api/ai-replies/${replyId}/regenerate${instruction ? `?instruction=${encodeURIComponent(instruction)}` : ''}`
      ),
    onSuccess: () => {
      queryClient.refetchQueries({ queryKey: queryKeys.sellerAgentReplies.all })
      queryClient.invalidateQueries({ queryKey: queryKeys.sellerAgentReplyInbox })
    },
    onError: (error) => {
      const blockedReason = extractSellerAgentReplyBlockReason(error)
      if (blockedReason) {
        queryClient.refetchQueries({ queryKey: queryKeys.sellerAgentReplies.all })
        queryClient.invalidateQueries({ queryKey: queryKeys.sellerAgentReplyInbox })
        toast.error(uz.compose.blockedReasons[blockedReason])
        return
      }
      toast.error(uz.common.error)
    },
  })
}

// -- Dev panel: Seller Agent reply trace --

export function useSellerAgentReplyTrace(replyId: number | undefined, enabled = false) {
  return useQuery({
    queryKey: ['seller-agent-reply-trace', replyId],
    queryFn: () => api.get<SellerAgentReplyTrace>(`/api/ai-replies/${replyId}/trace`),
    enabled: enabled && !!replyId,
    staleTime: Infinity,
    retry: false,
  })
}

// -- Typed chip execution hooks --

export function useApproveAndStage() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ replyId, conversationId, stage }: {
      replyId: number
      conversationId: number
      stage: string
    }) => {
      await api.post(`/api/ai-replies/${replyId}/approve`)
      await api.patch(`/api/conversations/${conversationId}`, { pipeline_stage: stage })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.sellerAgentReplies.all })
      queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all })
      toast.success(uz.sellerAgentReplies.sent)
    },
    onError: () => { toast.error(uz.common.error) },
  })
}

export function useSendQuickReply() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ conversationId, text }: { conversationId: number; text: string }) =>
      api.post(`/api/conversations/${conversationId}/send-message`, { content: text }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all })
      toast.success(uz.sellerAgentReplies.sent)
    },
    onError: () => { toast.error(uz.common.error) },
  })
}
