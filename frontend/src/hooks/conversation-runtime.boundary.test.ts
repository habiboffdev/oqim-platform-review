// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ReactNode } from 'react'
import { useWebSocket } from './use-websocket'
import { getOlderMessagesPageParam, useInfiniteMessages } from './use-infinite-messages'
import { api } from '@/lib/api-client'
import { queryKeys } from '@/lib/query-keys'

const { handlers, mockWsManager } = vi.hoisted(() => {
  type EventHandler = (data: Record<string, unknown>) => void
  const handlers = new Map<string, Set<EventHandler>>()

  const mockWsManager = {
    connect: vi.fn(),
    disconnect: vi.fn(),
    send: vi.fn(),
    on: vi.fn((event: string, handler: EventHandler) => {
      if (!handlers.has(event)) handlers.set(event, new Set())
      handlers.get(event)!.add(handler)
      return () => {
        handlers.get(event)?.delete(handler)
      }
    }),
  }

  return { handlers, mockWsManager }
})

function emitNamedEvent(type: string, data: Record<string, unknown> = {}) {
  const eventHandlers = handlers.get(type)
  if (eventHandlers) {
    eventHandlers.forEach((handler) => handler(data))
  }
}

function emitWsEvent(type: string, data: Record<string, unknown>) {
  const wildcardHandlers = handlers.get('*')
  if (wildcardHandlers) {
    wildcardHandlers.forEach((handler) => handler({ type, ...data }))
  }
}

vi.mock('@/lib/websocket', () => ({
  wsManager: mockWsManager,
}))

vi.mock('@/lib/api-client', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

vi.mock('@/lib/auth-context', () => ({
  useAuth: vi.fn(() => ({ isAuthenticated: true })),
}))

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}))

const mockApi = api as unknown as { get: ReturnType<typeof vi.fn>; post: ReturnType<typeof vi.fn> }

function createWrapper(qc: QueryClient) {
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: qc }, children)
}

describe('conversation runtime boundary', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    handlers.clear()
    mockApi.get.mockReset()
    mockApi.post.mockReset()
  })

  it('opens the active session on authoritative backend tail instead of stale cache', async () => {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    qc.setQueryData(queryKeys.messages.list(38), {
      pages: [{
        items: [{
          id: 1,
          conversation_id: 38,
          sender_type: 'customer',
          content: 'stale cached tail',
          channel: 'telegram',
          is_read: false,
          created_at: '2026-04-15T10:00:00Z',
        }],
        has_older: false,
      }],
      pageParams: [undefined],
    })

    mockApi.get.mockResolvedValueOnce({
      items: [{
        id: 22,
        conversation_id: 38,
        sender_type: 'seller',
        content: 'fresh backend tail',
        channel: 'telegram',
        is_read: true,
        created_at: '2026-04-15T10:08:00Z',
      }],
      has_older: false,
    })

    const { result } = renderHook(() => {
      useWebSocket(38)
      return useInfiniteMessages(38)
    }, {
      wrapper: createWrapper(qc),
    })

    await waitFor(() => {
      expect(result.current.data?.pages[0]?.items.map((message) => message.id)).toEqual([22])
    })
  })

  it('keeps older loaded history while opening a cached active chat refreshes only the tail page', async () => {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    qc.setQueryData(queryKeys.messages.list(38), {
      pages: [
        {
          items: [{
            id: 10,
            conversation_id: 38,
            sender_type: 'customer',
            content: 'older history page',
            channel: 'telegram',
            is_read: true,
            created_at: '2026-04-15T09:00:00Z',
          }],
          has_older: false,
        },
        {
          items: [{
            id: 50,
            conversation_id: 38,
            sender_type: 'customer',
            content: 'stale cached tail',
            channel: 'telegram',
            is_read: false,
            created_at: '2026-04-15T10:00:00Z',
          }],
          has_older: true,
        },
      ],
      pageParams: [10, undefined],
    })

    mockApi.get.mockResolvedValueOnce({
      items: [{
        id: 52,
        conversation_id: 38,
        sender_type: 'seller',
        content: 'fresh backend tail',
        channel: 'telegram',
        is_read: true,
        created_at: '2026-04-15T10:14:00Z',
      }],
      has_older: true,
    })

    renderHook(() => {
      useWebSocket(38)
      return useInfiniteMessages(38)
    }, {
      wrapper: createWrapper(qc),
    })

    await waitFor(() => {
      expect(
        (qc.getQueryData(queryKeys.messages.list(38)) as { pages?: Array<{ items: Array<{ id: number }> }> } | undefined)
          ?.pages?.map((page) => page.items.map((message) => message.id)),
      ).toEqual([[10], [52]])
    })
  })

  it('keeps older loaded history while reconnect recovery refreshes only the tail page', async () => {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })

    mockApi.get.mockResolvedValueOnce({
      items: [{
        id: 50,
        conversation_id: 38,
        sender_type: 'customer',
        content: 'latest tail before reconnect',
        channel: 'telegram',
        is_read: false,
        created_at: '2026-04-15T10:10:00Z',
      }],
      has_older: true,
    })

    const { result } = renderHook(() => {
      useWebSocket(38)
      const messages = useInfiniteMessages(38)
      return {
        hasPreviousPage: messages.hasPreviousPage,
        fetchPreviousPage: messages.fetchPreviousPage,
      }
    }, {
      wrapper: createWrapper(qc),
    })

    await waitFor(() => {
      expect(result.current.hasPreviousPage).toBe(true)
    })

    mockApi.get.mockResolvedValueOnce({
      items: [{
        id: 10,
        conversation_id: 38,
        sender_type: 'customer',
        content: 'older history page',
        channel: 'telegram',
        is_read: true,
        created_at: '2026-04-15T09:00:00Z',
      }],
      has_older: false,
    })

    await act(async () => {
      await result.current.fetchPreviousPage()
    })

    await waitFor(() => {
      expect(
        (qc.getQueryData(queryKeys.messages.list(38)) as { pages?: Array<{ items: Array<{ id: number }> }> } | undefined)
          ?.pages?.map((page) => page.items.map((message) => message.id)),
      ).toEqual([[10], [50]])
    })

    mockApi.get.mockResolvedValueOnce({
      items: [{
        id: 52,
        conversation_id: 38,
        sender_type: 'seller',
        content: 'fresh backend tail after reconnect',
        channel: 'telegram',
        is_read: true,
        created_at: '2026-04-15T10:14:00Z',
      }],
      has_older: true,
    })

    act(() => {
      emitNamedEvent('reconnect')
    })

    act(() => {
      emitWsEvent('sync_response', {
        data: {
          action: 'refresh_scoped_runtime',
          conversation_id: 38,
        },
      })
    })

    await waitFor(() => {
      expect(
        (qc.getQueryData(queryKeys.messages.list(38)) as { pages?: Array<{ items: Array<{ id: number }> }> } | undefined)
          ?.pages?.map((page) => page.items.map((message) => message.id)),
      ).toEqual([[10], [52]])
    })
  })

  it('preserves older media groups while reconnect recovery refreshes active media tail', async () => {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })

    qc.setQueryData(queryKeys.messages.list(38), {
      pages: [
        {
          items: [
            {
              id: 10,
              conversation_id: 38,
              sender_type: 'customer',
              content: '',
              channel: 'telegram',
              is_read: true,
              created_at: '2026-04-15T09:00:00Z',
              media_type: 'photo',
              media_metadata: { thumb: 'older-a' },
              grouped_id: 700,
            },
            {
              id: 11,
              conversation_id: 38,
              sender_type: 'customer',
              content: '',
              channel: 'telegram',
              is_read: true,
              created_at: '2026-04-15T09:00:01Z',
              media_type: 'photo',
              media_metadata: { thumb: 'older-b' },
              grouped_id: 700,
            },
          ],
          has_older: false,
        },
        {
          items: [{
            id: 50,
            conversation_id: 38,
            sender_type: 'customer',
            content: 'stale cached tail',
            channel: 'telegram',
            is_read: false,
            created_at: '2026-04-15T10:00:00Z',
          }],
          has_older: true,
          latest_conversation_seq: 50,
        },
      ],
      pageParams: [10, undefined],
    })

    mockApi.get.mockResolvedValueOnce({
      items: [
        {
          id: 52,
          conversation_id: 38,
          sender_type: 'customer',
          content: '',
          channel: 'telegram',
          is_read: false,
          created_at: '2026-04-15T10:14:00Z',
          media_type: 'photo',
          media_metadata: { thumb: 'tail-a' },
          grouped_id: 900,
          conversation_seq: 52,
        },
        {
          id: 53,
          conversation_id: 38,
          sender_type: 'customer',
          content: '',
          channel: 'telegram',
          is_read: false,
          created_at: '2026-04-15T10:14:01Z',
          media_type: 'photo',
          media_metadata: { thumb: 'tail-b' },
          grouped_id: 900,
          conversation_seq: 53,
        },
      ],
      has_older: true,
      latest_conversation_seq: 53,
    })

    renderHook(() => {
      useWebSocket(38)
      return useInfiniteMessages(38)
    }, {
      wrapper: createWrapper(qc),
    })

    act(() => {
      emitNamedEvent('reconnect')
    })

    act(() => {
      emitWsEvent('sync_response', {
        data: {
          action: 'refresh_scoped_runtime',
          conversation_id: 38,
        },
      })
    })

    await waitFor(() => {
      const data = qc.getQueryData(queryKeys.messages.list(38)) as {
        pages?: Array<{ items: Array<{ id: number; grouped_id?: number; media_metadata?: { thumb?: string } }> }>
      } | undefined
      expect(data?.pages?.map((page) => page.items.map((message) => message.id))).toEqual([[10, 11], [52, 53]])
      expect(data?.pages?.[0]?.items.map((message) => message.grouped_id)).toEqual([700, 700])
      expect(data?.pages?.[0]?.items.map((message) => message.media_metadata?.thumb)).toEqual(['older-a', 'older-b'])
      expect(data?.pages?.[1]?.items.map((message) => message.grouped_id)).toEqual([900, 900])
      expect(data?.pages?.[1]?.items.map((message) => message.media_metadata?.thumb)).toEqual(['tail-a', 'tail-b'])
    })
  })

  it('does not treat a remote Telegram history gap as a local older-page cursor', () => {
    expect(getOlderMessagesPageParam({
      items: [{
        id: 28,
        conversation_id: 38,
        sender_type: 'seller',
        content: '',
        channel: 'telegram',
        is_read: true,
        created_at: '2026-04-18T13:22:25Z',
      }],
      has_older: true,
      history_gap: {
        reason: 'visible_telegram_id_gap',
        before_external_message_id: '915',
        after_external_message_id: null,
      },
    })).toBeUndefined()

    expect(getOlderMessagesPageParam({
      items: [{
        id: 28,
        conversation_id: 38,
        sender_type: 'seller',
        content: '',
        channel: 'telegram',
        is_read: true,
        created_at: '2026-04-18T13:22:25Z',
      }],
      has_older: true,
      history_gap: null,
    })).toBe(28)
  })
})
