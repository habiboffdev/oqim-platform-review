// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ReactNode } from 'react'
import {
  useConversations,
  useInfiniteConversations,
  useConversation,
  useLiveChats,
} from './use-conversations'
import { queryKeys } from '@/lib/query-keys'

vi.mock('@/lib/api-client', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

import { api } from '@/lib/api-client'

const mockApi = api as unknown as { get: ReturnType<typeof vi.fn>; post: ReturnType<typeof vi.fn> }

const mockConversation = {
  id: 1,
  customer_id: 100,
  customer_name: 'Aziz',
  channel: 'telegram',
  telegram_chat_id: 999,
  pipeline_stage: 'lead',
  needs_attention: false,
  last_message_at: '2026-01-01T10:00:00Z',
  unread_count: 2,
  created_at: '2026-01-01T00:00:00Z',
}

function createWrapper(queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })) {
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: queryClient }, children)
}

describe('useConversations', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fetches and returns conversation items array', async () => {
    mockApi.get.mockResolvedValue({ items: [mockConversation], next_cursor: null })

    const wrapper = createWrapper()
    const { result } = renderHook(() => useConversations(), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(result.current.data).toEqual([mockConversation])
    expect(mockApi.get).toHaveBeenCalledWith('/api/conversations')
  })
})

describe('useInfiniteConversations', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fetches first page with default limit=50', async () => {
    mockApi.get.mockResolvedValue({ items: [mockConversation], next_cursor: null })

    const wrapper = createWrapper()
    const { result } = renderHook(() => useInfiniteConversations(), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(result.current.data?.pages[0].items).toEqual([mockConversation])
    expect(mockApi.get).toHaveBeenCalledWith(expect.stringContaining('limit=50'))
  })

  it('passes contact_type filter in query params', async () => {
    mockApi.get.mockResolvedValue({ items: [], next_cursor: null })

    const wrapper = createWrapper()
    const { result } = renderHook(
      () => useInfiniteConversations({ contact_type: 'customer' }),
      { wrapper },
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockApi.get).toHaveBeenCalledWith(expect.stringContaining('contact_type=customer'))
  })

  it('passes has_pending_reply=true filter in query params', async () => {
    mockApi.get.mockResolvedValue({ items: [], next_cursor: null })

    const wrapper = createWrapper()
    const { result } = renderHook(
      () => useInfiniteConversations({ has_pending_reply: true }),
      { wrapper },
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockApi.get).toHaveBeenCalledWith(expect.stringContaining('has_pending_reply=true'))
  })

  it('getNextPageParam returns next_cursor value', async () => {
    mockApi.get
      .mockResolvedValueOnce({ items: [mockConversation], next_cursor: 'cursor_abc' })
      .mockResolvedValueOnce({ items: [], next_cursor: null })

    const wrapper = createWrapper()
    const { result } = renderHook(() => useInfiniteConversations(), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(result.current.hasNextPage).toBe(true)
  })

  it('revalidates on mount even when cached data is still fresh', async () => {
    mockApi.get.mockResolvedValue({ items: [mockConversation], next_cursor: null })

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const wrapper = createWrapper(queryClient)

    const first = renderHook(() => useInfiniteConversations(), { wrapper })
    await waitFor(() => expect(first.result.current.isSuccess).toBe(true))
    first.unmount()

    const second = renderHook(() => useInfiniteConversations(), { wrapper })
    await waitFor(() => expect(second.result.current.isSuccess).toBe(true))

    expect(mockApi.get).toHaveBeenCalledTimes(2)
  })

  it('drops stale cached tail pages when the refreshed first page has no next cursor', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    queryClient.setQueryData(queryKeys.conversations.list(), {
      pages: [
        {
          items: [mockConversation],
          next_cursor: null,
        },
        {
          items: [
            {
              ...mockConversation,
              id: 99,
              customer_name: 'Operator <3',
            },
          ],
          next_cursor: 'stale_cursor',
        },
      ],
      pageParams: [undefined, 'stale_cursor'],
    })

    mockApi.get.mockResolvedValue({ items: [mockConversation], next_cursor: null })

    const wrapper = createWrapper(queryClient)
    const { result } = renderHook(() => useInfiniteConversations(), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(result.current.data?.pages).toHaveLength(1)
    expect(result.current.data?.pages[0].items).toEqual([mockConversation])
  })

  it('uses different query keys for different filters', () => {
    const keyNoFilter = queryKeys.conversations.list()
    const keyWithFilter = queryKeys.conversations.list({ contact_type: 'customer' })

    // Should be different keys
    expect(keyNoFilter).not.toEqual(keyWithFilter)
    expect(keyWithFilter[2]).toEqual({ contact_type: 'customer' })
  })
})

describe('useConversation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fetches single conversation by id', async () => {
    // Clear mock state from previous describe blocks and return correct value
    mockApi.get.mockReset()
    mockApi.get.mockResolvedValue(mockConversation)

    const wrapper = createWrapper()
    const { result } = renderHook(() => useConversation(1), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(result.current.data).toEqual(mockConversation)
    expect(mockApi.get).toHaveBeenCalledWith('/api/conversations/1')
  })

  it('revalidates detail on mount even when cached data is still fresh', async () => {
    mockApi.get.mockReset()
    mockApi.get.mockResolvedValue(mockConversation)

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const wrapper = createWrapper(queryClient)

    const first = renderHook(() => useConversation(1), { wrapper })
    await waitFor(() => expect(first.result.current.isSuccess).toBe(true))
    first.unmount()

    const second = renderHook(() => useConversation(1), { wrapper })
    await waitFor(() => expect(second.result.current.isSuccess).toBe(true))

    expect(mockApi.get).toHaveBeenCalledTimes(2)
  })

  it('is disabled when id is undefined', () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useConversation(undefined), { wrapper })

    expect(result.current.isLoading).toBe(false)
    expect(result.current.fetchStatus).toBe('idle')
  })
})

describe('useLiveChats', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fetches live chats and returns the chats array', async () => {
    const mockChat = {
      telegram_chat_id: 123,
      telegram_user_id: 456,
      display_name: 'Bobur',
      phone: null,
      unread_count: 1,
      last_message_text: 'Salom',
      last_message_date: null,
      last_message_is_outgoing: false,
      read_outbox_max_id: 0,
      contact_type: 'customer',
      has_ai: true,
      has_pending_reply: false,
      conversation_id: 1,
      customer_id: 100,
    }
    mockApi.get.mockResolvedValue({ chats: [mockChat], count: 1 })

    const wrapper = createWrapper()
    const { result } = renderHook(() => useLiveChats(), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(result.current.data).toEqual([mockChat])
    expect(mockApi.get).toHaveBeenCalledWith('/api/conversations/live')
  })
})
