// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { uz } from '@/lib/uz'

vi.mock('@/lib/api-client', () => {
  class MockApiError extends Error {
    data?: unknown
  }

  return {
    ApiError: MockApiError,
    api: {
      get: vi.fn(),
      post: vi.fn(),
    },
  }
})

import { ApiError, api } from '@/lib/api-client'
import { PhoneAuth } from './phone-auth'

const mockedApi = vi.mocked(api)

describe('PhoneAuth QR flow', () => {
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('keeps polling status after QR 2FA password submission', async () => {
    const user = {
      userId: '42',
      phone: '+998991234567',
      firstName: 'Test',
      lastName: 'Seller',
    }
    let passwordSubmitted = false

    mockedApi.post.mockImplementation(async (path: string) => {
      if (path === '/api/telegram/auth/qr/check-2fa') {
        passwordSubmitted = true
        return { status: 'password_submitted' }
      }
      return { status: 'started' }
    })
    mockedApi.get.mockImplementation(async (path: string) => {
      if (path === '/api/telegram/auth/qr/status') {
        return passwordSubmitted
          ? { status: 'success', user }
          : { status: '2fa_required' }
      }
      return {
        svg: '<svg aria-label="qr"></svg>',
        tgUrl: 'tg://login?token=test',
        expired: false,
      }
    })

    const onSuccess = vi.fn()
    render(<PhoneAuth onSuccess={onSuccess} />)

    fireEvent.click(await screen.findByRole('button', { name: uz.connect.useQr }))
    const passwordInput = await screen.findByPlaceholderText(uz.connect.twoFaPrompt)
    fireEvent.change(passwordInput, { target: { value: 'secret' } })
    fireEvent.click(screen.getByRole('button', { name: uz.auth.signIn }))

    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith({ ...user, authMethod: 'qr' }), {
      timeout: 4000,
    })
    expect(mockedApi.post).toHaveBeenCalledWith('/api/telegram/auth/qr/check-2fa', {
      password: 'secret',
    })
    expect(mockedApi.get).toHaveBeenCalledWith('/api/telegram/auth/qr/status')
  })

  it('refreshes an expired QR code instead of leaving stale auth on screen', async () => {
    mockedApi.post.mockResolvedValue({ status: 'started' })
    mockedApi.get.mockImplementation(async (path: string) => {
      if (path === '/api/telegram/auth/qr/status') {
        return { status: 'waiting' }
      }
      return {
        svg: '<svg aria-label="expired-qr"></svg>',
        tgUrl: 'tg://login?token=expired',
        expired: true,
      }
    })

    render(<PhoneAuth onSuccess={vi.fn()} />)

    fireEvent.click(await screen.findByRole('button', { name: uz.connect.useQr }))
    await waitFor(
      () => {
        const startCalls = mockedApi.post.mock.calls.filter(
          ([path]) => path === '/api/telegram/auth/qr/start',
        )
        expect(startCalls.length).toBeGreaterThanOrEqual(2)
      },
      { timeout: 4000 },
    )
    await waitFor(() => expect(screen.getByText(uz.connect.qrExpired)).toBeDefined(), {
      timeout: 4000,
    })
  })

  it('does not restart QR auth when the parent rerenders during polling', async () => {
    mockedApi.post.mockResolvedValue({ status: 'started' })
    mockedApi.get.mockImplementation(async (path: string) => {
      if (path === '/api/telegram/auth/qr/status') {
        return { status: 'waiting' }
      }
      return {
        svg: '<svg aria-label="qr"></svg>',
        tgUrl: 'tg://login?token=stable',
        expired: false,
      }
    })

    function Harness() {
      const [renderCount, setRenderCount] = useState(0)
      return (
        <>
          <button type="button" onClick={() => setRenderCount((value) => value + 1)}>
            rerender {renderCount}
          </button>
          <PhoneAuth onSuccess={async () => {}} />
        </>
      )
    }

    render(<Harness />)

    fireEvent.click(await screen.findByRole('button', { name: uz.connect.useQr }))
    await waitFor(() => {
      expect(mockedApi.post.mock.calls.filter(([path]) => path === '/api/telegram/auth/qr/start')).toHaveLength(1)
    })

    fireEvent.click(screen.getByRole('button', { name: /rerender/i }))
    await new Promise((resolve) => window.setTimeout(resolve, 100))

    expect(mockedApi.post.mock.calls.filter(([path]) => path === '/api/telegram/auth/qr/start')).toHaveLength(1)
  })

  it('returns to phone auth instead of staying on success when bridge login fails', async () => {
    const user = {
      userId: '42',
      phone: '+998991234567',
      firstName: 'Test',
      lastName: 'Seller',
    }
    mockedApi.post.mockImplementation(async (path: string) => {
      if (path === '/api/telegram/auth/send-code') {
        return { phoneCodeHash: 'hash' }
      }
      if (path === '/api/telegram/auth/sign-in') {
        return { user }
      }
      return {}
    })

    const onSuccess = vi.fn().mockRejectedValue(new Error('bridge failed'))
    render(<PhoneAuth onSuccess={onSuccess} />)

    fireEvent.change(screen.getByPlaceholderText('+998 90 123 45 67'), {
      target: { value: '+998991234567' },
    })
    fireEvent.click(screen.getByRole('button', { name: uz.connect.sendCode }))

    const codeInput = await screen.findByPlaceholderText('12345')
    fireEvent.change(codeInput, { target: { value: '12345' } })
    fireEvent.click(screen.getByRole('button', { name: uz.connect.verify }))

    await waitFor(() => expect(screen.getByText(uz.connect.serviceDown)).toBeDefined())
    expect(screen.getByRole('button', { name: uz.connect.sendCode })).toBeDefined()
  })

  it('shows actionable copy when phone 2FA password is rejected', async () => {
    mockedApi.post.mockImplementation(async (path: string) => {
      if (path === '/api/telegram/auth/send-code') {
        return { phoneCodeHash: 'hash' }
      }
      if (path === '/api/telegram/auth/sign-in') {
        return { error: '2FA_REQUIRED' }
      }
      if (path === '/api/telegram/auth/check-2fa') {
        return { error: 'PASSWORD_HASH_INVALID' }
      }
      return {}
    })

    render(<PhoneAuth onSuccess={vi.fn()} />)

    fireEvent.change(screen.getByPlaceholderText('+998 90 123 45 67'), {
      target: { value: '+998991234567' },
    })
    fireEvent.click(screen.getByRole('button', { name: uz.connect.sendCode }))

    const codeInput = await screen.findByPlaceholderText('12345')
    fireEvent.change(codeInput, { target: { value: '12345' } })
    fireEvent.click(screen.getByRole('button', { name: uz.connect.verify }))

    const passwordInput = await screen.findByPlaceholderText('Parol')
    fireEvent.change(passwordInput, { target: { value: 'wrong-secret' } })
    fireEvent.click(screen.getByRole('button', { name: uz.auth.signIn }))

    await waitFor(() => expect(screen.getByText(uz.connect.wrongPassword)).toBeDefined())
  })

  it('does not show raw GramJS retry text when Telegram cannot send a phone code', async () => {
    mockedApi.post.mockResolvedValue({
      error: 'PHONE_CODE_SEND_FAILED',
      code: 'PHONE_CODE_SEND_FAILED',
      message: 'Request was unsuccessful 5 time(s)',
    })

    render(<PhoneAuth onSuccess={vi.fn()} />)

    fireEvent.change(screen.getByPlaceholderText('+998 90 123 45 67'), {
      target: { value: '+998901635207' },
    })
    fireEvent.click(screen.getByRole('button', { name: uz.connect.sendCode }))

    await waitFor(() => expect(screen.getByText(uz.connect.phoneCodeSendFailed)).toBeDefined())
    expect(screen.queryByText(/Request was unsuccessful/i)).toBeNull()
  })

  it('does not show raw sidecar outage text when Telegram service is down', async () => {
    const err = new ApiError(502, 'Bad Gateway')
    err.data = { detail: 'Sidecar unreachable' }
    mockedApi.post.mockRejectedValue(err)

    render(<PhoneAuth onSuccess={vi.fn()} />)

    fireEvent.change(screen.getByPlaceholderText('+998 90 123 45 67'), {
      target: { value: '+998901635207' },
    })
    fireEvent.click(screen.getByRole('button', { name: uz.connect.sendCode }))

    await waitFor(() => expect(screen.getByText(uz.connect.serviceDown)).toBeDefined())
    expect(screen.queryByText(/Sidecar unreachable/i)).toBeNull()
  })

  it('stops QR status polling after repeated sidecar outages', async () => {
    const err = new ApiError(502, 'Bad Gateway')
    err.data = { detail: 'Sidecar unreachable' }
    mockedApi.post.mockResolvedValue({ status: 'started' })
    mockedApi.get.mockRejectedValue(err)

    render(<PhoneAuth onSuccess={vi.fn()} />)

    fireEvent.click(await screen.findByRole('button', { name: uz.connect.useQr }))

    await waitFor(
      () => {
        expect(mockedApi.get.mock.calls.filter(([path]) => path === '/api/telegram/auth/qr/status').length).toBe(3)
      },
      { timeout: 6_000 },
    )

    const statusCallsAfterStop = mockedApi.get.mock.calls.filter(
      ([path]) => path === '/api/telegram/auth/qr/status',
    ).length

    expect(statusCallsAfterStop).toBe(3)
    expect(screen.getByText(uz.connect.serviceDown)).toBeDefined()

    await new Promise((resolve) => setTimeout(resolve, 2_500))

    expect(
      mockedApi.get.mock.calls.filter(([path]) => path === '/api/telegram/auth/qr/status').length,
    ).toBe(statusCallsAfterStop)
  }, 10_000)

  it('shows device-code unavailable copy when Telegram refuses app delivery', async () => {
    mockedApi.post.mockResolvedValue({
      error: 'DEVICE_CODE_UNAVAILABLE',
      code: 'DEVICE_CODE_UNAVAILABLE',
      message: 'Telegram did not offer app/device code delivery for this phone number.',
    })

    render(<PhoneAuth onSuccess={vi.fn()} />)

    fireEvent.change(screen.getByPlaceholderText('+998 90 123 45 67'), {
      target: { value: '+998901635207' },
    })
    fireEvent.click(screen.getByRole('button', { name: uz.connect.sendCode }))

    await waitFor(() => expect(screen.getByText(uz.connect.deviceCodeUnavailable)).toBeDefined())
  })

  it('shows Telegram delivery truth and keeps fallback manual when SMS is reported by older backends', async () => {
    mockedApi.post.mockResolvedValue({
      phoneCodeHash: 'hash',
      tempSessionId: 'temp-1',
      delivery: {
        type: 'auth.SentCodeTypeSms',
        nextType: 'auth.CodeTypeCall',
        timeoutSeconds: 90,
      },
    })

    render(<PhoneAuth onSuccess={vi.fn()} />)

    fireEvent.change(screen.getByPlaceholderText('+998 90 123 45 67'), {
      target: { value: '+998901635207' },
    })
    fireEvent.click(screen.getByRole('button', { name: uz.connect.sendCode }))

    await waitFor(() => expect(screen.getByText(/Telegram SMS orqali kod yuborilganini bildirdi/)).toBeDefined())
    expect(screen.getByRole('button', { name: /Keyingi yo'l 90s/ }).hasAttribute('disabled')).toBe(true)
    expect(screen.getByText(uz.connect.codeRecoveryWaiting)).toBeDefined()
    expect(screen.getByText(/faqat siz bosganingizda/)).toBeDefined()
  })

  it('calls out degraded delivery when app code was preferred', async () => {
    mockedApi.post.mockResolvedValue({
      phoneCodeHash: 'hash',
      tempSessionId: 'temp-1',
      delivery: {
        type: 'auth.SentCodeTypeSms',
        preferredType: 'auth.SentCodeTypeApp',
        degraded: true,
      },
    })

    render(<PhoneAuth onSuccess={vi.fn()} />)

    fireEvent.change(screen.getByPlaceholderText('+998 90 123 45 67'), {
      target: { value: '+998901635207' },
    })
    fireEvent.click(screen.getByRole('button', { name: uz.connect.sendCode }))

    await waitFor(() => {
      expect(screen.getByText(uz.connect.codeHintAppDegraded)).toBeDefined()
    })
  })

  it('shows app-code recovery guidance without fake resend when Telegram gives no next route', async () => {
    mockedApi.post.mockImplementation(async (path: string, body?: unknown) => {
      if (path !== '/api/telegram/auth/send-code') return {}
      const alternate = typeof body === 'object' && body !== null && (body as Record<string, unknown>).authTransport === 'tcp'
      return {
        phoneCodeHash: alternate ? 'hash-tcp' : 'hash',
        tempSessionId: alternate ? 'temp-2' : 'temp-1',
        delivery: {
          type: 'auth.SentCodeTypeApp',
          preferredType: 'auth.SentCodeTypeApp',
          degraded: false,
          authTransport: alternate ? 'tcp' : 'web',
        },
      }
    })

    render(<PhoneAuth onSuccess={vi.fn()} />)

    fireEvent.change(screen.getByPlaceholderText('+998 90 123 45 67'), {
      target: { value: '+998901635207' },
    })
    fireEvent.click(screen.getByRole('button', { name: uz.connect.sendCode }))

    await waitFor(() => {
      expect(screen.getByText(uz.connect.codeHintApp)).toBeDefined()
    })
    expect(screen.getByText(uz.connect.codeAppNoFallbackTitle)).toBeDefined()
    expect(screen.getByText(uz.connect.codeAppNoFallback1)).toBeDefined()
    expect(screen.getByRole('link', { name: uz.connect.openTelegramWeb }).getAttribute('href')).toBe(
      'https://web.telegram.org/k/',
    )
    expect(screen.queryByRole('button', { name: uz.connect.codeRecoveryReady })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: uz.connect.tryAlternateCodeRoute }))
    await waitFor(() => {
      expect(mockedApi.post).toHaveBeenCalledWith('/api/telegram/auth/send-code', {
        phone: '+998901635207',
        tempSessionId: 'temp-1',
        authTransport: 'tcp',
      })
    })
    expect(screen.queryByRole('button', { name: uz.connect.tryAlternateCodeRoute })).toBeNull()
  })

  it('does not automatically switch to the next Telegram delivery route after timeout', async () => {
    mockedApi.post.mockImplementation(async (path: string) => {
      if (path === '/api/telegram/auth/send-code') {
        return {
          phoneCodeHash: 'hash-1',
          tempSessionId: 'temp-1',
          delivery: {
            type: 'auth.SentCodeTypeSms',
            nextType: 'auth.CodeTypeCall',
            timeoutSeconds: 1,
          },
        }
      }
      if (path === '/api/telegram/auth/resend-code') {
        return {
          phoneCodeHash: 'hash-2',
          tempSessionId: 'temp-1',
          delivery: {
            type: 'auth.SentCodeTypeCall',
            timeoutSeconds: null,
          },
        }
      }
      return {}
    })

    render(<PhoneAuth onSuccess={vi.fn()} />)

    fireEvent.change(screen.getByPlaceholderText('+998 90 123 45 67'), {
      target: { value: '+998901635207' },
    })
    fireEvent.click(screen.getByRole('button', { name: uz.connect.sendCode }))

    await screen.findByText(uz.connect.codeRecoveryWaiting)

    await waitFor(() => {
      expect(screen.getByRole('button', { name: uz.connect.codeRecoveryReady })).toBeDefined()
    }, { timeout: 3000 })

    expect(mockedApi.post).not.toHaveBeenCalledWith('/api/telegram/auth/resend-code', {
      phone: '+998901635207',
      phoneCodeHash: 'hash-1',
      tempSessionId: 'temp-1',
    })
  }, 7000)

  it('keeps temp auth state explicit for sign-in instead of relying only on cookies', async () => {
    const user = {
      userId: '42',
      phone: '+998991234567',
      firstName: 'Test',
      lastName: 'Seller',
    }
    mockedApi.post.mockImplementation(async (path: string) => {
      if (path === '/api/telegram/auth/send-code') {
        return { phoneCodeHash: 'hash', tempSessionId: 'temp-1' }
      }
      if (path === '/api/telegram/auth/sign-in') {
        return { user }
      }
      return {}
    })

    render(<PhoneAuth onSuccess={vi.fn()} />)

    fireEvent.change(screen.getByPlaceholderText('+998 90 123 45 67'), {
      target: { value: '+998901635207' },
    })
    fireEvent.click(screen.getByRole('button', { name: uz.connect.sendCode }))

    const codeInput = await screen.findByPlaceholderText('12345')
    fireEvent.change(codeInput, { target: { value: '12345' } })
    fireEvent.click(screen.getByRole('button', { name: uz.connect.verify }))

    await waitFor(() => {
      expect(mockedApi.post).toHaveBeenCalledWith('/api/telegram/auth/sign-in', {
        phone: '+998901635207',
        phoneCodeHash: 'hash',
        code: '12345',
        tempSessionId: 'temp-1',
      })
    })
  })
})
