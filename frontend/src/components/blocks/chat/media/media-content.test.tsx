// @vitest-environment jsdom
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { Message } from '@/lib/types'
import { uz } from '@/lib/uz'
import { MediaContent } from './media-content'

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
    media_preview_url: '/api/media/30/101?thumb=true',
    media_full_url: '/api/media/30/101',
    media_metadata: { width: 640, height: 480 },
    ...overrides,
  }
}

describe('MediaContent runtime states', () => {
  it('blocks unavailable media with a canonical degraded state', () => {
    const { container } = render(
      <MediaContent
        message={makeMessage({
          media_runtime: {
            hydration_status: 'unavailable',
            asset_state: 'unavailable',
            semantic_state: 'unavailable',
            action_state: 'failed',
          },
        })}
      />,
    )

    expect(screen.getByText(uz.conversations.mediaUnavailable)).toBeTruthy()
    expect(container.querySelector('img')).toBeNull()
  })

  it('keeps preview rendering while showing pending runtime state', () => {
    const { container } = render(
      <MediaContent
        message={makeMessage({
          media_runtime: {
            hydration_status: 'pending',
            asset_state: 'metadata_only',
            semantic_state: 'pending',
            action_state: 'pending',
          },
        })}
      />,
    )

    expect(screen.getByText(uz.conversations.mediaPreparing)).toBeTruthy()
    expect(container.querySelector('img')?.getAttribute('src')).toBe('/api/media/30/101?thumb=true')
  })
})
