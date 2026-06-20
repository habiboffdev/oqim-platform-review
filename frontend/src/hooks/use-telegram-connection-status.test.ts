// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ReactNode } from 'react'

import { useTelegramConnectionStatus } from './use-telegram-connection-status'

vi.mock('@/lib/api-client', () => ({
  api: {
    get: vi.fn(),
  },
}))

import { api } from '@/lib/api-client'

const mockApi = api as unknown as { get: ReturnType<typeof vi.fn> }

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: queryClient }, children)
}

describe('useTelegramConnectionStatus', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fetches live telegram status from the canonical endpoint', async () => {
    mockApi.get.mockResolvedValue({
      state: 'connected',
      workspaceId: 1,
      userId: '42',
      phone: '+998991234567',
      reconnectAttempts: 0,
    })

    const wrapper = createWrapper()
    const { result } = renderHook(() => useTelegramConnectionStatus(), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockApi.get).toHaveBeenCalledWith('/api/telegram/auth/status')
    expect(result.current.data?.state).toBe('connected')
  })

  it('preserves revoked session state as reconnectable runtime truth', async () => {
    mockApi.get.mockResolvedValue({
      state: 'revoked',
      workspaceId: 1,
      userId: null,
      phone: null,
      reconnectAttempts: 1,
      identityLinked: true,
      needsReconnect: true,
      lastError: 'SESSION_REVOKED',
    })

    const wrapper = createWrapper()
    const { result } = renderHook(() => useTelegramConnectionStatus(), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(result.current.data?.state).toBe('revoked')
    expect(result.current.data?.needsReconnect).toBe(true)
  })

  it('preserves Telegram identity mismatch as stale reconnect truth', async () => {
    mockApi.get.mockResolvedValue({
      state: 'stale',
      workspaceId: 1,
      userId: null,
      phone: null,
      reconnectAttempts: 0,
      identityLinked: true,
      identityMismatch: true,
      identityVerified: false,
      needsReconnect: true,
      lastError: 'telegram_identity_mismatch',
    })

    const wrapper = createWrapper()
    const { result } = renderHook(() => useTelegramConnectionStatus(), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(result.current.data?.state).toBe('stale')
    expect(result.current.data?.identityMismatch).toBe(true)
    expect(result.current.data?.needsReconnect).toBe(true)
  })
})
