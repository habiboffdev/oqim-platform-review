// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { VideoNoteMessage } from './video-note-message'

function makeVideoNoteMessage(id: number, preview: string, full: string) {
  return {
    id,
    conversation_id: 30,
    sender_type: 'customer',
    content: '[video_note]',
    channel: 'telegram_dm',
    is_read: true,
    created_at: '2026-04-23T06:00:00Z',
    media_type: 'video_note',
    media_preview_url: preview,
    media_full_url: full,
    media_metadata: {
      length: 400,
      duration: 6,
      is_round: true,
      is_video: true,
    },
  }
}

describe('VideoNoteMessage', () => {
  it('shows the preview image immediately before the full note is ready', () => {
    const message = makeVideoNoteMessage(101, '/api/media/30/101?thumb=true', '/api/media/30/101')

    const { container } = render(<VideoNoteMessage message={message as never} />)

    const preview = container.querySelector('img')
    const video = container.querySelector('video')
    expect(preview).not.toBeNull()
    expect(preview?.getAttribute('src')).toBe('/api/media/30/101?thumb=true&preview_runtime=vnote')
    expect(video?.style.opacity).toBe('0')
  })

  it('resets the preview when the message changes', () => {
    const firstMessage = makeVideoNoteMessage(101, '/api/media/30/101?thumb=true', '/api/media/30/101')
    const secondMessage = makeVideoNoteMessage(102, '/api/media/30/102?thumb=true', '/api/media/30/102')

    const { container, rerender } = render(<VideoNoteMessage message={firstMessage as never} />)

    fireEvent.load(container.querySelector('img') as HTMLImageElement)
    fireEvent.loadedMetadata(container.querySelector('video') as HTMLVideoElement)

    rerender(<VideoNoteMessage message={secondMessage as never} />)

    expect(container.querySelector('img')?.getAttribute('src')).toBe(
      '/api/media/30/102?thumb=true&preview_runtime=vnote'
    )
  })

  it('plays the note when the wrapper is clicked', () => {
    const message = makeVideoNoteMessage(101, '/api/media/30/101?thumb=true', '/api/media/30/101')
    const playMock = vi.fn().mockResolvedValue(undefined)

    Object.defineProperty(HTMLMediaElement.prototype, 'play', {
      configurable: true,
      value: playMock,
    })

    const { getByRole } = render(<VideoNoteMessage message={message as never} />)

    fireEvent.click(getByRole('button', { name: 'Play video note' }))

    expect(playMock).toHaveBeenCalled()
  })

  it('shows load percentage while the note is buffering', () => {
    const message = makeVideoNoteMessage(101, '/api/media/30/101?thumb=true', '/api/media/30/101')
    const playMock = vi.fn().mockResolvedValue(undefined)

    Object.defineProperty(HTMLMediaElement.prototype, 'play', {
      configurable: true,
      value: playMock,
    })

    const { getByRole, container } = render(<VideoNoteMessage message={message as never} />)
    const video = container.querySelector('video') as HTMLVideoElement

    Object.defineProperty(video, 'duration', {
      configurable: true,
      value: 10,
    })
    Object.defineProperty(video, 'buffered', {
      configurable: true,
      value: {
        length: 1,
        end: () => 4,
      },
    })

    fireEvent.click(getByRole('button', { name: 'Play video note' }))
    fireEvent.progress(video)

    expect(screen.getByText('40%')).not.toBeNull()
  })
})
