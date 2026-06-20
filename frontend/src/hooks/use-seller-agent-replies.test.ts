// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ReactNode } from 'react'
import { useApproveSellerAgentReply, useEditSellerAgentReply, useDismissSellerAgentReply, useLatestSellerAgentReply, useRegenerateSellerAgentReply } from './use-seller-agent-replies'

// Mock api-client
vi.mock('@/lib/api-client', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}))

// Mock sonner so toast calls don't throw in jsdom
vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  },
}))

import { api } from '@/lib/api-client'
import { toast } from 'sonner'
import { uz } from '@/lib/uz'

const mockApi = api as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
}

const mockToast = toast as unknown as {
  success: ReturnType<typeof vi.fn>
  error: ReturnType<typeof vi.fn>
  warning: ReturnType<typeof vi.fn>
}

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: queryClient }, children)
}

const mockReply = {
  id: 10,
  conversation_id: 5,
  confidence_score: 0.85,
  status: 'draft' as const,
  draft_content: 'Ha, mahsulot bor',
  chips: null,
  split_messages: null,
  is_auto_sent: false,
  created_at: '2026-01-01T00:00:00Z',
}

describe('useLatestSellerAgentReply', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('returns the first pending seller reply', async () => {
    const replies = [
      { ...mockReply, id: 1, status: 'sent' as const },
      { ...mockReply, id: 2, status: 'draft' as const },
    ]
    mockApi.get.mockResolvedValue(replies)

    const wrapper = createWrapper()
    const { result } = renderHook(() => useLatestSellerAgentReply(5), { wrapper })

    await waitFor(() => expect(result.current).not.toBeNull())
    expect(result.current?.id).toBe(2)
  })

  it('returns null when no pending seller reply exists', async () => {
    mockApi.get.mockResolvedValue([{ ...mockReply, status: 'sent' as const }])

    const wrapper = createWrapper()
    const { result } = renderHook(() => useLatestSellerAgentReply(5), { wrapper })

    await waitFor(() => expect(mockApi.get).toHaveBeenCalled())
    expect(result.current).toBeNull()
  })

  it('returns delivery_unknown replies so users see reconcile state', async () => {
    mockApi.get.mockResolvedValue([
      { ...mockReply, id: 3, status: 'delivery_unknown' as const },
      { ...mockReply, id: 4, status: 'sent' as const },
    ])

    const wrapper = createWrapper()
    const { result } = renderHook(() => useLatestSellerAgentReply(5), { wrapper })

    await waitFor(() => expect(result.current).not.toBeNull())
    expect(result.current?.id).toBe(3)
  })
})

describe('useApproveSellerAgentReply', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('calls POST /api/ai-replies/:id/approve', async () => {
    mockApi.post.mockResolvedValue({ ...mockReply, status: 'approved' })

    const wrapper = createWrapper()
    const { result } = renderHook(() => useApproveSellerAgentReply(), { wrapper })

    await act(async () => {
      result.current.mutate(10)
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockApi.post).toHaveBeenCalledWith('/api/ai-replies/10/approve')
  })

  it('shows success toast on approve', async () => {
    mockApi.post.mockResolvedValue({ ...mockReply, status: 'approved' })

    const wrapper = createWrapper()
    const { result } = renderHook(() => useApproveSellerAgentReply(), { wrapper })

    await act(async () => {
      result.current.mutate(10)
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockToast.success).toHaveBeenCalledWith(uz.compose.send)
  })

  it('shows error toast on failure', async () => {
    mockApi.post.mockRejectedValue(new Error('Server error'))

    const wrapper = createWrapper()
    const { result } = renderHook(() => useApproveSellerAgentReply(), { wrapper })

    await act(async () => {
      result.current.mutate(10)
    })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(mockToast.error).toHaveBeenCalledWith(uz.common.error)
  })
})

describe('useEditSellerAgentReply', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('calls POST /api/ai-replies/:id/edit with new content', async () => {
    mockApi.post.mockResolvedValue({ ...mockReply, draft_content: 'Updated text' })

    const wrapper = createWrapper()
    const { result } = renderHook(() => useEditSellerAgentReply(), { wrapper })

    await act(async () => {
      result.current.mutate({ replyId: 10, content: 'Updated text' })
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockApi.post).toHaveBeenCalledWith('/api/ai-replies/10/edit', { content: 'Updated text' })
    expect(mockToast.success).toHaveBeenCalledWith(uz.compose.sent)
  })

  it('shows reconcile toast when edited draft delivery is unknown', async () => {
    mockApi.post.mockResolvedValue({ ...mockReply, status: 'delivery_unknown' })

    const wrapper = createWrapper()
    const { result } = renderHook(() => useEditSellerAgentReply(), { wrapper })

    await act(async () => {
      result.current.mutate({ replyId: 10, content: 'Updated text' })
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockToast.warning).toHaveBeenCalledWith(uz.sellerAgentReplies.deliveryUnknown)
    expect(mockToast.error).not.toHaveBeenCalled()
  })
})

describe('useDismissSellerAgentReply', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('calls POST /api/ai-replies/:id/reject', async () => {
    mockApi.post.mockResolvedValue({ ...mockReply, status: 'rejected' })

    const wrapper = createWrapper()
    const { result } = renderHook(() => useDismissSellerAgentReply(), { wrapper })

    await act(async () => {
      result.current.mutate({ replyId: 10 })
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockApi.post).toHaveBeenCalledWith('/api/ai-replies/10/reject', undefined)
  })

  it('passes override_reason when reason is provided', async () => {
    mockApi.post.mockResolvedValue({ ...mockReply, status: 'rejected' })

    const wrapper = createWrapper()
    const { result } = renderHook(() => useDismissSellerAgentReply(), { wrapper })

    await act(async () => {
      result.current.mutate({ replyId: 10, reason: 'bad_tone' })
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockApi.post).toHaveBeenCalledWith('/api/ai-replies/10/reject', { override_reason: 'bad_tone' })
    expect(mockToast.success).toHaveBeenCalledWith(uz.compose.dismissLearning)
  })
})

describe('useRegenerateSellerAgentReply', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('calls POST /api/ai-replies/:id/regenerate with instruction', async () => {
    mockApi.post.mockResolvedValue({ ...mockReply, id: 11 })

    const wrapper = createWrapper()
    const { result } = renderHook(() => useRegenerateSellerAgentReply(), { wrapper })

    await act(async () => {
      result.current.mutate({ replyId: 10, instruction: 'Qisqaroq yoz' })
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockApi.post).toHaveBeenCalledWith('/api/ai-replies/10/regenerate?instruction=Qisqaroq%20yoz')
  })

  it('shows specific blocked toast when media hydration is still pending', async () => {
    mockApi.post.mockRejectedValue({
      status: 409,
      statusText: 'Conflict',
      data: { detail: 'Cannot regenerate reply: awaiting_media_hydration' },
    })

    const wrapper = createWrapper()
    const { result } = renderHook(() => useRegenerateSellerAgentReply(), { wrapper })

    await act(async () => {
      result.current.mutate({ replyId: 10 })
    })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(mockToast.error).toHaveBeenCalledWith(
      uz.compose.blockedReasons.awaiting_media_hydration,
    )
  })

  it('shows specific blocked toast when the customer has already moved the tail forward', async () => {
    mockApi.post.mockRejectedValue({
      status: 409,
      statusText: 'Conflict',
      data: { detail: 'Cannot regenerate reply: superseded_by_newer_customer_message' },
    })

    const wrapper = createWrapper()
    const { result } = renderHook(() => useRegenerateSellerAgentReply(), { wrapper })

    await act(async () => {
      result.current.mutate({ replyId: 10 })
    })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(mockToast.error).toHaveBeenCalledWith(
      uz.compose.blockedReasons.superseded_by_newer_customer_message,
    )
  })
})
