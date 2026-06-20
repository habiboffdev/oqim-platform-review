import { describe, expect, it, vi } from 'vitest'
import { QueryClient } from '@tanstack/react-query'

vi.mock('@/lib/active-tail-sync', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/active-tail-sync')>()
  return {
    ...actual,
    reconcileActiveTail: vi.fn(),
  }
})

import { queryKeys } from '@/lib/query-keys'
import { reconcileActiveTail } from '@/lib/active-tail-sync'
import { applyMessageWebSocketEvent } from './websocket-message-events'
import type { Conversation, PaginatedConversations } from './types'

function createClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
}

function conversation(overrides: Partial<Conversation>): Conversation {
  return {
    id: 1,
    customer_id: 1,
    customer_name: 'Customer',
    channel: 'telegram_dm',
    telegram_chat_id: 1001,
    pipeline_stage: 'new',
    needs_attention: false,
    last_message_at: '2026-04-28T08:00:00Z',
    unread_count: 0,
    created_at: '2026-04-28T08:00:00Z',
    ...overrides,
  }
}

function page(items: Conversation[]): PaginatedConversations {
  return { items, next_cursor: null }
}

describe('message websocket event projection', () => {
  it('promotes live conversation projection and appends active tail message', () => {
    const queryClient = createClient()
    const typingTimers = new Map<number, ReturnType<typeof setTimeout>>()
    queryClient.setQueryData(queryKeys.conversations.list(), {
      pages: [page([
        conversation({ id: 9, customer_name: 'Other' }),
        conversation({ id: 38, customer_name: 'Azim', last_message_text: 'old' }),
      ])],
      pageParams: [undefined],
    })
    queryClient.setQueryData(queryKeys.messages.list(38), {
      pages: [{ items: [], has_older: false }],
      pageParams: [undefined],
    })

    const handled = applyMessageWebSocketEvent({
      queryClient,
      activeConversationId: 38,
      typingTimers,
      data: {
        type: 'new_message',
        conversation_id: 38,
        conversation: conversation({ id: 38, customer_name: 'Azim' }),
        message: {
          id: 101,
          conversation_id: 38,
          sender_type: 'customer',
          content: 'canonical live text',
          created_at: '2026-04-28T09:00:00Z',
          conversation_revision: 12,
        },
      },
    })

    expect(handled).toBe(true)
    const conversations = queryClient.getQueryData(queryKeys.conversations.list()) as {
      pages: Array<{ items: Conversation[] }>
    }
    expect(conversations.pages[0]?.items.map((item) => item.id)).toEqual([38, 9])
    expect(conversations.pages[0]?.items[0]?.last_message_text).toBe('canonical live text')
    const messages = queryClient.getQueryData(queryKeys.messages.list(38)) as {
      pages: Array<{ items: Array<{ id: number; content: string }> }>
    }
    expect(messages.pages[0]?.items).toEqual([
      expect.objectContaining({ id: 101, content: 'canonical live text' }),
    ])
    expect(reconcileActiveTail).toHaveBeenCalledWith(queryClient, 38)
  })

  it('uses the live message timestamp when the conversation projection timestamp is stale', () => {
    const queryClient = createClient()
    queryClient.setQueryData(queryKeys.conversations.list(), {
      pages: [page([
        conversation({ id: 38, customer_name: 'Azim', last_message_at: '2026-04-28T08:00:00Z' }),
      ])],
      pageParams: [undefined],
    })

    applyMessageWebSocketEvent({
      queryClient,
      activeConversationId: undefined,
      typingTimers: new Map(),
      data: {
        type: 'new_message',
        conversation_id: 38,
        conversation: conversation({
          id: 38,
          customer_name: 'Azim',
          last_message_at: '2026-04-28T08:00:00Z',
        }),
        message: {
          id: 102,
          conversation_id: 38,
          sender_type: 'customer',
          content: 'new canonical text',
          created_at: '2026-04-28T09:15:00Z',
        },
      },
    })

    const conversations = queryClient.getQueryData(queryKeys.conversations.list()) as {
      pages: Array<{ items: Conversation[] }>
    }
    expect(conversations.pages[0]?.items[0]).toEqual(expect.objectContaining({
      last_message_at: '2026-04-28T09:15:00Z',
      last_message_text: 'new canonical text',
    }))
  })

  it('keeps delayed historical message projections ordered by canonical tail time', () => {
    const queryClient = createClient()
    queryClient.setQueryData(queryKeys.conversations.list(), {
      pages: [page([
        conversation({
          id: 9,
          customer_name: 'Fresh customer',
          last_message_at: '2026-04-28T11:00:00Z',
          last_message_text: 'fresh tail',
        }),
        conversation({
          id: 38,
          customer_name: 'Azim',
          last_message_at: '2026-04-28T10:00:00Z',
          last_message_text: 'current tail',
        }),
      ])],
      pageParams: [undefined],
    })

    applyMessageWebSocketEvent({
      queryClient,
      activeConversationId: undefined,
      typingTimers: new Map(),
      data: {
        type: 'new_message',
        conversation_id: 38,
        conversation: conversation({
          id: 38,
          customer_name: 'Azim',
          last_message_at: '2026-04-28T10:00:00Z',
          last_message_text: 'current tail',
        }),
        message: {
          id: 90,
          conversation_id: 38,
          sender_type: 'seller',
          content: 'old replayed ai reply',
          created_at: '2026-04-28T08:00:00Z',
        },
      },
    })

    const conversations = queryClient.getQueryData(queryKeys.conversations.list()) as {
      pages: Array<{ items: Conversation[] }>
    }
    expect(conversations.pages[0]?.items.map((item) => item.id)).toEqual([9, 38])
    expect(conversations.pages[0]?.items[1]).toEqual(expect.objectContaining({
      last_message_at: '2026-04-28T10:00:00Z',
      last_message_text: 'current tail',
    }))
  })

  it('does not insert an unloaded old replay conversation into the visible first page', () => {
    const queryClient = createClient()
    queryClient.setQueryData(queryKeys.conversations.list(), {
      pages: [page([
        conversation({ id: 9, customer_name: 'Fresh', last_message_at: '2026-04-28T11:00:00Z' }),
        conversation({ id: 10, customer_name: 'Still visible', last_message_at: '2026-04-28T10:00:00Z' }),
      ])],
      pageParams: [undefined],
    })

    applyMessageWebSocketEvent({
      queryClient,
      activeConversationId: undefined,
      typingTimers: new Map(),
      data: {
        type: 'new_message',
        conversation_id: 77,
        conversation: conversation({
          id: 77,
          customer_name: 'Old replay',
          last_message_at: '2026-04-27T09:00:00Z',
          last_message_text: 'old replay',
        }),
        message: {
          id: 77,
          conversation_id: 77,
          sender_type: 'customer',
          content: 'old replay',
          created_at: '2026-04-27T09:00:00Z',
        },
      },
    })

    const conversations = queryClient.getQueryData(queryKeys.conversations.list()) as {
      pages: Array<{ items: Conversation[] }>
    }
    expect(conversations.pages[0]?.items.map((item) => item.id)).toEqual([9, 10])
  })

  it('invalidates conversations when live event lacks a conversation projection', () => {
    const queryClient = createClient()
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    expect(applyMessageWebSocketEvent({
      queryClient,
      typingTimers: new Map(),
      data: { type: 'new_message', conversation_id: 38 },
    })).toBe(true)

    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: queryKeys.conversations.all }),
    )
  })

  it('returns false for non-message events', () => {
    expect(applyMessageWebSocketEvent({
      queryClient: createClient(),
      typingTimers: new Map(),
      data: { type: 'typing' },
    })).toBe(false)
  })
})
