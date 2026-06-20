// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ChatViewer } from './chat-viewer'

const { scrollToIndexMock } = vi.hoisted(() => ({
  scrollToIndexMock: vi.fn(),
}))

vi.mock('@/hooks/use-infinite-messages', () => ({
  useInfiniteMessages: vi.fn(),
}))

vi.mock('@/hooks/use-message-lookup', () => ({
  useMessageLookup: vi.fn(() => new Map()),
}))

vi.mock('@/lib/api-client', () => ({
  api: {
    post: vi.fn(),
  },
}))

vi.mock('./message-list', () => ({
  START_INDEX: 1_000_000,
  buildChatItems: vi.fn(() => []),
  MessageList: ({
    onPhotoClick,
    virtuosoRef,
  }: {
    onPhotoClick: (messageId: number) => void
    virtuosoRef?: { current: { scrollToIndex: typeof scrollToIndexMock } | null }
  }) => {
    if (virtuosoRef) {
      virtuosoRef.current = { scrollToIndex: scrollToIndexMock }
    }
    return (
      <button type="button" onClick={() => onPhotoClick(101)}>
        open photo
      </button>
    )
  },
}))

vi.mock('./photo-lightbox', () => ({
  PhotoLightbox: ({ open, slides }: { open: boolean; slides: { src: string }[] }) => (
    <div
      data-testid="photo-lightbox"
      data-open={String(open)}
      data-slides={slides.map((slide) => slide.src).join(',')}
    />
  ),
}))

vi.mock('./scroll-to-bottom-fab', () => ({
  ScrollToBottomFab: () => null,
}))

vi.mock('./typing-indicator', () => ({
  TypingIndicator: () => null,
}))

import { useInfiniteMessages } from '@/hooks/use-infinite-messages'
import { api } from '@/lib/api-client'

const mockUseInfiniteMessages = useInfiniteMessages as ReturnType<typeof vi.fn>
const mockApi = api as unknown as { post: ReturnType<typeof vi.fn> }

function renderViewer(conversationId = 38) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <ChatViewer conversationId={conversationId} />
    </QueryClientProvider>,
  )
}

describe('ChatViewer photo lightbox continuity', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    scrollToIndexMock.mockClear()
    mockApi.post.mockResolvedValue({
      requested: 0,
      persisted: 0,
      duplicates: 0,
      unread_count: 0,
      hydration: { state: 'idle', needed: false },
    })
    mockUseInfiniteMessages.mockReturnValue({
      data: {
        pages: [
          {
            items: [
              {
                id: 101,
                conversation_id: 38,
                sender_type: 'customer',
                content: '[photo]',
                channel: 'telegram',
                is_read: true,
                media_type: 'photo',
                media_full_url: '/api/media/38/50',
                created_at: '2026-04-23T06:00:00Z',
              },
            ],
          },
        ],
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
      hasPreviousPage: false,
      isFetchingPreviousPage: false,
      fetchPreviousPage: vi.fn(),
    })
  })

  it('opens photo lightbox when only media_full_url is present', async () => {
    renderViewer()

    await waitFor(() => expect(screen.getByTestId('photo-lightbox')).toBeTruthy())
    const lightbox = screen.getByTestId('photo-lightbox')
    expect(lightbox.getAttribute('data-open')).toBe('false')
    expect(lightbox.getAttribute('data-slides')).toBe('/api/media/38/50')

    fireEvent.click(screen.getByText('open photo'))

    expect(screen.getByTestId('photo-lightbox').getAttribute('data-open')).toBe('true')
  })

  it('queues chat-open hydration without route-time history fetch', async () => {
    renderViewer()

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith('/api/conversations/38/hydrate', { limit: 50 })
    })
    expect(
      mockApi.post.mock.calls.some(([path]) =>
        typeof path === 'string' && path.includes('/mark-read'),
      ),
    ).toBe(false)
  })

  it('shows hydration progress instead of empty copy when canonical rows lag dialog tail', async () => {
    mockUseInfiniteMessages.mockReturnValue({
      data: {
        pages: [
          {
            items: [],
            hydration: {
              schema_version: 'conversation_hydration_runtime.v1',
              state: 'queued',
              reason: 'chat_open',
              needed: true,
              can_retry: true,
              attempt_count: 0,
              max_attempts: 3,
              requested_count: 0,
              persisted_count: 0,
              duplicate_count: 0,
            },
          },
        ],
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
      hasPreviousPage: false,
      isFetchingPreviousPage: false,
      fetchPreviousPage: vi.fn(),
    })

    renderViewer()

    expect(screen.getByText('Telegramdan xabarlar yuklanmoqda...')).toBeTruthy()
    expect(screen.queryByText('Xabar yo‘q')).toBeNull()
  })

  it('polls pending hydration so websocket reconnect gaps still converge', async () => {
    vi.useFakeTimers()
    const refetch = vi.fn()
    mockUseInfiniteMessages.mockReturnValue({
      data: {
        pages: [
          {
            items: [],
            hydration: {
              schema_version: 'conversation_hydration_runtime.v1',
              state: 'running',
              reason: 'chat_open',
              needed: true,
              can_retry: true,
              attempt_count: 1,
              max_attempts: 3,
              requested_count: 0,
              persisted_count: 0,
              duplicate_count: 0,
            },
          },
        ],
      },
      isLoading: false,
      isError: false,
      refetch,
      hasPreviousPage: false,
      isFetchingPreviousPage: false,
      fetchPreviousPage: vi.fn(),
    })

    try {
      renderViewer()
      const initialCalls = refetch.mock.calls.length

      await act(async () => {
        vi.advanceTimersByTime(1_500)
      })

      expect(refetch.mock.calls.length).toBeGreaterThan(initialCalls)
    } finally {
      vi.useRealTimers()
    }
  })

  it('requests hydration again when the active canonical tail later reports idle recovery needed', async () => {
    let hookState = {
      data: {
        pages: [
          {
            items: [],
            hydration: null as null | Record<string, unknown>,
          },
        ],
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
      hasPreviousPage: false,
      isFetchingPreviousPage: false,
      fetchPreviousPage: vi.fn(),
    }
    mockUseInfiniteMessages.mockImplementation(() => hookState)

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <ChatViewer conversationId={38} />
      </QueryClientProvider>,
    )

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith('/api/conversations/38/hydrate', { limit: 50 })
    })
    mockApi.post.mockClear()

    hookState = {
      ...hookState,
      data: {
        pages: [
          {
            items: [],
            hydration: {
              schema_version: 'conversation_hydration_runtime.v1',
              state: 'idle',
              reason: 'chat_open',
              needed: true,
              can_retry: true,
              attempt_count: 0,
              max_attempts: 3,
              requested_count: 0,
              persisted_count: 0,
              duplicate_count: 0,
            },
          },
        ],
      },
    }

    rerender(
      <QueryClientProvider client={queryClient}>
        <ChatViewer conversationId={38} />
      </QueryClientProvider>,
    )

    expect(screen.getByText('Telegramdan xabarlar yuklanmoqda...')).toBeTruthy()
    expect(screen.queryByText('Xabar yo‘q')).toBeNull()
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith('/api/conversations/38/hydrate', { limit: 50 })
    })
  })

  it('scrolls to latest again when the active conversation changes', async () => {
    vi.useFakeTimers()
    mockUseInfiniteMessages.mockReturnValue({
      data: {
        pages: [
          {
            items: [
              {
                id: 101,
                conversation_id: 38,
                sender_type: 'customer',
                content: 'first chat',
                channel: 'telegram',
                is_read: true,
                created_at: '2026-04-23T06:00:00Z',
              },
            ],
          },
        ],
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
      hasPreviousPage: false,
      isFetchingPreviousPage: false,
      fetchPreviousPage: vi.fn(),
    })

    try {
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      })
      const { rerender } = render(
        <QueryClientProvider client={queryClient}>
          <ChatViewer conversationId={38} />
        </QueryClientProvider>,
      )

      await act(async () => {
        vi.advanceTimersByTime(100)
      })
      expect(scrollToIndexMock).toHaveBeenCalledTimes(1)
      expect(scrollToIndexMock).toHaveBeenLastCalledWith({
        index: 'LAST',
        align: 'end',
        behavior: 'auto',
      })

      rerender(
        <QueryClientProvider client={queryClient}>
          <ChatViewer conversationId={39} />
        </QueryClientProvider>,
      )

      await act(async () => {
        vi.advanceTimersByTime(100)
      })

      expect(scrollToIndexMock).toHaveBeenCalledTimes(2)
      expect(scrollToIndexMock).toHaveBeenLastCalledWith({
        index: 'LAST',
        align: 'end',
        behavior: 'auto',
      })
    } finally {
      vi.useRealTimers()
    }
  })

  it('re-anchors to latest when cached conversation messages are replaced by the canonical tail', async () => {
    vi.useFakeTimers()
    let hookState = {
      data: {
        pages: [
          {
            items: [
              {
                id: 101,
                conversation_id: 38,
                sender_type: 'customer',
                content: 'cached old page',
                channel: 'telegram',
                is_read: true,
                created_at: '2026-04-23T06:00:00Z',
              },
            ],
          },
        ],
      },
      dataUpdatedAt: 1,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
      hasPreviousPage: false,
      isFetchingPreviousPage: false,
      fetchPreviousPage: vi.fn(),
    }
    mockUseInfiniteMessages.mockImplementation(() => hookState)

    try {
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      })
      const { rerender } = render(
        <QueryClientProvider client={queryClient}>
          <ChatViewer conversationId={38} />
        </QueryClientProvider>,
      )

      await act(async () => {
        vi.advanceTimersByTime(100)
      })
      expect(scrollToIndexMock).toHaveBeenCalledTimes(1)
      expect(scrollToIndexMock).toHaveBeenLastCalledWith({
        index: 'LAST',
        align: 'end',
        behavior: 'auto',
      })

      hookState = {
        ...hookState,
        data: {
          pages: [
            {
              items: [
                ...hookState.data.pages[0].items,
                {
                  id: 102,
                  conversation_id: 38,
                  sender_type: 'customer',
                  content: 'canonical latest tail',
                  channel: 'telegram',
                  is_read: true,
                  created_at: '2026-04-23T06:05:00Z',
                },
              ],
            },
          ],
        },
        dataUpdatedAt: 2,
      }
      rerender(
        <QueryClientProvider client={queryClient}>
          <ChatViewer conversationId={38} />
        </QueryClientProvider>,
      )

      await act(async () => {
        vi.advanceTimersByTime(100)
      })

      expect(scrollToIndexMock).toHaveBeenCalledTimes(2)
      expect(scrollToIndexMock).toHaveBeenLastCalledWith({
        index: 'LAST',
        align: 'end',
        behavior: 'auto',
      })
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps refetching when hydration is ready but local message cache is empty', async () => {
    vi.useFakeTimers()
    const refetch = vi.fn()
    mockUseInfiniteMessages.mockReturnValue({
      data: {
        pages: [
          {
            items: [],
            hydration: {
              schema_version: 'conversation_hydration_runtime.v1',
              state: 'ready',
              reason: 'chat_open',
              needed: false,
              can_retry: false,
              attempt_count: 1,
              max_attempts: 3,
              requested_count: 3,
              persisted_count: 3,
              duplicate_count: 0,
            },
          },
        ],
      },
      isLoading: false,
      isError: false,
      refetch,
      hasPreviousPage: false,
      isFetchingPreviousPage: false,
      fetchPreviousPage: vi.fn(),
    })

    try {
      renderViewer()
      expect(refetch).toHaveBeenCalled()
      const initialCalls = refetch.mock.calls.length

      await act(async () => {
        vi.advanceTimersByTime(1_500)
      })

      expect(refetch.mock.calls.length).toBeGreaterThan(initialCalls)
    } finally {
      vi.useRealTimers()
    }
  })

  it('treats image documents as photos for lightbox continuity', async () => {
    mockUseInfiniteMessages.mockReturnValue({
      data: {
        pages: [
          {
            items: [
              {
                id: 101,
                conversation_id: 38,
                sender_type: 'customer',
                content: '[document]',
                channel: 'telegram',
                is_read: true,
                media_type: 'document',
                media_full_url: '/api/media/38/51',
                media_metadata: { mime_type: 'image/jpeg', file_name: 'photo.jpg' },
                created_at: '2026-04-23T06:00:00Z',
              },
            ],
          },
        ],
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
      hasPreviousPage: false,
      isFetchingPreviousPage: false,
      fetchPreviousPage: vi.fn(),
    })

    renderViewer()

    await waitFor(() => expect(screen.getByTestId('photo-lightbox')).toBeTruthy())
    const lightbox = screen.getByTestId('photo-lightbox')
    expect(lightbox.getAttribute('data-slides')).toBe('/api/media/38/51')
  })
})
