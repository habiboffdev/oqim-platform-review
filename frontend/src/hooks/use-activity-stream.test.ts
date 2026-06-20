// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ReactNode } from 'react'
import { useWebSocket } from './use-websocket'
import { useActivityStream } from './use-activity-stream'

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

function emitWsEvent(type: string, data: Record<string, unknown>) {
  const wildcardHandlers = handlers.get('*')
  if (wildcardHandlers) {
    wildcardHandlers.forEach((handler) => handler({ type, ...data }))
  }
}

function emitNamedEvent(type: string, data: Record<string, unknown> = {}) {
  const namedHandlers = handlers.get(type)
  if (namedHandlers) {
    namedHandlers.forEach((handler) => handler(data))
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

function createWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })

  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: qc }, children)
}

describe('useActivityStream', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    handlers.clear()
  })

  it('emits a recovery-checking activity event when websocket reconnects', async () => {
    const { result } = renderHook(() => {
      useWebSocket(38)
      return useActivityStream()
    }, { wrapper: createWrapper() })

    act(() => {
      emitNamedEvent('reconnect')
    })

    await waitFor(() => {
      expect(result.current.latestEvent).toEqual(
        expect.objectContaining({
          type: 'sync:checking',
          scope: 'system',
          data: expect.objectContaining({
            conversation_id: 38,
            last_sequence: 0,
          }),
        }),
      )
    })
  })

  it('emits a recovery-complete activity event when sync_response arrives', async () => {
    const { result } = renderHook(() => {
      useWebSocket(38)
      return useActivityStream()
    }, { wrapper: createWrapper() })

    act(() => {
      emitWsEvent('sync_response', {
        sequence_id: 7,
        data: {
          action: 'invalidate_all',
        },
      })
    })

    await waitFor(() => {
      expect(result.current.latestEvent).toEqual(
        expect.objectContaining({
          type: 'sync:complete',
          scope: 'system',
          data: expect.objectContaining({
            action: 'invalidate_all',
            sequence_id: 7,
          }),
        }),
      )
    })
  })

  it('accepts Seller Agent activity events and ignores old followup activity names', async () => {
    const { result } = renderHook(() => {
      useWebSocket(38)
      return useActivityStream()
    }, { wrapper: createWrapper() })

    act(() => {
      emitWsEvent('seller_agent_reply:ready', {
        message: 'Javob tayyor',
        scope: 'conversation',
        data: { conversation_id: 38 },
      })
    })

    await waitFor(() => {
      expect(result.current.latestEvent).toEqual(
        expect.objectContaining({
          type: 'seller_agent_reply:ready',
          scope: 'conversation',
          message: 'Javob tayyor',
        }),
      )
    })

    act(() => {
      emitWsEvent('followup:due', {
        message: 'legacy followup',
        scope: 'conversation',
      })
    })

    expect(result.current.latestEvent?.type).toBe('seller_agent_reply:ready')
  })
})
