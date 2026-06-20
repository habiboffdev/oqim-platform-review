// @vitest-environment jsdom
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { StickerMessage } from './sticker-message'

function makeStickerMessage(id: number, full: string, preview: string) {
  return {
    id,
    conversation_id: 30,
    sender_type: 'seller',
    content: '',
    channel: 'telegram_dm',
    is_read: true,
    created_at: '2026-04-23T06:00:00Z',
    media_type: 'sticker',
    media_full_url: full,
    media_preview_url: preview,
    media_metadata: {
      mime_type: 'image/webp',
      is_animated: false,
      is_video: false,
      emoji: '🤩',
    },
  }
}

describe('StickerMessage', () => {
  it('resets the rendered src when the message changes', () => {
    const firstMessage = makeStickerMessage(101, '/api/media/30/101', '/api/media/30/101?thumb=true')
    const secondMessage = makeStickerMessage(102, '/api/media/30/102', '/api/media/30/102?thumb=true')

    const { rerender } = render(<StickerMessage message={firstMessage as never} />)

    expect(screen.getByRole('img').getAttribute('src')).toBe('/api/media/30/101')

    rerender(<StickerMessage message={secondMessage as never} />)

    expect(screen.getByRole('img').getAttribute('src')).toBe('/api/media/30/102')
  })
})
