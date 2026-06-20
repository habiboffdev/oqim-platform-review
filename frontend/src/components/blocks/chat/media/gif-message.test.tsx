// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { fireEvent, render } from '@testing-library/react'
import { GifMessage } from './gif-message'

class MockIntersectionObserver {
  observe() {}
  disconnect() {}
}

describe('GifMessage media continuity', () => {
  beforeEach(() => {
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver)
  })

  it('uses canonical full media url when media_url is absent', () => {
    const { container } = render(
      <GifMessage
        message={{
          id: 101,
          conversation_id: 38,
          sender_type: 'customer',
          content: '[gif]',
          channel: 'telegram',
          is_read: true,
          media_type: 'gif',
          media_full_url: '/api/media/38/77',
          created_at: '2026-04-23T06:00:00Z',
        }}
      />,
    )

    const video = container.querySelector('video')
    expect(video?.getAttribute('src')).toBe('/api/media/38/77')
  })

  it('resets the loaded state when virtualization swaps to a different gif message', () => {
    const firstMessage = {
      id: 101,
      conversation_id: 38,
      sender_type: 'customer',
      content: '[gif]',
      channel: 'telegram',
      is_read: true,
      media_type: 'gif',
      media_full_url: '/api/media/38/77',
      created_at: '2026-04-23T06:00:00Z',
    }
    const secondMessage = {
      ...firstMessage,
      id: 102,
      media_full_url: '/api/media/38/78',
    }

    const { container, rerender } = render(<GifMessage message={firstMessage} />)
    const firstVideo = container.querySelector('video') as HTMLVideoElement
    fireEvent.loadedData(firstVideo)
    expect(firstVideo.className).toContain('tg-media-loaded')

    rerender(<GifMessage message={secondMessage} />)

    const secondVideo = container.querySelector('video') as HTMLVideoElement
    expect(secondVideo.getAttribute('src')).toBe('/api/media/38/78')
    expect(secondVideo.className).not.toContain('tg-media-loaded')
  })
})
