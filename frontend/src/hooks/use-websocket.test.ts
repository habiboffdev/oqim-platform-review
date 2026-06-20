// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ReactNode } from 'react'
import { useWebSocket, useShimmerState } from './use-websocket'
import { useInfiniteMessages } from './use-infinite-messages'
import { queryKeys } from '@/lib/query-keys'
import { api } from '@/lib/api-client'
import { resetSyncSessionCursorsForTests } from '@/lib/sync-session'

// vi.hoisted ensures these are initialized before vi.mock factories run
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

// Helper to emit events through wildcard handlers
function emitWsEvent(type: string, data: Record<string, unknown>) {
  const wildcardHandlers = handlers.get('*')
  if (wildcardHandlers) {
    wildcardHandlers.forEach((h) => h({ type, ...data }))
  }
}

function emitNamedEvent(type: string, data: Record<string, unknown> = {}) {
  const eventHandlers = handlers.get(type)
  if (eventHandlers) {
    eventHandlers.forEach((h) => h(data))
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

function createQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
}

function createWrapper(qc: QueryClient) {
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: qc }, children)
}

const mockApi = api as unknown as { get: ReturnType<typeof vi.fn>; post: ReturnType<typeof vi.fn> }

describe('useWebSocket', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    handlers.clear()
    mockApi.get.mockReset()
    mockApi.post.mockReset()
    resetSyncSessionCursorsForTests()
  })

  it('calls wsManager.connect when authenticated', () => {
    const qc = createQueryClient()
    renderHook(() => useWebSocket(), { wrapper: createWrapper(qc) })

    expect(mockWsManager.connect).toHaveBeenCalledTimes(1)
  })

  it('does not connect when not authenticated', async () => {
    const authModule = await import('@/lib/auth-context')
    ;(authModule.useAuth as ReturnType<typeof vi.fn>).mockReturnValueOnce({ isAuthenticated: false })

    const qc = createQueryClient()
    renderHook(() => useWebSocket(), { wrapper: createWrapper(qc) })

    expect(mockWsManager.connect).not.toHaveBeenCalled()
  })

  it('handles new_message event by invalidating conversations', () => {
    const qc = createQueryClient()
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries')

    renderHook(() => useWebSocket(), { wrapper: createWrapper(qc) })

    act(() => {
      emitWsEvent('new_message', { conversation_id: 1 })
    })

    // Should invalidate conversations (no conversation object = full invalidation)
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: queryKeys.conversations.all }),
    )
  })

  it('handles new_message with conversation object by updating cache', () => {
    const qc = createQueryClient()
    const setQueriesDataSpy = vi.spyOn(qc, 'setQueriesData')

    renderHook(() => useWebSocket(), { wrapper: createWrapper(qc) })

    const updatedConv = {
      id: 5,
      customer_name: 'Test',
      last_message_at: '2026-01-01T00:00:00Z',
    }

    act(() => {
      emitWsEvent('new_message', { conversation_id: 5, conversation: updatedConv })
    })

    expect(setQueriesDataSpy).toHaveBeenCalled()
  })

  it('trims stale cached conversation tail pages while applying live conversation updates', () => {
    const qc = createQueryClient()
    qc.setQueryData(queryKeys.conversations.list(), {
      pages: [
        {
          items: [{
            id: 5,
            customer_id: 5,
            customer_name: 'Fresh chat',
            channel: 'telegram_dm',
            telegram_chat_id: 5,
            pipeline_stage: 'new',
            needs_attention: false,
            last_message_at: '2026-04-23T08:00:00Z',
            unread_count: 0,
            created_at: '2026-04-23T08:00:00Z',
          }],
          next_cursor: null,
        },
        {
          items: [{
            id: 99,
            customer_id: 99,
            customer_name: 'Operator <3',
            channel: 'telegram_dm',
            telegram_chat_id: 99,
            pipeline_stage: 'new',
            needs_attention: false,
            last_message_at: '2026-04-20T08:00:00Z',
            unread_count: 0,
            created_at: '2026-04-20T08:00:00Z',
          }],
          next_cursor: 'stale_cursor',
        },
      ],
      pageParams: [undefined, 'stale_cursor'],
    })

    renderHook(() => useWebSocket(), { wrapper: createWrapper(qc) })

    act(() => {
      emitWsEvent('new_message', {
        conversation_id: 5,
        conversation: {
          id: 5,
          customer_id: 5,
          customer_name: 'Fresh chat',
          channel: 'telegram_dm',
          telegram_chat_id: 5,
          pipeline_stage: 'new',
          needs_attention: false,
          last_message_at: '2026-04-23T09:00:00Z',
          unread_count: 0,
          created_at: '2026-04-23T08:00:00Z',
        },
      })
    })

    const data = qc.getQueryData(queryKeys.conversations.list()) as {
      pages: Array<{ items: Array<{ id: number }>; next_cursor: string | null }>
    } | undefined

    expect(data?.pages).toHaveLength(1)
    expect(data?.pages[0]?.items.map((conversation) => conversation.id)).toEqual([5])
  })

  it('replaces an optimistic seller message when a live new_message carries the same client uuid', () => {
    const qc = createQueryClient()
    qc.setQueryData(queryKeys.messages.list(38), {
      pages: [{
        items: [{
          id: -1,
          conversation_id: 38,
          sender_type: 'seller',
          content: 'optimistic send',
          channel: 'telegram_dm',
          is_read: true,
          created_at: '2026-04-15T10:00:00Z',
          client_message_uuid: 'send-uuid-live-1',
        }],
        has_older: false,
      }],
      pageParams: [undefined],
    })

    renderHook(() => useWebSocket(38), { wrapper: createWrapper(qc) })

    act(() => {
      emitWsEvent('new_message', {
        conversation_id: 38,
        conversation: {
          id: 38,
          customer_name: 'Test',
          last_message_at: '2026-04-15T10:00:01Z',
        },
        message: {
          id: 501,
          telegram_message_id: 91001,
          sender_type: 'seller',
          content: 'optimistic send',
          created_at: '2026-04-15T10:00:01Z',
          client_message_uuid: 'send-uuid-live-1',
          delivery_state: 'confirmed',
          delivery_runtime: {
            schema_version: 'delivery_runtime.v1',
            state: 'reconciled',
            customer_status: 'sent',
            next_action: 'none',
            is_terminal: true,
            requires_reconciliation: false,
            can_retry: false,
            attempt_count: 1,
            max_attempts: 3,
            retry_budget_remaining: 2,
            external_message_id: '91001',
          },
          conversation_revision: 1,
        },
      })
    })

    const data = qc.getQueryData(queryKeys.messages.list(38)) as {
      pages?: Array<{
        latest_conversation_revision?: number
        items: Array<{ id: number; client_message_uuid?: string }>
      }>
    } | undefined

    expect(data?.pages?.[0]?.items).toEqual([
      expect.objectContaining({
        id: 501,
        client_message_uuid: 'send-uuid-live-1',
        delivery_state: 'confirmed',
        delivery_runtime: expect.objectContaining({
          customer_status: 'sent',
          state: 'reconciled',
        }),
      }),
    ])
    expect(data?.pages?.[0]?.latest_conversation_revision).toBe(1)
  })

  it('handles ai_reply_created with full entity by updating cache directly', () => {
    const qc = createQueryClient()
    // Pre-populate the cache with an existing reply
    qc.setQueryData(queryKeys.sellerAgentReplies.byConversation(3), [])

    const setQueryDataSpy = vi.spyOn(qc, 'setQueryData')

    renderHook(() => useWebSocket(), { wrapper: createWrapper(qc) })

    const newReply = { id: 20, conversation_id: 3, status: 'draft' }

    act(() => {
      emitWsEvent('ai_reply_created', {
        conversation_id: 3,
        ai_reply: newReply,
      })
    })

    expect(setQueryDataSpy).toHaveBeenCalledWith(
      queryKeys.sellerAgentReplies.byConversation(3),
      expect.any(Function),
    )
  })

  it('handles ai_reply_created by invalidating sellerAgentReplyInbox', () => {
    const qc = createQueryClient()
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries')

    renderHook(() => useWebSocket(), { wrapper: createWrapper(qc) })

    act(() => {
      emitWsEvent('ai_reply_created', { conversation_id: 3 })
    })

    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: queryKeys.sellerAgentReplyInbox }),
    )
  })

  it('handles typing event by setting query data', () => {
    const qc = createQueryClient()
    const setQueryDataSpy = vi.spyOn(qc, 'setQueryData')

    renderHook(() => useWebSocket(), { wrapper: createWrapper(qc) })

    act(() => {
      emitWsEvent('typing', { conversation_id: 7, is_typing: true })
    })

    expect(setQueryDataSpy).toHaveBeenCalledWith(
      ['typing', 7],
      expect.objectContaining({ isTyping: true }),
    )
  })

  it('handles read_outbox event by updating live chats cache', () => {
    const qc = createQueryClient()
    const setQueriesDataSpy = vi.spyOn(qc, 'setQueriesData')

    renderHook(() => useWebSocket(), { wrapper: createWrapper(qc) })

    act(() => {
      emitWsEvent('read_outbox', {
        conversation_id: 9,
        read_outbox_max_id: 42,
      })
    })

    expect(setQueriesDataSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: queryKeys.liveChats }),
      expect.any(Function),
    )
  })

  it('keeps the singleton websocket alive on hook unmount', () => {
    const qc = createQueryClient()
    const { unmount } = renderHook(() => useWebSocket(), { wrapper: createWrapper(qc) })

    unmount()

    expect(mockWsManager.disconnect).not.toHaveBeenCalled()
  })

  it('registers and unregisters the active chat from route authority', () => {
    const qc = createQueryClient()
    const { rerender, unmount } = renderHook(
      ({ conversationId }: { conversationId?: number }) => useWebSocket(conversationId),
      {
        wrapper: createWrapper(qc),
        initialProps: { conversationId: 38 },
      },
    )

    expect(mockWsManager.send).toHaveBeenCalledWith({
      type: 'chat_opened',
      conversation_id: 38,
    })

    rerender({ conversationId: 31 })

    expect(mockWsManager.send).toHaveBeenCalledWith({
      type: 'chat_closed',
      conversation_id: 38,
    })
    expect(mockWsManager.send).toHaveBeenCalledWith({
      type: 'chat_opened',
      conversation_id: 31,
    })

    unmount()

    expect(mockWsManager.send).toHaveBeenCalledWith({
      type: 'chat_closed',
      conversation_id: 31,
    })
  })

  it('reconciles the active chat tail from the backend after a live message event', async () => {
    const qc = createQueryClient()
    qc.setQueryData(queryKeys.messages.list(38), {
      pages: [{
        items: [{
          id: 1,
          conversation_id: 38,
          sender_type: 'customer',
          content: 'old cached message',
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
        id: 1,
        conversation_id: 38,
        sender_type: 'customer',
        content: 'old cached message',
        channel: 'telegram',
        is_read: false,
        created_at: '2026-04-15T10:00:00Z',
      }],
      has_older: false,
    })

    const { result } = renderHook(() => {
      useWebSocket(38)
      const messages = useInfiniteMessages(38)
      return {
        data: messages.data,
        hasPreviousPage: messages.hasPreviousPage,
        fetchPreviousPage: messages.fetchPreviousPage,
      }
    }, { wrapper: createWrapper(qc) })

    await act(async () => {
      await Promise.resolve()
    })

    mockApi.get.mockResolvedValueOnce({
      items: [{
        id: 9,
        conversation_id: 38,
        sender_type: 'seller',
        content: 'fresh backend tail',
        channel: 'telegram',
        is_read: true,
        created_at: '2026-04-15T10:05:00Z',
      }],
      has_older: false,
    })

    act(() => {
      emitWsEvent('new_message', {
        conversation_id: 38,
        message: {
          id: 13,
          telegram_message_id: 309242,
          sender_type: 'customer',
          content: 'live append only',
          created_at: '2026-04-15T10:06:00Z',
        },
      })
    })

    await waitFor(() => {
      expect(result.current.data?.pages[0]?.items.map((message) => message.id)).toEqual([9])
    })
  })

  it('reconciles the active chat tail from the backend after websocket reconnect', async () => {
    const qc = createQueryClient()
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
          conversation_seq: 7,
        }],
        has_older: false,
        latest_conversation_seq: 7,
        latest_conversation_revision: 7,
      }],
      pageParams: [undefined],
    })

    mockApi.get.mockResolvedValueOnce({
      items: [{
        id: 1,
        conversation_id: 38,
        sender_type: 'customer',
        content: 'stale cached tail',
        channel: 'telegram',
        is_read: false,
        created_at: '2026-04-15T10:00:00Z',
        conversation_seq: 7,
      }],
      has_older: false,
      latest_conversation_seq: 7,
      latest_conversation_revision: 7,
    })

    const { result } = renderHook(() => {
      useWebSocket(38)
      return useInfiniteMessages(38)
    }, { wrapper: createWrapper(qc) })

    await act(async () => {
      await Promise.resolve()
    })

    mockApi.get.mockResolvedValueOnce({
      items: [{
        id: 22,
        conversation_id: 38,
        sender_type: 'seller',
        content: 'fresh after reconnect',
        channel: 'telegram',
        is_read: true,
        created_at: '2026-04-15T10:08:00Z',
        conversation_seq: 12,
      }],
      has_older: false,
      latest_conversation_seq: 12,
    })

    act(() => {
      emitNamedEvent('reconnect')
    })

    expect(mockWsManager.send).toHaveBeenCalledWith({
      type: 'session.resume',
      last_sequence: 0,
      conversation_id: 38,
      last_seen_conversation_seq: 7,
      last_seen_conversation_revision: 7,
    })
    expect(mockWsManager.send).toHaveBeenCalledWith({
      type: 'chat_opened',
      conversation_id: 38,
    })

    act(() => {
      emitWsEvent('sync_response', {
        data: { action: 'refresh_scoped_runtime', conversation_id: 38 },
        sequence_id: 22,
      })
    })

    await waitFor(() => {
      expect(result.current.data?.pages[0]?.items.map((message) => message.id)).toEqual([22])
    })
  })

  it('preserves older history after the active chat tail reconciles from reconnect', async () => {
    const qc = createQueryClient()

    mockApi.get.mockResolvedValueOnce({
      items: [{
        id: 50,
        conversation_id: 38,
        sender_type: 'customer',
        content: 'latest tail before reconnect',
        channel: 'telegram',
        is_read: false,
        created_at: '2026-04-15T10:10:00Z',
        conversation_seq: 50,
      }],
      has_older: true,
      latest_conversation_seq: 50,
      latest_conversation_revision: 50,
    })

    const { result } = renderHook(() => {
      useWebSocket(38)
      const messages = useInfiniteMessages(38)
      return {
        data: messages.data,
        hasPreviousPage: messages.hasPreviousPage,
        fetchPreviousPage: messages.fetchPreviousPage,
      }
    }, { wrapper: createWrapper(qc) })

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
        conversation_seq: 10,
      }],
      has_older: false,
      latest_conversation_seq: 10,
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
        conversation_seq: 52,
      }],
      has_older: true,
      latest_conversation_seq: 52,
    })

    act(() => {
      emitNamedEvent('reconnect')
    })

    expect(mockWsManager.send).toHaveBeenCalledWith({
      type: 'session.resume',
      last_sequence: 0,
      conversation_id: 38,
      last_seen_conversation_seq: 50,
      last_seen_conversation_revision: 50,
    })
    expect(mockWsManager.send).toHaveBeenCalledWith({
      type: 'chat_opened',
      conversation_id: 38,
    })

    act(() => {
      emitWsEvent('sync_response', {
        data: { action: 'refresh_scoped_runtime', conversation_id: 38 },
        sequence_id: 23,
      })
    })

    await waitFor(() => {
      expect(
        (qc.getQueryData(queryKeys.messages.list(38)) as { pages?: Array<{ items: Array<{ id: number }> }> } | undefined)
          ?.pages?.map((page) => page.items.map((message) => message.id)),
      ).toEqual([[10], [52]])
    })
  })

  it('patches only the newer active-chat tail when reconnect sync provides a bounded conversation-seq delta', async () => {
    const qc = createQueryClient()

    qc.setQueryData(queryKeys.messages.list(38), {
      pages: [{
        items: [{
          id: 10,
          conversation_id: 38,
          sender_type: 'customer',
          content: 'older history page',
          channel: 'telegram',
          is_read: true,
          created_at: '2026-04-15T09:00:00Z',
          conversation_seq: 10,
        }],
        has_older: false,
        latest_conversation_seq: 10,
        latest_conversation_revision: 10,
      }, {
        items: [{
          id: 50,
          conversation_id: 38,
          sender_type: 'customer',
          content: 'latest cached tail before reconnect',
          channel: 'telegram',
          is_read: false,
          created_at: '2026-04-15T10:10:00Z',
          conversation_seq: 50,
        }],
        has_older: true,
        latest_conversation_seq: 50,
        latest_conversation_revision: 50,
      }],
      pageParams: [10, undefined],
    })

    renderHook(() => useWebSocket(38), { wrapper: createWrapper(qc) })

    mockApi.get.mockResolvedValueOnce({
      items: [{
        id: 52,
        conversation_id: 38,
        sender_type: 'seller',
        content: 'fresh backend tail delta',
        channel: 'telegram',
        is_read: true,
        created_at: '2026-04-15T10:14:00Z',
        conversation_seq: 52,
      }],
      has_older: false,
      latest_conversation_seq: 52,
      latest_conversation_revision: 52,
    })

    act(() => {
      emitWsEvent('sync_response', {
        data: {
          action: 'refresh_scoped_runtime_delta',
          conversation_id: 38,
          after_conversation_seq: 50,
          latest_conversation_seq: 52,
          latest_conversation_revision: 52,
        },
        sequence_id: 23,
      })
    })

    await waitFor(() => {
      expect(mockApi.get).toHaveBeenCalledWith(
        '/api/conversations/38/messages?limit=200&after_conversation_seq=50',
      )
      expect(
        (qc.getQueryData(queryKeys.messages.list(38)) as { pages?: Array<{ items: Array<{ id: number }> }> } | undefined)
          ?.pages?.map((page) => page.items.map((message) => message.id)),
      ).toEqual([[10], [50, 52]])
    })
  })

  it('applies sync-session projection deltas instead of global cache-truth invalidation', async () => {
    const qc = createQueryClient()
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries')

    qc.setQueryData(queryKeys.messages.list(38), {
      pages: [{
        items: [{
          id: 50,
          conversation_id: 38,
          sender_type: 'customer',
          content: 'latest cached tail before reconnect',
          channel: 'telegram',
          is_read: false,
          created_at: '2026-04-15T10:10:00Z',
          conversation_seq: 50,
        }],
        has_older: true,
        latest_conversation_seq: 50,
        latest_conversation_revision: 50,
      }],
      pageParams: [undefined],
    })

    renderHook(() => useWebSocket(38), { wrapper: createWrapper(qc) })

    mockApi.get.mockResolvedValueOnce({
      items: [{
        id: 51,
        conversation_id: 38,
        sender_type: 'seller',
        content: 'projection delta tail',
        channel: 'telegram',
        is_read: true,
        created_at: '2026-04-15T10:14:00Z',
        conversation_seq: 51,
      }],
      has_older: false,
      latest_conversation_seq: 51,
      latest_conversation_revision: 51,
    })

    act(() => {
      emitWsEvent('session.delta', {
        data: {
          kind: 'delta',
          action: 'refresh_scoped_runtime_delta',
          conversation_id: 38,
          projections: [
            {
              name: 'messages',
              mode: 'delta',
              conversation_id: 38,
              after_conversation_seq: 50,
            },
            { name: 'conversation_state', mode: 'reset', conversation_id: 38 },
            { name: 'seller_agent_replies', mode: 'reset', conversation_id: 38 },
            { name: 'read_state', mode: 'reset', conversation_id: 38 },
          ],
        },
        sequence_id: 24,
      })
    })

    await waitFor(() => {
      expect(mockApi.get).toHaveBeenCalledWith(
        '/api/conversations/38/messages?limit=200&after_conversation_seq=50',
      )
      expect(
        (qc.getQueryData(queryKeys.messages.list(38)) as { pages?: Array<{ items: Array<{ id: number }> }> } | undefined)
          ?.pages?.[0]?.items.map((message) => message.id),
      ).toEqual([50, 51])
    })

    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: queryKeys.conversations.detail(38) }),
    )
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: queryKeys.sellerAgentReplies.byConversation(38) }),
    )
  })

  it('preserves older history after the active chat tail reconciles from a live event', async () => {
    const qc = createQueryClient()

    mockApi.get.mockResolvedValueOnce({
      items: [{
        id: 50,
        conversation_id: 38,
        sender_type: 'customer',
        content: 'latest tail before scroll up',
        channel: 'telegram',
        is_read: false,
        created_at: '2026-04-15T10:10:00Z',
      }],
      has_older: true,
    })

    const { result } = renderHook(() => {
      useWebSocket(38)
      return useInfiniteMessages(38)
    }, { wrapper: createWrapper(qc) })

    await waitFor(() => {
      expect(result.current.data?.pages).toHaveLength(1)
    })
    expect(result.current.hasPreviousPage).toBe(true)

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

    expect(mockApi.get).toHaveBeenNthCalledWith(
      2,
      '/api/conversations/38/messages?limit=50&before_id=50',
    )
    expect(mockApi.get).toHaveBeenCalledTimes(2)

    await waitFor(() => {
      expect(
        (qc.getQueryData(queryKeys.messages.list(38)) as { pages?: Array<{ items: Array<{ id: number }> }> } | undefined)
          ?.pages?.map((page) => page.items.map((message) => message.id)),
      ).toEqual([[10], [50]])
    })

    mockApi.get.mockResolvedValueOnce({
      items: [{
        id: 51,
        conversation_id: 38,
        sender_type: 'seller',
        content: 'fresh backend tail after live event',
        channel: 'telegram',
        is_read: true,
        created_at: '2026-04-15T10:12:00Z',
      }],
      has_older: true,
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

    act(() => {
      emitWsEvent('new_message', {
        conversation_id: 38,
        message: {
          id: 99,
          telegram_message_id: 309299,
          sender_type: 'customer',
          content: 'live event that should not collapse history',
          created_at: '2026-04-15T10:13:00Z',
        },
      })
    })

    await waitFor(() => {
      expect(
        (qc.getQueryData(queryKeys.messages.list(38)) as { pages?: Array<{ items: Array<{ id: number }> }> } | undefined)
          ?.pages?.map((page) => page.items.map((message) => message.id)),
      ).toEqual([[10], [51]])
    })
  })

  it('scopes sync recovery to runtime caches instead of invalidating the whole query client', async () => {
    const qc = createQueryClient()
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries')

    qc.setQueryData(queryKeys.messages.list(38), {
      pages: [{
        items: [{
          id: 7,
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
        id: 7,
        conversation_id: 38,
        sender_type: 'customer',
        content: 'stale cached tail',
        channel: 'telegram',
        is_read: false,
        created_at: '2026-04-15T10:00:00Z',
        conversation_seq: 7,
      }],
      has_older: false,
      latest_conversation_seq: 7,
    })

    const { result } = renderHook(() => {
      useWebSocket(38)
      return useInfiniteMessages(38)
    }, { wrapper: createWrapper(qc) })

    await act(async () => {
      await Promise.resolve()
    })

    mockApi.get.mockResolvedValueOnce({
      items: [{
        id: 44,
        conversation_id: 38,
        sender_type: 'seller',
        content: 'fresh backend tail after scoped sync',
        channel: 'telegram',
        is_read: true,
        created_at: '2026-04-15T10:09:00Z',
      }],
      has_older: false,
    })

    act(() => {
      emitWsEvent('sync_response', {
        data: { action: 'refresh_scoped_runtime', conversation_id: 38 },
        sequence_id: 19,
      })
    })

    await waitFor(() => {
      expect(result.current.data?.pages[0]?.items.map((message) => message.id)).toEqual([44])
    })

    expect(invalidateSpy).not.toHaveBeenCalledWith()
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: queryKeys.conversations.all }),
    )
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: queryKeys.liveChats }),
    )
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: queryKeys.sellerAgentReplyInbox }),
    )
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: queryKeys.sellerAgentReplies.byConversation(38) }),
    )
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: queryKeys.conversations.detail(38) }),
    )
  })

  it('preserves grouped media metadata after reconnect tail reconciliation', async () => {
    const qc = createQueryClient()

    mockApi.get.mockResolvedValueOnce({
      items: [{
        id: 50,
        conversation_id: 38,
        sender_type: 'customer',
        content: '[photo]',
        channel: 'telegram',
        is_read: false,
        media_type: 'photo',
        media_url: '/api/media/38/50',
        media_full_url: '/api/media/38/50',
        media_preview_url: '/api/media/38/50?thumb=true',
        grouped_id: 700,
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
    }, { wrapper: createWrapper(qc) })

    await waitFor(() => {
      expect(result.current.hasPreviousPage).toBe(true)
    })

    mockApi.get.mockResolvedValueOnce({
      items: [{
        id: 10,
        conversation_id: 38,
        sender_type: 'customer',
        content: 'older text page',
        channel: 'telegram',
        is_read: true,
        created_at: '2026-04-15T09:00:00Z',
      }],
      has_older: false,
    })

    await act(async () => {
      await result.current.fetchPreviousPage()
    })

    mockApi.get.mockResolvedValueOnce({
      items: [
        {
          id: 51,
          conversation_id: 38,
          sender_type: 'seller',
          content: '[photo]',
          channel: 'telegram',
          is_read: true,
          media_type: 'photo',
          media_url: '/api/media/38/51',
          media_full_url: '/api/media/38/51',
          media_preview_url: '/api/media/38/51?thumb=true',
          grouped_id: 900,
          created_at: '2026-04-15T10:14:00Z',
        },
        {
          id: 52,
          conversation_id: 38,
          sender_type: 'seller',
          content: '[photo]',
          channel: 'telegram',
          is_read: true,
          media_type: 'photo',
          media_url: '/api/media/38/52',
          media_full_url: '/api/media/38/52',
          media_preview_url: '/api/media/38/52?thumb=true',
          grouped_id: 900,
          created_at: '2026-04-15T10:14:05Z',
        },
      ],
      has_older: true,
    })

    act(() => {
      emitNamedEvent('reconnect')
    })

    act(() => {
      emitWsEvent('sync_response', {
        data: { action: 'refresh_scoped_runtime', conversation_id: 38 },
        sequence_id: 24,
      })
    })

    await waitFor(() => {
      const data = qc.getQueryData(queryKeys.messages.list(38)) as {
        pages?: Array<{ items: Array<{
          id: number
          grouped_id?: number
          media_type?: string
          media_preview_url?: string
        }> }>
      } | undefined

      expect(data?.pages?.map((page) => page.items.map((message) => message.id))).toEqual([[10], [51, 52]])
      expect(data?.pages?.[1]?.items).toEqual([
        expect.objectContaining({
          id: 51,
          grouped_id: 900,
          media_type: 'photo',
          media_preview_url: '/api/media/38/51?thumb=true',
        }),
        expect.objectContaining({
          id: 52,
          grouped_id: 900,
          media_type: 'photo',
          media_preview_url: '/api/media/38/52?thumb=true',
        }),
      ])
    })
  })

  it('applies canonical conversation state from reconnect sync to cached conversation lists', () => {
    const qc = createQueryClient()
    qc.setQueryData(queryKeys.conversations.list(), {
      pages: [{
        items: [{
          id: 38,
          customer_id: 38,
          customer_name: 'Azim',
          channel: 'telegram_dm',
          telegram_chat_id: 38,
          pipeline_stage: 'new',
          needs_attention: false,
          last_message_at: '2026-04-23T08:00:00Z',
          unread_count: 9,
          last_message_text: 'stale cached preview',
          created_at: '2026-04-23T08:00:00Z',
        }],
        next_cursor: null,
      }],
      pageParams: [undefined],
    })

    renderHook(() => useWebSocket(38), { wrapper: createWrapper(qc) })

    act(() => {
      emitWsEvent('sync_response', {
        data: {
          action: 'refresh_scoped_runtime_delta',
          conversation_id: 38,
          latest_conversation_seq: 52,
          latest_conversation_revision: 52,
          conversation_state: {
            last_message_text: 'canonical reconnect preview',
            unread_count: 2,
          },
        },
        sequence_id: 23,
      })
    })

    const data = qc.getQueryData(queryKeys.conversations.list()) as {
      pages: Array<{ items: Array<{ id: number; last_message_text?: string; unread_count: number }> }>
    }
    expect(data.pages[0]?.items[0]).toEqual(expect.objectContaining({
      id: 38,
      last_message_text: 'canonical reconnect preview',
      unread_count: 2,
    }))
  })

  it('replaces an optimistic seller send when reconnect tail returns the matching client uuid', async () => {
    const qc = createQueryClient()
    qc.setQueryData(queryKeys.messages.list(38), {
      pages: [{
        items: [{
          id: -1,
          conversation_id: 38,
          sender_type: 'seller',
          content: 'optimistic send',
          channel: 'telegram_dm',
          is_read: true,
          created_at: '2026-04-15T10:00:00Z',
          client_message_uuid: 'send-uuid-1',
        }],
        has_older: false,
      }],
      pageParams: [undefined],
    })

    mockApi.get.mockResolvedValueOnce({
      items: [{
        id: -1,
        conversation_id: 38,
        sender_type: 'seller',
        content: 'optimistic send',
        channel: 'telegram_dm',
        is_read: true,
        created_at: '2026-04-15T10:00:00Z',
        client_message_uuid: 'send-uuid-1',
      }],
      has_older: false,
    })

    const { result } = renderHook(() => {
      useWebSocket(38)
      return useInfiniteMessages(38)
    }, { wrapper: createWrapper(qc) })

    await act(async () => {
      await Promise.resolve()
    })

    mockApi.get.mockResolvedValueOnce({
      items: [{
        id: 205,
        conversation_id: 38,
        sender_type: 'seller',
        content: 'optimistic send',
        channel: 'telegram_dm',
        is_read: true,
        created_at: '2026-04-15T10:00:01Z',
        client_message_uuid: 'send-uuid-1',
      }],
      has_older: false,
    })

    act(() => {
      emitNamedEvent('reconnect')
    })

    act(() => {
      emitWsEvent('sync_response', {
        data: { action: 'refresh_scoped_runtime', conversation_id: 38 },
        sequence_id: 25,
      })
    })

    await waitFor(() => {
      expect(result.current.data?.pages[0]?.items).toEqual([
        expect.objectContaining({
          id: 205,
          client_message_uuid: 'send-uuid-1',
        }),
      ])
    })
  })

  it('preserves an unresolved optimistic seller send when reconnect tail has not caught up yet', async () => {
    const qc = createQueryClient()
    qc.setQueryData(queryKeys.messages.list(38), {
      pages: [{
        items: [{
          id: -1,
          conversation_id: 38,
          sender_type: 'seller',
          content: 'still pending locally',
          channel: 'telegram_dm',
          is_read: true,
          created_at: '2026-04-15T10:00:00Z',
          client_message_uuid: 'send-uuid-2',
        }],
        has_older: false,
      }],
      pageParams: [undefined],
    })

    mockApi.get.mockResolvedValueOnce({
      items: [{
        id: -1,
        conversation_id: 38,
        sender_type: 'seller',
        content: 'still pending locally',
        channel: 'telegram_dm',
        is_read: true,
        created_at: '2026-04-15T10:00:00Z',
        client_message_uuid: 'send-uuid-2',
      }],
      has_older: false,
    })

    const { result } = renderHook(() => {
      useWebSocket(38)
      return useInfiniteMessages(38)
    }, { wrapper: createWrapper(qc) })

    await act(async () => {
      await Promise.resolve()
    })

    mockApi.get.mockResolvedValueOnce({
      items: [{
        id: 300,
        conversation_id: 38,
        sender_type: 'customer',
        content: 'authoritative customer tail',
        channel: 'telegram_dm',
        is_read: false,
        created_at: '2026-04-15T10:00:01Z',
      }],
      has_older: false,
    })

    act(() => {
      emitNamedEvent('reconnect')
    })

    act(() => {
      emitWsEvent('sync_response', {
        data: { action: 'refresh_scoped_runtime', conversation_id: 38 },
        sequence_id: 26,
      })
    })

    await waitFor(() => {
      expect(result.current.data?.pages[0]?.items).toEqual([
        expect.objectContaining({ id: 300 }),
        expect.objectContaining({
          id: -1,
          client_message_uuid: 'send-uuid-2',
          content: 'still pending locally',
        }),
      ])
    })
  })

  it('reconnect sync without an active conversation keeps the global recovery request shape', () => {
    const qc = createQueryClient()

    renderHook(() => useWebSocket(), { wrapper: createWrapper(qc) })

    act(() => {
      emitNamedEvent('reconnect')
    })

    expect(mockWsManager.send).toHaveBeenCalledWith({
      type: 'session.resume',
      last_sequence: 0,
    })
  })

  it('does not build reconnect cursors from local message-cache guesses', () => {
    const qc = createQueryClient()
    qc.setQueryData(queryKeys.messages.list(38), {
      pages: [{
        items: [{
          id: 99,
          conversation_id: 38,
          sender_type: 'customer',
          content: 'local-only cache guess',
          channel: 'telegram',
          is_read: false,
          created_at: '2026-04-15T10:00:00Z',
          conversation_seq: 999,
        }],
        has_older: false,
        latest_conversation_seq: 999,
        latest_conversation_revision: 999,
      }],
      pageParams: [undefined],
    })

    renderHook(() => useWebSocket(38), { wrapper: createWrapper(qc) })

    act(() => {
      emitNamedEvent('reconnect')
    })

    expect(mockWsManager.send).toHaveBeenCalledWith({
      type: 'session.resume',
      last_sequence: 0,
      conversation_id: 38,
    })
    expect(mockWsManager.send).not.toHaveBeenCalledWith(
      expect.objectContaining({
        type: 'session.resume',
        last_seen_conversation_seq: 999,
      }),
    )
  })

  it('does not infer conversation_revision from a live message conversation_seq', () => {
    const qc = createQueryClient()
    mockApi.get.mockResolvedValue({
      items: [],
      has_older: false,
    })

    renderHook(() => useWebSocket(38), { wrapper: createWrapper(qc) })

    act(() => {
      emitWsEvent('new_message', {
        conversation_id: 38,
        message: {
          id: 1001,
          telegram_message_id: 1001,
          sender_type: 'customer',
          content: 'backend sent seq only',
          created_at: '2026-04-15T10:00:00Z',
          conversation_seq: 999,
        },
      })
    })

    act(() => {
      emitNamedEvent('reconnect')
    })

    expect(mockWsManager.send).toHaveBeenCalledWith({
      type: 'session.resume',
      last_sequence: 0,
      conversation_id: 38,
      last_seen_conversation_seq: 999,
    })
    expect(mockWsManager.send).not.toHaveBeenCalledWith(
      expect.objectContaining({
        type: 'session.resume',
        last_seen_conversation_revision: 999,
      }),
    )
  })

  it('preserves video note media semantics on live active-chat updates', async () => {
    const qc = createQueryClient()

    qc.setQueryData(queryKeys.messages.list(38), {
      pages: [{
        items: [],
        has_older: false,
      }],
      pageParams: [undefined],
    })

    renderHook(() => useWebSocket(38), { wrapper: createWrapper(qc) })

    act(() => {
      emitWsEvent('new_message', {
        conversation_id: 38,
        message: {
          id: 77,
          telegram_message_id: 309377,
          sender_type: 'customer',
          content: '[video_note]',
          created_at: '2026-04-15T10:20:00Z',
          media_type: 'video_note',
          media_url: '/api/media/38/309377',
          media_full_url: '/api/media/38/309377',
          media_preview_url: '/api/media/38/309377?thumb=true',
          media_metadata: { length: 240, duration_seconds: 14 },
        },
      })
    })

    const data = qc.getQueryData(queryKeys.messages.list(38)) as {
      pages?: Array<{ items: Array<{
        id: number
        media_type?: string
        media_full_url?: string
        media_preview_url?: string
        media_metadata?: Record<string, unknown>
      }> }>
    } | undefined

    expect(data?.pages?.[0]?.items).toEqual([
      expect.objectContaining({
        id: 77,
        media_type: 'video_note',
        media_full_url: '/api/media/38/309377',
        media_preview_url: '/api/media/38/309377?thumb=true',
        media_metadata: expect.objectContaining({ length: 240, duration_seconds: 14 }),
      }),
    ])
  })

  it('reconciles the active chat from the backend after a delete mutation event', async () => {
    const qc = createQueryClient()
    qc.setQueryData(queryKeys.messages.list(38), {
      pages: [{
        items: [{
          id: 11,
          conversation_id: 38,
          sender_type: 'customer',
          content: 'message before delete',
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
        id: 11,
        conversation_id: 38,
        sender_type: 'customer',
        content: 'message before delete',
        channel: 'telegram',
        is_read: false,
        created_at: '2026-04-15T10:00:00Z',
      }],
      has_older: false,
    })

    renderHook(() => {
      useWebSocket(38)
      return useInfiniteMessages(38)
    }, { wrapper: createWrapper(qc) })

    await act(async () => {
      await Promise.resolve()
    })

    mockApi.get.mockResolvedValueOnce({
      items: [{
        id: 11,
        conversation_id: 38,
        sender_type: 'customer',
        content: '[deleted]',
        channel: 'telegram',
        is_read: false,
        created_at: '2026-04-15T10:00:00Z',
      }],
      has_older: false,
      latest_conversation_revision: 7,
    })

    act(() => {
      emitWsEvent('message_deleted', {
        conversation_id: 38,
        message_id: 11,
        conversation_revision: 11,
      })
    })

    await waitFor(() => {
      expect(
        (qc.getQueryData(queryKeys.messages.list(38)) as { pages?: Array<{ items: Array<{ id: number; content: string }> }> } | undefined)
          ?.pages?.[0]?.items,
      ).toEqual([
        expect.objectContaining({
          id: 11,
          content: '[deleted]',
        }),
      ])
      expect(
        (qc.getQueryData(queryKeys.messages.list(38)) as {
          pages?: Array<{ latest_conversation_revision?: number }>
        } | undefined)?.pages?.[0]?.latest_conversation_revision,
      ).toBe(11)
    })
  })

  it('patches the active chat message after an edit mutation event', async () => {
    const qc = createQueryClient()
    qc.setQueryData(queryKeys.messages.list(38), {
      pages: [{
        items: [{
          id: 12,
          conversation_id: 38,
          sender_type: 'customer',
          content: 'before edit',
          channel: 'telegram',
          is_read: false,
          created_at: '2026-04-15T10:00:00Z',
        }],
        has_older: false,
        latest_conversation_revision: 7,
      }],
      pageParams: [undefined],
    })

    mockApi.get.mockResolvedValueOnce({
      items: [{
        id: 12,
        conversation_id: 38,
        sender_type: 'customer',
        content: 'before edit',
        channel: 'telegram',
        is_read: false,
        created_at: '2026-04-15T10:00:00Z',
      }],
      has_older: false,
    })

    renderHook(() => {
      useWebSocket(38)
      return useInfiniteMessages(38)
    }, { wrapper: createWrapper(qc) })

    await act(async () => {
      await Promise.resolve()
    })

    act(() => {
      emitWsEvent('message_edited', {
        conversation_id: 38,
        message_id: 12,
        content: 'after edit',
        edited_at: '2026-04-15T10:05:00Z',
        conversation_revision: 12,
      })
    })

    await waitFor(() => {
      expect(
        (qc.getQueryData(queryKeys.messages.list(38)) as {
          pages?: Array<{ items: Array<{ id: number; content: string; edited_at?: string }> }>
        } | undefined)?.pages?.[0]?.items,
      ).toEqual([
        expect.objectContaining({
          id: 12,
          content: 'after edit',
          edited_at: '2026-04-15T10:05:00Z',
        }),
      ])
      expect(
        (qc.getQueryData(queryKeys.messages.list(38)) as {
          pages?: Array<{ latest_conversation_revision?: number }>
        } | undefined)?.pages?.[0]?.latest_conversation_revision,
      ).toBe(12)
    })
  })

  it('invalidates conversation previews after an edit mutation event', () => {
    const qc = createQueryClient()
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries')

    renderHook(() => useWebSocket(38), { wrapper: createWrapper(qc) })

    act(() => {
      emitWsEvent('message_edited', {
        conversation_id: 38,
        message_id: 12,
        content: 'after edit',
        edited_at: '2026-04-15T10:05:00Z',
      })
    })

    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: queryKeys.conversations.all }),
    )
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: queryKeys.liveChats }),
    )
  })
})

describe('useShimmerState', () => {
  it('returns an empty Set initially', () => {
    const qc = createQueryClient()
    const wrapper = createWrapper(qc)
    const { result } = renderHook(() => useShimmerState(), { wrapper })

    expect(result.current).toBeInstanceOf(Set)
    expect(result.current.size).toBe(0)
  })
})
