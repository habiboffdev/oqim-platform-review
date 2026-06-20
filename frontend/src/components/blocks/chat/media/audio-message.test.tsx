// @vitest-environment jsdom
import { fireEvent, render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { AudioMessage } from './audio-message'

describe('AudioMessage', () => {
  it('plays canonical audio media urls', () => {
    const playMock = vi.fn().mockResolvedValue(undefined)

    Object.defineProperty(HTMLMediaElement.prototype, 'play', {
      configurable: true,
      value: playMock,
    })

    const { getByRole } = render(
      <AudioMessage
        message={{
          id: 101,
          conversation_id: 38,
          sender_type: 'customer',
          content: '[audio]',
          channel: 'telegram',
          is_read: true,
          media_type: 'audio',
          media_full_url: '/api/media/38/80',
          media_metadata: {
            file_name: 'note.mp3',
            duration: 18,
          },
          created_at: '2026-04-23T06:00:00Z',
        }}
      />,
    )

    fireEvent.click(getByRole('button', { name: "Ijro etish" }))

    expect(playMock).toHaveBeenCalled()
  })
})
