import { beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient } from '@tanstack/react-query'

import { queryKeys } from '@/lib/query-keys'
import { applySellerAgentWebSocketEvent } from './websocket-seller-agent-events'

vi.mock('sonner', () => ({
  toast: Object.assign(vi.fn(), {
    error: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
  }),
}))

vi.mock('@/lib/api-client', () => ({
  api: {
    post: vi.fn(),
  },
}))

function createClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
}

describe('Seller Agent websocket event projection', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('upserts Seller Agent replies and invalidates reply/runtime projections', () => {
    const queryClient = createClient()
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    queryClient.setQueryData(queryKeys.sellerAgentReplies.byConversation(38), [
      { id: 5, conversation_id: 38, status: 'draft', content: 'old' },
    ])

    const handled = applySellerAgentWebSocketEvent({
      queryClient,
      shimmerTimers: new Map(),
      clearShimmer: vi.fn(),
      data: {
        type: 'ai_reply_updated',
        conversation_id: 38,
        ai_reply: { id: 5, conversation_id: 38, status: 'approved', content: 'new' },
      },
    })

    expect(handled).toBe(true)
    expect(queryClient.getQueryData(queryKeys.sellerAgentReplies.byConversation(38))).toEqual([
      expect.objectContaining({ id: 5, status: 'approved', content: 'new' }),
    ])
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: queryKeys.sellerAgentReplyInbox }),
    )
  })

  it('does not invent a Seller Agent reply row for update events when cache is empty', () => {
    const queryClient = createClient()

    applySellerAgentWebSocketEvent({
      queryClient,
      shimmerTimers: new Map(),
      clearShimmer: vi.fn(),
      data: {
        type: 'ai_reply_updated',
        conversation_id: 38,
        ai_reply: { id: 5, conversation_id: 38, status: 'approved', content: 'new' },
      },
    })

    expect(queryClient.getQueryData(queryKeys.sellerAgentReplies.byConversation(38))).toBeUndefined()
  })

  it('returns false for non-Seller-Agent websocket events', () => {
    expect(applySellerAgentWebSocketEvent({
      queryClient: createClient(),
      shimmerTimers: new Map(),
      clearShimmer: vi.fn(),
      data: { type: 'new_message' },
    })).toBe(false)
  })

  it('treats unknown delivery as reconcile state instead of failed resend state', async () => {
    const { toast } = await import('sonner')
    const queryClient = createClient()
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    const handled = applySellerAgentWebSocketEvent({
      queryClient,
      shimmerTimers: new Map(),
      clearShimmer: vi.fn(),
      data: { type: 'delivery_unknown', conversation_id: 38 },
    })

    expect(handled).toBe(true)
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: queryKeys.sellerAgentReplies.byConversation(38) }),
    )
    expect(toast.warning).toHaveBeenCalled()
    expect(toast.error).not.toHaveBeenCalled()
  })
})
