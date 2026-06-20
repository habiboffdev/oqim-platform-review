import { QueryClient } from '@tanstack/react-query'
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest'

import { queryKeys } from '@/lib/query-keys'
import {
  applyMarkReadEvent,
  applyMessageDeletedEvent,
  applyMessageEditedEvent,
  applyReadInboxEvent,
  applyReadOutboxEvent,
  applyTypingEvent,
  clearTypingIndicator,
} from './websocket-runtime-events'

function createQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
}

describe('websocket runtime event handlers', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('updates conversation unread counts for read-state events', () => {
    const queryClient = createQueryClient()
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    queryClient.setQueryData(queryKeys.conversations.list(), {
      pages: [{
        items: [
          { id: 8, unread_count: 4 },
          { id: 9, unread_count: 2 },
        ],
        next_cursor: null,
      }],
      pageParams: [undefined],
    })

    applyMarkReadEvent(queryClient, {
      conversation_id: 8,
      unread_count: 0,
    })

    expect(
      (
        queryClient.getQueryData(queryKeys.conversations.list()) as {
          pages: Array<{ items: Array<{ id: number; unread_count: number }> }>
        }
      ).pages[0]?.items,
    ).toEqual([
      { id: 8, unread_count: 0 },
      { id: 9, unread_count: 2 },
    ])
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: queryKeys.liveChats })
  })

  it('applies read inbox events through the same canonical unread projection', () => {
    const queryClient = createQueryClient()
    queryClient.setQueryData(queryKeys.conversations.list(), {
      pages: [{
        items: [{ id: 8, unread_count: 4 }],
        next_cursor: null,
      }],
      pageParams: [undefined],
    })

    applyReadInboxEvent(queryClient, {
      conversation_id: 8,
      unread_count: 1,
    })

    expect(
      (
        queryClient.getQueryData(queryKeys.conversations.list()) as {
          pages: Array<{ items: Array<{ unread_count: number }> }>
        }
      ).pages[0]?.items[0]?.unread_count,
    ).toBe(1)
  })

  it('updates read outbox max id on live chat cache only', () => {
    const queryClient = createQueryClient()
    queryClient.setQueryData(queryKeys.liveChats, [
      { conversation_id: 8, read_outbox_max_id: 10 },
      { conversation_id: 9, read_outbox_max_id: 20 },
    ])

    applyReadOutboxEvent(queryClient, {
      conversation_id: 8,
      read_outbox_max_id: 33,
    })

    expect(queryClient.getQueryData(queryKeys.liveChats)).toEqual([
      { conversation_id: 8, read_outbox_max_id: 33 },
      { conversation_id: 9, read_outbox_max_id: 20 },
    ])
  })

  it('applies message edit events to the canonical message projection', () => {
    const queryClient = createQueryClient()
    queryClient.setQueryData(queryKeys.messages.list(8), {
      pages: [{
        items: [
          { id: 44, content: 'old', text_entities: [], edited_at: undefined },
          { id: 45, content: 'keep', text_entities: [], edited_at: undefined },
        ],
        latest_conversation_revision: 3,
        has_older: false,
      }],
      pageParams: [undefined],
    })

    const found = applyMessageEditedEvent(queryClient, {
      conversation_id: 8,
      message_id: 44,
      content: 'new',
      edited_at: '2026-04-27T10:00:00Z',
      conversation_revision: 4,
      text_entities: [{ type: 'bold', offset: 0, length: 3 }],
    })

    const data = queryClient.getQueryData(queryKeys.messages.list(8)) as {
      pages: Array<{
        latest_conversation_revision: number
        items: Array<{ id: number; content: string; edited_at?: string; text_entities?: unknown[] }>
      }>
    }
    expect(found).toBe(true)
    expect(data.pages[0]?.latest_conversation_revision).toBe(4)
    expect(data.pages[0]?.items[0]).toMatchObject({
      id: 44,
      content: 'new',
      edited_at: '2026-04-27T10:00:00Z',
      text_entities: [{ type: 'bold', offset: 0, length: 3 }],
    })
    expect(data.pages[0]?.items[1]?.content).toBe('keep')
  })

  it('applies message delete events and reports whether the message was in cache', () => {
    const queryClient = createQueryClient()
    queryClient.setQueryData(queryKeys.messages.list(8), {
      pages: [{
        items: [{ id: 44, content: 'old' }],
        latest_conversation_revision: 3,
        has_older: false,
      }],
      pageParams: [undefined],
    })

    const found = applyMessageDeletedEvent(queryClient, {
      conversation_id: 8,
      message_id: 44,
      conversation_revision: 5,
    })
    const missing = applyMessageDeletedEvent(queryClient, {
      conversation_id: 8,
      message_id: 404,
      conversation_revision: 6,
    })

    const data = queryClient.getQueryData(queryKeys.messages.list(8)) as {
      pages: Array<{ latest_conversation_revision: number; items: Array<{ content: string }> }>
    }
    expect(found).toBe(true)
    expect(missing).toBe(false)
    expect(data.pages[0]?.latest_conversation_revision).toBe(6)
    expect(data.pages[0]?.items[0]?.content).toBe('[deleted]')
  })

  it('auto-clears typing state after the timeout', () => {
    const queryClient = createQueryClient()
    const timers = new Map<number, ReturnType<typeof setTimeout>>()

    applyTypingEvent(queryClient, timers, {
      conversation_id: 8,
      is_typing: true,
    })

    expect(queryClient.getQueryData(['typing', 8])).toEqual({
      isTyping: true,
      timestamp: expect.any(Number),
    })
    expect(timers.has(8)).toBe(true)

    vi.advanceTimersByTime(5000)

    expect(queryClient.getQueryData(['typing', 8])).toEqual({
      isTyping: false,
      timestamp: expect.any(Number),
    })
    expect(timers.has(8)).toBe(false)
  })

  it('clears typing state and cancels the timer explicitly', () => {
    const queryClient = createQueryClient()
    const timers = new Map<number, ReturnType<typeof setTimeout>>()

    applyTypingEvent(queryClient, timers, {
      conversation_id: 8,
      is_typing: true,
    })
    clearTypingIndicator(queryClient, timers, 8)

    expect(queryClient.getQueryData(['typing', 8])).toEqual({
      isTyping: false,
      timestamp: expect.any(Number),
    })
    expect(timers.has(8)).toBe(false)
  })
})
