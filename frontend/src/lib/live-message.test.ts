import { describe, expect, it } from 'vitest'

import { normalizeLiveMessagePayload } from './live-message'

describe('live message normalization', () => {
  it('prefers backend-provided media URLs and normalizes text entities', () => {
    const message = normalizeLiveMessagePayload({
      id: 1,
      sender_type: 'customer',
      content: 'hey',
      created_at: '2026-04-27T10:00:00Z',
      media_type: 'photo',
      media_url: '/api/media/source',
      media_full_url: '/api/media/full',
      media_preview_url: '/api/media/preview',
      text_entities: [
        { type: 'custom_emoji', offset: 0, length: 2, document_id: 123 },
      ],
    }, 38)

    expect(message.media_url).toBe('/api/media/source')
    expect(message.media_full_url).toBe('/api/media/full')
    expect(message.media_preview_url).toBe('/api/media/preview')
    expect(message.text_entities).toEqual([
      { type: 'custom_emoji', offset: 0, length: 2, document_id: '123' },
    ])
  })

  it('reconstructs previewable media URLs for older websocket payloads', () => {
    const message = normalizeLiveMessagePayload({
      id: 2,
      sender_type: 'customer',
      content: '[video_note]',
      created_at: '2026-04-27T10:00:00Z',
      media_type: 'video_note',
      telegram_chat_id: 700,
      telegram_message_id: 90,
    }, 38)

    expect(message.media_url).toBe('/api/media/700/90')
    expect(message.media_full_url).toBe('/api/media/700/90')
    expect(message.media_preview_url).toBe('/api/media/700/90?thumb=true')
  })

  it('does not reconstruct URLs for non-downloadable media descriptors', () => {
    const message = normalizeLiveMessagePayload({
      id: 3,
      sender_type: 'customer',
      content: '[location]',
      created_at: '2026-04-27T10:00:00Z',
      media_type: 'location',
      telegram_chat_id: 700,
      telegram_message_id: 91,
    }, 38)

    expect(message.media_url).toBeUndefined()
    expect(message.media_full_url).toBeUndefined()
    expect(message.media_preview_url).toBeUndefined()
  })

  it('does not invent preview URLs for voice or audio messages', () => {
    const message = normalizeLiveMessagePayload({
      id: 4,
      sender_type: 'customer',
      content: '[voice]',
      created_at: '2026-04-27T10:00:00Z',
      media_type: 'voice',
      telegram_chat_id: 700,
      telegram_message_id: 92,
    }, 38)

    expect(message.media_url).toBe('/api/media/700/92')
    expect(message.media_full_url).toBe('/api/media/700/92')
    expect(message.media_preview_url).toBeUndefined()
  })
})
