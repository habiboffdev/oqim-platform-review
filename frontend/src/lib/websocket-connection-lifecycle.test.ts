import { beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient } from '@tanstack/react-query'

vi.mock('@/lib/active-tail-sync', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/active-tail-sync')>()
  return {
    ...actual,
    reconcileActiveTail: vi.fn(),
  }
})

import { reconcileActiveTail } from '@/lib/active-tail-sync'
import { queryKeys } from '@/lib/query-keys'
import {
  registerActiveConversationLifecycle,
  registerReconnectLifecycle,
} from './websocket-connection-lifecycle'

function createClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
}

describe('websocket connection lifecycle', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('resumes from canonical sequence and re-registers the active chat on reconnect', () => {
    const handlers = new Map<string, (data: Record<string, unknown>) => void>()
    const send = vi.fn()
    const dispatchActivityEvent = vi.fn()

    const unsubscribe = registerReconnectLifecycle({
      wsManager: {
        on: vi.fn((event, handler) => {
          handlers.set(event, handler)
          return vi.fn()
        }),
        send,
      },
      activeConversationRef: { current: 42 },
      lastSequenceRef: { current: 9001 },
      dispatchActivityEvent,
    })

    handlers.get('reconnect')?.({})

    expect(unsubscribe).toEqual(expect.any(Function))
    expect(dispatchActivityEvent).toHaveBeenCalledWith(
      expect.objectContaining({
        type: 'sync:checking',
        data: { conversation_id: 42, last_sequence: 9001 },
      }),
    )
    expect(send).toHaveBeenCalledWith(expect.objectContaining({
      type: 'session.resume',
      conversation_id: 42,
      last_sequence: 9001,
    }))
    expect(send).toHaveBeenCalledWith({ type: 'chat_opened', conversation_id: 42 })
  })

  it('keeps reconnect resume global when no conversation is active', () => {
    const handlers = new Map<string, (data: Record<string, unknown>) => void>()
    const send = vi.fn()

    registerReconnectLifecycle({
      wsManager: {
        on: vi.fn((event, handler) => {
          handlers.set(event, handler)
          return vi.fn()
        }),
        send,
      },
      activeConversationRef: { current: undefined },
      lastSequenceRef: { current: 7 },
      dispatchActivityEvent: vi.fn(),
    })

    handlers.get('reconnect')?.({})

    expect(send).toHaveBeenCalledTimes(1)
    expect(send).toHaveBeenCalledWith(expect.objectContaining({
      type: 'session.resume',
      last_sequence: 7,
    }))
  })

  it('opens the active chat, reconciles cached tails, and closes on cleanup', () => {
    const queryClient = createClient()
    const send = vi.fn()
    queryClient.setQueryData(queryKeys.messages.list(42), {
      pages: [{ items: [{ id: 1 }], has_older: false }],
      pageParams: [undefined],
    })

    const cleanup = registerActiveConversationLifecycle({
      wsManager: { send },
      queryClient,
      activeConversationId: 42,
    })

    expect(send).toHaveBeenCalledWith({ type: 'chat_opened', conversation_id: 42 })
    expect(reconcileActiveTail).toHaveBeenCalledWith(queryClient, 42)

    cleanup()

    expect(send).toHaveBeenCalledWith({ type: 'chat_closed', conversation_id: 42 })
  })
})
