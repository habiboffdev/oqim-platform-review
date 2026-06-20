import { describe, expect, it } from 'vitest'
import type { InfiniteData } from '@tanstack/react-query'

import {
  appendMissingLiveMessageToTail,
  hasMessageId,
  mergeAuthoritativeDelta,
  mergeAuthoritativeTail,
  normalizeTextEntities,
  trimConversationPages,
  upsertLiveMessageInPages,
} from './active-tail-sync'
import type {
  Message,
  PaginatedConversations,
  PaginatedMessages,
} from './types'

function message(overrides: Partial<Message>): Message {
  return {
    id: 1,
    conversation_id: 38,
    sender_type: 'customer',
    content: 'salom',
    channel: 'telegram_dm',
    is_read: false,
    created_at: '2026-04-26T09:00:00Z',
    ...overrides,
  }
}

function page(items: Message[], extra: Partial<PaginatedMessages> = {}): PaginatedMessages {
  return {
    items,
    has_older: false,
    ...extra,
  }
}

describe('active-tail sync helpers', () => {
  it('replaces the authoritative tail while preserving unresolved optimistic sends', () => {
    const unresolved = message({
      id: -1,
      sender_type: 'seller',
      content: 'still local',
      client_message_uuid: 'local-only',
    })
    const matchedOptimistic = message({
      id: -2,
      sender_type: 'seller',
      content: 'will reconcile',
      client_message_uuid: 'settled',
    })
    const old: InfiniteData<PaginatedMessages> = {
      pages: [
        page([message({ id: 1, content: 'old page' })]),
        page([matchedOptimistic, unresolved]),
      ],
      pageParams: [undefined, 'cursor'],
    }
    const latestTail = page([
      message({ id: 10, content: 'authoritative', client_message_uuid: 'settled' }),
    ])

    const merged = mergeAuthoritativeTail(old, latestTail)

    expect(merged.pages[0].items.map((item) => item.content)).toEqual(['old page'])
    expect(merged.pages[1].items.map((item) => item.content)).toEqual([
      'authoritative',
      'still local',
    ])
  })

  it('preserves positive unknown placeholders until Telegram echo reconciles them', () => {
    const unknownLocalSend = message({
      id: 101,
      sender_type: 'seller',
      content: 'sent but sidecar response was lost',
      client_message_uuid: 'unknown-send',
      delivery_state: 'unknown',
      delivery_runtime: {
        schema_version: 'delivery_runtime.v1',
        state: 'unknown',
        customer_status: 'uncertain',
        next_action: 'reconcile',
        is_terminal: false,
        requires_reconciliation: true,
        can_retry: false,
        attempt_count: 1,
        max_attempts: 3,
        retry_budget_remaining: 2,
      },
    })
    const old: InfiniteData<PaginatedMessages> = {
      pages: [
        page([unknownLocalSend]),
      ],
      pageParams: [undefined],
    }
    const latestTail = page([
      message({ id: 102, content: 'authoritative customer tail' }),
    ])

    const merged = mergeAuthoritativeTail(old, latestTail)

    expect(merged.pages[0].items.map((item) => item.content)).toEqual([
      'authoritative customer tail',
      'sent but sidecar response was lost',
    ])
  })

  it('merges bounded deltas without duplicating settled ids or client UUIDs', () => {
    const old: InfiniteData<PaginatedMessages> = {
      pages: [
        page([
          message({ id: 1, content: 'already loaded' }),
          message({ id: -1, sender_type: 'seller', content: 'pending', client_message_uuid: 'pending' }),
          message({ id: -2, sender_type: 'seller', content: 'settled local', client_message_uuid: 'settled' }),
        ], {
          latest_conversation_seq: 10,
          latest_conversation_revision: 10,
        }),
      ],
      pageParams: [undefined],
    }
    const delta = page([
      message({ id: 1, content: 'already loaded updated' }),
      message({ id: 2, content: 'new server message', client_message_uuid: 'settled' }),
    ], {
      latest_conversation_seq: 12,
      latest_conversation_revision: 14,
    })

    const merged = mergeAuthoritativeDelta(old, delta)

    expect(merged.pages[0].latest_conversation_seq).toBe(12)
    expect(merged.pages[0].latest_conversation_revision).toBe(14)
    expect(merged.pages[0].items.map((item) => item.content)).toEqual([
      'already loaded updated',
      'new server message',
      'pending',
    ])
  })

  it('upserts live messages by replacing matching optimistic client uuids', () => {
    const old: InfiniteData<PaginatedMessages> = {
      pages: [
        page([
          message({
            id: -1,
            sender_type: 'seller',
            content: 'optimistic',
            client_message_uuid: 'uuid-1',
          }),
        ], {
          latest_conversation_seq: 10,
          latest_conversation_revision: 10,
        }),
      ],
      pageParams: [undefined],
    }

    const merged = upsertLiveMessageInPages(old, message({
      id: 88,
      sender_type: 'seller',
      content: 'confirmed',
      client_message_uuid: 'uuid-1',
      conversation_seq: 11,
    }), {
      latestConversationRevision: 12,
    })

    expect(merged?.pages[0].items.map((item) => item.id)).toEqual([88])
    expect(merged?.pages[0].latest_conversation_seq).toBe(11)
    expect(merged?.pages[0].latest_conversation_revision).toBe(12)
  })

  it('does not append live messages that are already present by id', () => {
    const existing = message({ id: 88, content: 'existing' })
    const old: InfiniteData<PaginatedMessages> = {
      pages: [page([existing])],
      pageParams: [undefined],
    }

    const merged = upsertLiveMessageInPages(old, message({ id: 88, content: 'duplicate' }))

    expect(merged).toBe(old)
    expect(hasMessageId(merged, 88)).toBe(true)
  })

  it('appends missing live messages to the tail page for delayed reconciliation', () => {
    const old: InfiniteData<PaginatedMessages> = {
      pages: [
        page([message({ id: 1, content: 'old' })]),
        page([message({ id: 2, content: 'tail' })], {
          latest_conversation_seq: 5,
          latest_conversation_revision: 5,
        }),
      ],
      pageParams: [undefined, 'tail'],
    }

    const merged = appendMissingLiveMessageToTail(old, message({
      id: 3,
      content: 'new',
      conversation_seq: 6,
      conversation_revision: 7,
    }))

    expect(merged?.pages[0].items.map((item) => item.content)).toEqual(['old'])
    expect(merged?.pages[1].items.map((item) => item.content)).toEqual(['tail', 'new'])
    expect(merged?.pages[1].latest_conversation_seq).toBe(6)
    expect(merged?.pages[1].latest_conversation_revision).toBe(7)
  })

  it('trims conversation pages after the first exhausted page', () => {
    const data: InfiniteData<PaginatedConversations> = {
      pages: [
        { items: [], next_cursor: 'next' },
        { items: [], next_cursor: null },
        { items: [], next_cursor: 'stale' },
      ],
      pageParams: [undefined, 'next', 'stale'],
    }

    const trimmed = trimConversationPages(data)

    expect(trimmed.pages).toHaveLength(2)
    expect(trimmed.pageParams).toEqual([undefined, 'next'])
  })

  it('normalizes valid text entities and rejects malformed payloads', () => {
    expect(normalizeTextEntities([
      { type: 'custom_emoji', offset: '1', length: 2, document_id: 123 },
      { type: '', offset: 0, length: 1 },
      { type: 'bold', offset: 'bad', length: 1 },
    ])).toEqual([
      {
        type: 'custom_emoji',
        offset: 1,
        length: 2,
        document_id: '123',
      },
    ])
    expect(normalizeTextEntities({ nope: true })).toBeUndefined()
  })
})
