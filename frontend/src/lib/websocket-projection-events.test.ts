import { describe, expect, it, vi } from 'vitest'
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
import { applyProjectionWebSocketEvent } from './websocket-projection-events'

function createClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
}

describe('projection websocket event routing', () => {
  it('updates unread state through canonical read projections', () => {
    const queryClient = createClient()
    queryClient.setQueryData(queryKeys.conversations.list(), {
      pages: [{
        items: [
          { id: 38, unread_count: 5 },
          { id: 39, unread_count: 2 },
        ],
        next_cursor: null,
      }],
      pageParams: [undefined],
    })

    const handled = applyProjectionWebSocketEvent({
      queryClient,
      typingTimers: new Map(),
      data: { type: 'read_inbox', conversation_id: 38, unread_count: 1 },
    })

    expect(handled).toBe(true)
    expect(
      (
        queryClient.getQueryData(queryKeys.conversations.list()) as {
          pages: Array<{ items: Array<{ id: number; unread_count: number }> }>
        }
      ).pages[0]?.items,
    ).toEqual([
      { id: 38, unread_count: 1 },
      { id: 39, unread_count: 2 },
    ])
  })

  it('reconciles active tail when edit projection misses local cache', () => {
    const queryClient = createClient()

    const handled = applyProjectionWebSocketEvent({
      queryClient,
      activeConversationId: 38,
      typingTimers: new Map(),
      data: {
        type: 'message_edited',
        conversation_id: 38,
        message_id: 404,
        content: 'edited elsewhere',
        edited_at: '2026-04-30T10:00:00Z',
        conversation_revision: 9,
      },
    })

    expect(handled).toBe(true)
    expect(reconcileActiveTail).toHaveBeenCalledWith(queryClient, 38)
  })

  it('refreshes conversation projections after a delete because the deleted row may be the list tail', () => {
    const queryClient = createClient()
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    const handled = applyProjectionWebSocketEvent({
      queryClient,
      activeConversationId: 38,
      typingTimers: new Map(),
      data: {
        type: 'message_deleted',
        conversation_id: 38,
        message_id: 404,
        conversation_revision: 10,
      },
    })

    expect(handled).toBe(true)
    expect(reconcileActiveTail).toHaveBeenCalledWith(queryClient, 38)
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: queryKeys.conversations.all }),
    )
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: queryKeys.liveChats }),
    )
  })

  it('invalidates bounded business projections without touching unknown events', () => {
    const queryClient = createClient()
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    expect(applyProjectionWebSocketEvent({
      queryClient,
      typingTimers: new Map(),
      data: { type: 'conversation_updated', conversation_id: 38 },
    })).toBe(true)
    expect(applyProjectionWebSocketEvent({
      queryClient,
      typingTimers: new Map(),
      data: { type: 'unknown.event' },
    })).toBe(false)

    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: queryKeys.conversations.all }),
    )
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: queryKeys.conversations.detail(38) }),
    )
  })
})
