// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, act, waitFor, fireEvent } from '@testing-library/react'
import { AuthProvider, useAuth } from './auth-context'

const { mockWsManager } = vi.hoisted(() => ({
  mockWsManager: {
    disconnect: vi.fn(),
  },
}))

// Mock api-client so we don't make real HTTP calls
vi.mock('./api-client', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
  ApiError: class ApiError extends Error {
    status: number
    constructor(status: number, msg: string) {
      super(msg)
      this.name = 'ApiError'
      this.status = status
    }
  },
}))

// Mock use-mount-effect to call callback immediately
vi.mock('@/hooks/use-mount-effect', () => ({
  useMountEffect: (fn: () => void) => {
    // Call synchronously in tests
    fn()
  },
}))

vi.mock('./websocket', () => ({
  wsManager: mockWsManager,
}))

import { api, ApiError } from './api-client'

const mockApi = api as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
}

function sessionProjection(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: 'auth_session_projection.v1',
    authenticated: true,
    workspace: {
      id: 1,
      phone_number: '+998901234567',
      name: 'Alisher',
      telegram_connected: true,
      onboarding_completed: true,
    },
    onboarding_completed: true,
    integrations: [
      {
        provider: 'telegram_personal',
        state: 'connected',
        identity_linked: true,
        durable_connected: true,
        needs_reconnect: false,
        source: 'workspace_projection',
        live_state: 'not_checked',
      },
    ],
    ...overrides,
  }
}

function TestConsumer() {
  const { user, isAuthenticated, isLoading, authError } = useAuth()
  return (
    <div>
      <span data-testid="loading">{String(isLoading)}</span>
      <span data-testid="authenticated">{String(isAuthenticated)}</span>
      <span data-testid="user-name">{user?.name ?? 'none'}</span>
      <span data-testid="auth-error">{authError?.message ?? 'none'}</span>
    </div>
  )
}

describe('AuthProvider', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('starts with isLoading=true and isAuthenticated=false', async () => {
    // Never resolves — keeps loading state
    mockApi.get.mockImplementation(() => new Promise(() => {}))

    const { getByTestId } = render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    )

    expect(getByTestId('loading').textContent).toBe('true')
    expect(getByTestId('authenticated').textContent).toBe('false')
  })

  it('sets user data after successful fetchMe', async () => {
    mockApi.get.mockResolvedValue(sessionProjection())

    const { getByTestId } = render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    )

    await waitFor(() => {
      expect(getByTestId('loading').textContent).toBe('false')
    })

    expect(getByTestId('authenticated').textContent).toBe('true')
    expect(getByTestId('user-name').textContent).toBe('Alisher')
  })

  it('stays unauthenticated on 401 from /api/auth/session', async () => {
    mockApi.get.mockRejectedValue(new ApiError(401, 'Unauthorized'))

    const { getByTestId } = render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    )

    await waitFor(() => {
      expect(getByTestId('loading').textContent).toBe('false')
    })

    expect(getByTestId('authenticated').textContent).toBe('false')
    expect(getByTestId('user-name').textContent).toBe('none')
  })

  it('clears a stale deleted workspace session on 404 from /api/auth/session', async () => {
    mockApi.get.mockRejectedValue(new ApiError(404, 'Workspace not found'))
    mockApi.post.mockResolvedValue({})

    const { getByTestId } = render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    )

    await waitFor(() => {
      expect(getByTestId('loading').textContent).toBe('false')
    })

    expect(getByTestId('authenticated').textContent).toBe('false')
    expect(getByTestId('user-name').textContent).toBe('none')
    expect(getByTestId('auth-error').textContent).toBe('none')
    expect(mockApi.post).toHaveBeenCalledWith('/api/auth/logout')
    expect(mockWsManager.disconnect).toHaveBeenCalled()
  })

  it('throws when useAuth is used outside AuthProvider', () => {
    // Suppress expected error output in test
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})

    expect(() => {
      render(<TestConsumer />)
    }).toThrow('useAuth must be used within AuthProvider')

    consoleError.mockRestore()
  })
})

describe('AuthProvider login flow', () => {
  it('calls /api/auth/login then /api/auth/session after login', async () => {
    // First call: initial auth check, second call: post-login session refresh.
    mockApi.post.mockResolvedValue({})
    mockApi.get
      .mockRejectedValueOnce(new ApiError(401, 'Unauthorized'))
      .mockResolvedValueOnce(sessionProjection({
        workspace: {
          id: 2,
          phone_number: '+998',
          name: 'Test',
          telegram_connected: false,
          onboarding_completed: false,
        },
        onboarding_completed: false,
        integrations: [],
      })) // post-login fetchSession

    function LoginTester() {
      const { login, user } = useAuth()
      return (
        <div>
          <button onClick={() => login('+998', 'pass')}>Login</button>
          <span data-testid="user">{user?.name ?? 'none'}</span>
        </div>
      )
    }

    const { getByText } = render(
      <AuthProvider>
        <LoginTester />
      </AuthProvider>,
    )

    await act(async () => {
      fireEvent.click(getByText('Login'))
    })

    expect(mockApi.post).toHaveBeenCalledWith('/api/auth/login', {
      phone_number: '+998',
      password: 'pass',
    })
  })
})

describe('AuthProvider logout flow', () => {
  it('disconnects the websocket singleton on logout', async () => {
    mockApi.get.mockRejectedValue(new ApiError(401, 'Unauthorized'))
    mockApi.post.mockResolvedValue({})

    function LogoutTester() {
      const { logout } = useAuth()
      return (
        <button onClick={logout}>Logout</button>
      )
    }

    render(
      <AuthProvider>
        <LogoutTester />
      </AuthProvider>,
    )

    await act(async () => {
      fireEvent.click(screen.getByText('Logout'))
    })

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith('/api/auth/logout')
      expect(mockWsManager.disconnect).toHaveBeenCalled()
    })
  })
})
