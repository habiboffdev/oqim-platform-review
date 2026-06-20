// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { createRef, type ComponentProps } from 'react'
import { act, render, screen } from '@testing-library/react'

import { MessageList, type ChatItem, START_INDEX } from './message-list'
import type { Message } from '@/lib/types'

const { virtuosoProps } = vi.hoisted(() => ({
  virtuosoProps: [] as Array<Record<string, unknown>>,
}))

vi.mock('react-virtuoso', () => ({
  Virtuoso: (props: Record<string, unknown>) => {
    virtuosoProps.push(props)
    return <div data-testid="virtuoso" />
  },
}))

function message(id: number, content: string): Message {
  return {
    id,
    conversation_id: 38,
    sender_type: 'customer',
    content,
    channel: 'telegram_dm',
    is_read: true,
    created_at: '2026-04-27T10:00:00Z',
  }
}

describe('MessageList virtual tail positioning', () => {
  function renderMessageList(
    overrides: Partial<ComponentProps<typeof MessageList>> = {},
  ) {
    const messages = [message(1, 'old'), message(2, 'latest')]
    const chatItems: ChatItem[] = messages.map((msg) => ({
      type: 'message',
      message: msg,
      position: 'single',
    }))
    const props = {
      messages,
      chatItems,
      hasOlder: false,
      isLoadingOlder: false,
      onLoadOlder: vi.fn(),
      onAtBottomChange: vi.fn(),
      virtuosoRef: createRef(),
      scrollerElementRef: createRef<HTMLElement>(),
      scrollToMessage: vi.fn(),
      messageMap: new Map(),
      highlightedMessageId: null,
      onPhotoClick: vi.fn(),
      tailVersion: 'conversation:2',
      ...overrides,
    } satisfies ComponentProps<typeof MessageList>

    render(<MessageList {...props} />)

    return { props, messages, chatItems }
  }

  it('opens on the last rendered chat item when firstItemIndex is offset', () => {
    const { chatItems } = renderMessageList()

    expect(screen.getByTestId('virtuoso')).toBeTruthy()
    expect(virtuosoProps.at(-1)).toMatchObject({
      style: { height: '100%' },
      firstItemIndex: START_INDEX - chatItems.length,
      initialTopMostItemIndex: { index: 'LAST', align: 'end' },
      increaseViewportBy: { top: 400, bottom: 0 },
    })
  })

  it('does not fetch older history from initial layout before bottom is reached', () => {
    const onLoadOlder = vi.fn()
    renderMessageList({ hasOlder: true, onLoadOlder })

    const props = virtuosoProps.at(-1) as {
      startReached: () => void
      atTopStateChange: (atTop: boolean) => void
      atBottomStateChange: (atBottom: boolean) => void
    }

    act(() => {
      props.startReached()
      props.atTopStateChange(true)
    })
    expect(onLoadOlder).not.toHaveBeenCalled()

    act(() => {
      props.atBottomStateChange(true)
      props.startReached()
    })
    expect(onLoadOlder).toHaveBeenCalledTimes(1)
  })
})
