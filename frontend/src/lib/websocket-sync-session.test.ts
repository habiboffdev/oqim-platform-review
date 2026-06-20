import { beforeEach, describe, expect, it, vi } from 'vitest'
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
import {
  buildSessionResumePayload,
  resetSyncSessionCursorsForTests,
} from '@/lib/sync-session'
import { applyWebSocketSyncSession } from './websocket-sync-session'
import type { Conversation, PaginatedConversations } from './types'

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
  return {
    items,
    next_cursor: null,
  }
}

describe('websocket sync-session application', () => {
  beforeEach(() => {
    resetSyncSessionCursorsForTests()
    vi.mocked(reconcileActiveTail).mockReset()
  })

  it('promotes canonical reconnect conversation state over stale cached list state', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const staleActive = conversation({
      id: 38,
      customer_id: 38,
      customer_name: 'Azim',
      telegram_chat_id: 3800,
      last_message_text: 'old cached preview',
      last_message_at: '2026-04-28T08:00:00Z',
      unread_count: 7,
      latest_conversation_seq: 10,
      latest_conversation_revision: 10,
    })
    const newerOther = conversation({
      id: 39,
      customer_id: 39,
      customer_name: 'Other',
      telegram_chat_id: 3900,
      last_message_at: '2026-04-28T09:00:00Z',
    })

    queryClient.setQueryData(queryKeys.conversations.list(), {
      pages: [page([newerOther, staleActive])],
      pageParams: [undefined],
    })
    queryClient.setQueryData(queryKeys.conversations.detail(38), staleActive)

    applyWebSocketSyncSession({
      queryClient,
      rawSyncData: {
        data: {
          action: 'refresh_scoped_runtime_delta',
          conversation_id: 38,
          latest_conversation_seq: 42,
          latest_conversation_revision: 44,
          conversation_state: {
            last_message_text: 'canonical reconnect preview',
            last_message_at: '2026-04-28T09:30:00+00:00',
            unread_count: 2,
          },
        },
        sequence_id: 77,
      },
      activeConversationId: 38,
      sequenceId: 77,
      dispatchActivityEvent: vi.fn(),
    })

    const list = queryClient.getQueryData(queryKeys.conversations.list()) as {
      pages: Array<{ items: Conversation[] }>
    }
    expect(list.pages[0]?.items.map((item) => item.id)).toEqual([38, 39])
    expect(list.pages[0]?.items[0]).toEqual(expect.objectContaining({
      last_message_text: 'canonical reconnect preview',
      last_message_at: '2026-04-28T09:30:00+00:00',
      unread_count: 2,
      latest_conversation_seq: 42,
      latest_conversation_revision: 44,
    }))
    expect(queryClient.getQueryData(queryKeys.conversations.detail(38))).toEqual(
      expect.objectContaining({
        last_message_text: 'canonical reconnect preview',
        last_message_at: '2026-04-28T09:30:00+00:00',
        unread_count: 2,
        latest_conversation_seq: 42,
        latest_conversation_revision: 44,
      }),
    )
  })

  it('does not advance the resume cursor before message reconciliation succeeds', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })

    applyWebSocketSyncSession({
      queryClient,
      rawSyncData: {
        data: {
          action: 'refresh_scoped_runtime_delta',
          conversation_id: 38,
          after_conversation_seq: 10,
          latest_conversation_seq: 42,
          latest_conversation_revision: 44,
          projections: [
            {
              name: 'messages',
              mode: 'delta',
              conversation_id: 38,
              after_conversation_seq: 10,
            },
          ],
        },
        sequence_id: 77,
      },
      activeConversationId: 38,
      sequenceId: 77,
      dispatchActivityEvent: vi.fn(),
    })

    expect(buildSessionResumePayload(38, 77)).toEqual({
      type: 'session.resume',
      last_sequence: 77,
      conversation_id: 38,
    })
  })

  it('does not move a conversation to the top from seq-only state patches', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const active = conversation({
      id: 38,
      customer_id: 38,
      customer_name: 'Azim',
      telegram_chat_id: 3800,
      last_message_text: 'old cached preview',
      last_message_at: '2026-04-28T08:00:00Z',
      unread_count: 7,
      latest_conversation_seq: 10,
      latest_conversation_revision: 10,
    })
    const newerOther = conversation({
      id: 39,
      customer_id: 39,
      customer_name: 'Other',
      telegram_chat_id: 3900,
      last_message_at: '2026-04-28T09:00:00Z',
    })

    queryClient.setQueryData(queryKeys.conversations.list(), {
      pages: [page([newerOther, active])],
      pageParams: [undefined],
    })

    applyWebSocketSyncSession({
      queryClient,
      rawSyncData: {
        data: {
          action: 'refresh_scoped_runtime_delta',
          conversation_id: 38,
          latest_conversation_seq: 42,
          latest_conversation_revision: 44,
          conversation_state: {
            unread_count: 2,
          },
        },
        sequence_id: 78,
      },
      activeConversationId: 38,
      sequenceId: 78,
      dispatchActivityEvent: vi.fn(),
    })

    const list = queryClient.getQueryData(queryKeys.conversations.list()) as {
      pages: Array<{ items: Conversation[] }>
    }
    expect(list.pages[0]?.items.map((item) => item.id)).toEqual([39, 38])
    expect(list.pages[0]?.items[1]).toEqual(expect.objectContaining({
      unread_count: 2,
      latest_conversation_seq: 42,
      latest_conversation_revision: 44,
    }))
  })

  it('falls back to scoped refresh when projection rows are not actionable', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    applyWebSocketSyncSession({
      queryClient,
      rawSyncData: {
        data: {
          action: 'refresh_scoped_runtime_delta',
          conversation_id: 38,
          after_conversation_seq: 10,
          projections: [
            null,
            { name: 'unknown', conversation_id: 38 },
            { name: 'messages', conversation_id: -1 },
          ],
        },
        sequence_id: 79,
      },
      activeConversationId: 38,
      sequenceId: 79,
      dispatchActivityEvent: vi.fn(),
    })

    expect(reconcileActiveTail).toHaveBeenCalledWith(queryClient, 38, {
      afterConversationSeq: 10,
    })
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
})
