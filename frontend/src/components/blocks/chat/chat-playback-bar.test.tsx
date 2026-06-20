// @vitest-environment jsdom
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAudioPlayer } from '@/hooks/use-audio-player'
import { ChatPlaybackBar } from './chat-playback-bar'

describe('ChatPlaybackBar', () => {
  beforeEach(() => {
    Object.defineProperty(HTMLMediaElement.prototype, 'pause', {
      configurable: true,
      value: vi.fn(),
    })

    act(() => {
      useAudioPlayer.setState({
        queue: [
          { messageId: 101, url: '/api/media/38/80', duration: 18, kind: 'voice', label: 'Ovozli xabar' },
          { messageId: 102, url: '/api/media/38/81', duration: 30, kind: 'audio', label: 'voice-note.mp3' },
        ],
        activeMessageId: 101,
        activeKind: 'voice',
        activeLabel: 'Ovozli xabar',
        activeSource: 'audio',
        isPlaying: true,
        currentTime: 6,
        duration: 18,
        playbackRate: 1,
      })
    })
  })

  afterEach(() => {
    act(() => {
      useAudioPlayer.getState().clear()
    })
  })

  it('renders the active media label, queue position, and supports speed/seek controls', () => {
    render(<ChatPlaybackBar />)

    expect(screen.getByText('Ovozli xabar')).not.toBeNull()
    expect(screen.getByText('1 / 2')).not.toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Ijro tezligi' }))
    expect(useAudioPlayer.getState().playbackRate).toBe(1.25)

    fireEvent.change(screen.getByRole('slider', { name: 'Media ichida surish' }), {
      target: { value: '9' },
    })
    expect(useAudioPlayer.getState().currentTime).toBe(9)
  })
})
