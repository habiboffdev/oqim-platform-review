import { describe, it, expect } from 'vitest'
import { buildChatItems, computeChatItemKey } from './message-list'
import type { Message } from '@/lib/types'

function makeMessage(overrides: Partial<Message>): Message {
  return {
    id: 1,
    conversation_id: 38,
    sender_type: 'customer',
    content: '[photo]',
    channel: 'telegram',
    is_read: true,
    created_at: '2026-04-23T06:00:00Z',
    ...overrides,
  }
}

describe('buildChatItems media grouping', () => {
  it('groups image documents that render as photos', () => {
    const messages = [
      makeMessage({
        id: 101,
        content: '[document]',
        media_type: 'document',
        media_metadata: { mime_type: 'image/jpeg', file_name: 'album-1.jpg' },
        grouped_id: 700,
      }),
      makeMessage({
        id: 102,
        content: '[document]',
        media_type: 'document',
        media_metadata: { mime_type: 'image/jpeg', file_name: 'album-2.jpg' },
        grouped_id: 700,
        created_at: '2026-04-23T06:00:01Z',
      }),
    ]

    const items = buildChatItems(messages)
    const mediaGroup = items.find((item) => item.type === 'media-group')

    expect(mediaGroup).toBeDefined()
    expect(mediaGroup?.type).toBe('media-group')
    expect(mediaGroup?.messages.map((message) => message.id)).toEqual([101, 102])
  })

  it('uses stable message-based keys for virtualized media rows', () => {
    const groupedItems = buildChatItems([
      makeMessage({
        id: 101,
        content: '[document]',
        media_type: 'document',
        media_metadata: { mime_type: 'image/jpeg', file_name: 'album-1.jpg' },
        grouped_id: 700,
      }),
      makeMessage({
        id: 102,
        content: '[document]',
        media_type: 'document',
        media_metadata: { mime_type: 'image/jpeg', file_name: 'album-2.jpg' },
        grouped_id: 700,
        created_at: '2026-04-23T06:00:01Z',
      }),
    ])
    const mediaGroup = groupedItems.find((item) => item.type === 'media-group')
    const messageItem = buildChatItems([makeMessage({ id: 201 })]).find((item) => item.type === 'message')

    expect(mediaGroup).toBeDefined()
    expect(messageItem).toBeDefined()
    expect(computeChatItemKey(mediaGroup!)).toBe('media-group:101-102')
    expect(computeChatItemKey(messageItem!)).toBe('message:201')
  })
})
