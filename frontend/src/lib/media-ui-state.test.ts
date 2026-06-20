import { describe, expect, it } from 'vitest'

import type { Message } from '@/lib/types'
import { uz } from '@/lib/uz'
import {
  getMediaRuntimeDisplay,
  getRenderableMediaType,
  isMediaPlaceholder,
} from './media-ui-state'

function makeMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: 101,
    conversation_id: 30,
    sender_type: 'customer',
    content: '[photo]',
    channel: 'telegram_dm',
    is_read: true,
    created_at: '2026-04-23T06:00:00Z',
    media_type: 'photo',
    ...overrides,
  }
}

describe('media UI state projection', () => {
  it('detects canonical media placeholders without hiding normal captions', () => {
    expect(isMediaPlaceholder(makeMessage({ content: '[photo]' }))).toBe(true)
    expect(isMediaPlaceholder(makeMessage({ content: '[photo] caption' }))).toBe(false)
    expect(isMediaPlaceholder(makeMessage({ media_type: undefined, content: '[photo]' }))).toBe(false)
  })

  it('normalizes Telegram document stickers and document photos for rendering', () => {
    expect(getRenderableMediaType(makeMessage({
      media_type: 'document',
      media_metadata: { mime_type: 'application/x-tgsticker' },
    }))).toBe('sticker')

    expect(getRenderableMediaType(makeMessage({
      media_type: 'document',
      media_metadata: { mime_type: 'image/webp', emoji: '🙂' },
    }))).toBe('sticker')

    expect(getRenderableMediaType(makeMessage({
      media_type: 'document',
      media_metadata: { mime_type: 'image/png' },
    }))).toBe('photo')
  })

  it('blocks unavailable media and keeps retry/pending states non-blocking', () => {
    expect(getMediaRuntimeDisplay(makeMessage({
      media_runtime: {
        hydration_status: 'unavailable',
        asset_state: 'unavailable',
        semantic_state: 'unavailable',
        action_state: 'failed',
      },
    }))).toEqual({
      tone: 'unavailable',
      label: uz.conversations.mediaUnavailable,
      blocking: true,
    })

    expect(getMediaRuntimeDisplay(makeMessage({
      media_runtime: {
        hydration_status: 'deferred',
        asset_state: 'metadata_only',
        semantic_state: 'retrying',
        action_state: 'deferred',
      },
    }))).toEqual({
      tone: 'retrying',
      label: uz.conversations.mediaRetrying,
      blocking: false,
    })

    expect(getMediaRuntimeDisplay(makeMessage({
      media_runtime: {
        hydration_status: 'pending',
        asset_state: 'metadata_only',
        semantic_state: 'pending',
        action_state: 'pending',
      },
    }))).toEqual({
      tone: 'pending',
      label: uz.conversations.mediaPreparing,
      blocking: false,
    })
  })
})
