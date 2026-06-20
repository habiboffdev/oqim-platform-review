/* eslint-disable react-refresh/only-export-components */
import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from 'react'
import { useMountEffect } from '@/hooks/use-mount-effect'
import { api, ApiError } from './api-client'
import { wsManager } from './websocket'
import type { AuthSessionProjection, User, Workspace } from './types'

interface AuthContext {
  user: User | null
  session: AuthSessionProjection | null
  isLoading: boolean
  authError: Error | null
  isAuthenticated: boolean
  login: (phone: string, password: string) => Promise<void>
  register: (phone: string, password: string, name: string) => Promise<void>
  logout: () => void
  refreshUser: () => Promise<void>
}

const AuthCtx = createContext<AuthContext | null>(null)

function isAuthSessionPayload(value: unknown): value is AuthSessionProjection {
  return (
    !!value
    && typeof value === 'object'
    && (value as AuthSessionProjection).schema_version === 'auth_session_projection.v1'
    && !!(value as AuthSessionProjection).workspace
    && typeof (value as AuthSessionProjection).workspace.id === 'number'
  )
}

function workspaceToUser(workspace: Workspace, session: AuthSessionProjection): User {
  const telegram = session.integrations.find((item) => item.provider === 'telegram_personal')
  return {
    id: workspace.id,
    phone_number: workspace.phone_number || '',
    name: workspace.name || '',
    full_name: workspace.name || '',
    workspace_id: workspace.id,
    platform_role: session.platform_role,
    is_founder: session.is_founder,
    type: workspace.type,
    monthly_revenue_band: workspace.monthly_revenue_band,
    subscription_tier: workspace.subscription_tier,
    onboarding_completed: session.onboarding_completed,
    telegram_connected: telegram?.durable_connected ?? workspace.telegram_connected ?? false,
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [session, setSession] = useState<AuthSessionProjection | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [authError, setAuthError] = useState<Error | null>(null)

  const clearLocalSession = useCallback(async () => {
    try {
      await api.post('/api/auth/logout')
    } catch {
      // Best effort: frontend state must still become safe if cookie clearing fails.
    }
    wsManager.disconnect()
    setUser(null)
    setSession(null)
    setAuthError(null)
  }, [])

  const fetchSession = useCallback(async () => {
    try {
      const data = await api.get<AuthSessionProjection>('/api/auth/session')
      if (!isAuthSessionPayload(data)) {
        throw new Error('Invalid auth session response')
      }
      setAuthError(null)
      setSession(data)
      setUser(workspaceToUser(data.workspace, data))
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setUser(null)
        setSession(null)
        setAuthError(null)
      } else if (err instanceof ApiError && err.status === 404) {
        await clearLocalSession()
      } else {
        const error = err instanceof Error ? err : new Error('Auth check failed')
        setAuthError(error)
        // Keep the current session state during backend restarts/network blips.
        console.error('fetchSession failed:', err)
      }
    } finally {
      setIsLoading(false)
    }
  }, [clearLocalSession])

  useMountEffect(() => {
    fetchSession()
  })

  const login = useCallback(async (phone: string, password: string) => {
    await api.post('/api/auth/login', {
      phone_number: phone,
      password,
    })
    await fetchSession()
  }, [fetchSession])

  const register = useCallback(
    async (phone: string, password: string, name: string) => {
      await api.post('/api/auth/register', {
        phone_number: phone,
        password,
        name,
      })
      await fetchSession()
    },
    [fetchSession],
  )

  const refreshUser = useCallback(async () => {
    await fetchSession()
  }, [fetchSession])

  const logout = useCallback(async () => {
    await clearLocalSession()
  }, [clearLocalSession])

  return (
    <AuthCtx.Provider
      value={{
        user,
        session,
        isLoading,
        authError,
        isAuthenticated: !!user,
        login,
        register,
        logout,
        refreshUser,
      }}
    >
      {children}
    </AuthCtx.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthCtx)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
