// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ReactNode } from 'react'

import { useSendMessage } from './use-send-message'
import { queryKeys } from '@/lib/query-keys'
import { api } from '@/lib/api-client'

vi.mock('@/lib/api-client', () => ({
  api: {
    post: vi.fn(),
  },
}))

const mockApi = api as unknown as { post: ReturnType<typeof vi.fn> }

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
}

function createWrapper(queryClient: QueryClient) {
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: queryClient }, children)
}

describe('useSendMessage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('uses the same generated client uuid for optimistic state and the API request', async () => {
    const queryClient = createQueryClient()
    mockApi.post.mockImplementation(async (_url, body: { client_message_uuid: string }) => ({
      id: 202,
      conversation_id: 38,
      sender_type: 'seller',
      content: 'Bor',
      channel: 'telegram_dm',
      is_read: true,
      created_at: '2026-04-22T10:01:00Z',
      client_message_uuid: body.client_message_uuid,
      delivery_state: 'confirmed',
    }))

    const { result } = renderHook(() => useSendMessage(), {
      wrapper: createWrapper(queryClient),
    })

    await act(async () => {
      await result.current.mutateAsync({
        conversationId: 38,
        content: 'Bor',
      })
    })

    const apiBody = mockApi.post.mock.calls[0]?.[1] as { client_message_uuid: string }
    const data = queryClient.getQueryData(queryKeys.messages.list(38)) as {
      pages: Array<{ items: Array<{ id: number; client_message_uuid?: string }> }>
    }

    expect(apiBody.client_message_uuid).toBeTruthy()
    expect(data.pages.at(-1)?.items).toEqual([
      expect.objectContaining({
        id: 202,
        client_message_uuid: apiBody.client_message_uuid,
      }),
    ])
  })

  it('replaces the optimistic message with the server response', async () => {
    const queryClient = createQueryClient()
    const clientMessageUuid = 'send-uuid-1'
    queryClient.setQueryData(queryKeys.messages.list(38), {
      pages: [{
        items: [{
          id: 7,
          conversation_id: 38,
          sender_type: 'customer',
          content: 'Salom',
          channel: 'telegram_dm',
          is_read: false,
          created_at: '2026-04-22T10:00:00Z',
        }],
        has_older: false,
      }],
      pageParams: [undefined],
    })
    mockApi.post.mockResolvedValue({
      id: 101,
      conversation_id: 38,
      sender_type: 'seller',
      content: 'Bor',
      channel: 'telegram_dm',
      is_read: true,
      created_at: '2026-04-22T10:01:00Z',
      client_message_uuid: clientMessageUuid,
      delivery_state: 'confirmed',
    })

    const { result } = renderHook(() => useSendMessage(), {
      wrapper: createWrapper(queryClient),
    })

    await act(async () => {
      await result.current.mutateAsync({
        conversationId: 38,
        content: 'Bor',
        clientMessageUuid,
      })
    })

    await waitFor(() => {
      const data = queryClient.getQueryData(queryKeys.messages.list(38)) as {
        pages: Array<{ items: Array<{ id: number; client_message_uuid?: string; content: string }> }>
      }
      expect(data.pages.at(-1)?.items).toEqual([
        {
          id: 7,
          conversation_id: 38,
          sender_type: 'customer',
          content: 'Salom',
          channel: 'telegram_dm',
          is_read: false,
          created_at: '2026-04-22T10:00:00Z',
        },
        {
          id: 101,
          conversation_id: 38,
          sender_type: 'seller',
          content: 'Bor',
          channel: 'telegram_dm',
          is_read: true,
          created_at: '2026-04-22T10:01:00Z',
          client_message_uuid: clientMessageUuid,
          delivery_state: 'confirmed',
        },
      ])
    })
  })

  it('marks the optimistic message as pending before the API resolves', async () => {
    const queryClient = createQueryClient()
    let resolveSend!: (message: unknown) => void
    mockApi.post.mockReturnValue(new Promise((resolve) => {
      resolveSend = resolve
    }))

    const { result } = renderHook(() => useSendMessage(), {
      wrapper: createWrapper(queryClient),
    })

    act(() => {
      result.current.mutate({
        conversationId: 38,
        content: 'Bor',
        clientMessageUuid: 'pending-send-uuid',
      })
    })

    await waitFor(() => {
      const data = queryClient.getQueryData(queryKeys.messages.list(38)) as {
        pages: Array<{ items: Array<{ client_message_uuid?: string; delivery_state?: string }> }>
      }
      expect(data.pages.at(-1)?.items.at(-1)).toEqual(expect.objectContaining({
        client_message_uuid: 'pending-send-uuid',
        delivery_state: 'pending',
      }))
    })

    await act(async () => {
      resolveSend({
        id: 101,
        conversation_id: 38,
        sender_type: 'seller',
        content: 'Bor',
        channel: 'telegram_dm',
        is_read: true,
        created_at: '2026-04-22T10:01:00Z',
        client_message_uuid: 'pending-send-uuid',
        delivery_state: 'confirmed',
      })
    })
  })

  it('keeps the local send as unknown when the response is lost after submit', async () => {
    const queryClient = createQueryClient()
    mockApi.post.mockRejectedValue(new TypeError('Failed to fetch'))

    const { result } = renderHook(() => useSendMessage(), {
      wrapper: createWrapper(queryClient),
    })

    await act(async () => {
      await result.current.mutateAsync({
        conversationId: 38,
        content: "Telegram olgan bo'lishi mumkin",
        clientMessageUuid: 'lost-response-send-uuid',
      })
    })

    const data = queryClient.getQueryData(queryKeys.messages.list(38)) as {
      pages: Array<{
        items: Array<{
          client_message_uuid?: string
          delivery_state?: string
          delivery_runtime?: { customer_status?: string; next_action?: string }
          content: string
        }>
      }>
    }
    expect(data.pages.at(-1)?.items.at(-1)).toEqual(expect.objectContaining({
      client_message_uuid: 'lost-response-send-uuid',
      content: "Telegram olgan bo'lishi mumkin",
      delivery_state: 'unknown',
      delivery_runtime: expect.objectContaining({
        customer_status: 'uncertain',
        next_action: 'reconcile',
      }),
    }))
  })

  it('rolls back the optimistic message when send fails', async () => {
    const queryClient = createQueryClient()
    queryClient.setQueryData(queryKeys.messages.list(38), {
      pages: [{
        items: [{
          id: 7,
          conversation_id: 38,
          sender_type: 'customer',
          content: 'Salom',
          channel: 'telegram_dm',
          is_read: false,
          created_at: '2026-04-22T10:00:00Z',
        }],
        has_older: false,
      }],
      pageParams: [undefined],
    })
    mockApi.post.mockRejectedValue(new Error('boom'))

    const { result } = renderHook(() => useSendMessage(), {
      wrapper: createWrapper(queryClient),
    })

    await expect(
      act(async () => {
        await result.current.mutateAsync({
          conversationId: 38,
          content: 'Bor',
          clientMessageUuid: 'send-uuid-2',
        })
      }),
    ).rejects.toThrow('boom')

    const data = queryClient.getQueryData(queryKeys.messages.list(38)) as {
      pages: Array<{ items: Array<{ id: number; content: string }> }>
    }
    expect(data.pages[0]?.items).toEqual([
      {
        id: 7,
        conversation_id: 38,
        sender_type: 'customer',
        content: 'Salom',
        channel: 'telegram_dm',
        is_read: false,
        created_at: '2026-04-22T10:00:00Z',
      },
    ])
  })
})
