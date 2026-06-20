// @vitest-environment jsdom
import { fireEvent, render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { PhotoMessage } from './photo-message'

function makeMessage(id: number, preview: string, full: string) {
  return {
    id,
    conversation_id: 30,
    sender_type: 'seller',
    content: '',
    channel: 'telegram_dm',
    is_read: true,
    created_at: '2026-04-23T06:00:00Z',
    media_type: 'photo',
    media_preview_url: preview,
    media_full_url: full,
    media_metadata: { width: 640, height: 480 },
  }
}

describe('PhotoMessage', () => {
  it('resets preview src when virtuoso reuses the component for a new message', () => {
    const firstMessage = makeMessage(101, '/api/media/30/101?thumb=true', '/api/media/30/101')
    const secondMessage = makeMessage(102, '/api/media/30/102?thumb=true', '/api/media/30/102')

    const { container, rerender } = render(<PhotoMessage message={firstMessage as never} />)

    const image = container.querySelector('img') as HTMLImageElement
    expect(image.getAttribute('src')).toBe('/api/media/30/101?thumb=true')

    fireEvent.load(image)
    rerender(<PhotoMessage message={secondMessage as never} />)

    expect(container.querySelector('img')?.getAttribute('src')).toBe('/api/media/30/102?thumb=true')
  })

  it('falls back to the full media url after a preview error', () => {
    const message = makeMessage(101, '/api/media/30/101?thumb=true', '/api/media/30/101')

    const { container } = render(<PhotoMessage message={message as never} />)

    const image = container.querySelector('img') as HTMLImageElement
    fireEvent.error(image)

    expect(container.querySelector('img')?.getAttribute('src')).toBe('/api/media/30/101')
  })
})
