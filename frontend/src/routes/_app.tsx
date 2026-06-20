import { useEffect } from 'react'
import { Outlet, useLocation, useNavigate, useParams } from '@tanstack/react-router'
import { AppShell } from '@/components/blocks/app-shell'
import { ErrorBoundary } from '@/components/primitives/error-boundary'
import { Spinner } from '@/components/ui/spinner'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { useAuth } from '@/lib/auth-context'
import { useWebSocket } from '@/hooks/use-websocket'
import { activeConversationIdFromRoute } from '@/lib/active-conversation-route'
import { uz } from '@/lib/uz'

export function AppLayout() {
  const { authError, isAuthenticated, isLoading, refreshUser, user } = useAuth()

  const params = useParams({ strict: false }) as { conversationId?: string }
  const location = useLocation()
  const navigate = useNavigate()
  const activeConversationId = activeConversationIdFromRoute({
    pathname: location.pathname,
    param: params.conversationId,
  })
  const workspaceReady = isAuthenticated && Boolean(user?.onboarding_completed)
  useWebSocket(workspaceReady ? activeConversationId : undefined, { enabled: workspaceReady })

  useEffect(() => {
    if (isLoading) return
    if (isAuthenticated && user?.onboarding_completed) return
    if (location.pathname === '/onboarding') return

    void navigate({ to: '/onboarding', replace: true })
  }, [isAuthenticated, isLoading, location.pathname, navigate, user?.onboarding_completed])

  if (isLoading) {
    return (
      <div className="flex h-svh items-center justify-center">
        <Spinner size="lg" />
      </div>
    )
  }

  if (!isAuthenticated && authError) {
    return (
      <div className="flex h-svh items-center justify-center bg-background px-4">
        <Alert className="w-full max-w-sm">
          <AlertTitle>{uz.auth.networkError}</AlertTitle>
          <AlertDescription>{uz.onboarding.serviceDown}</AlertDescription>
          <Button
            type="button"
            onClick={() => {
              void refreshUser()
            }}
            className="mt-4"
          >
            {uz.conversations.retry}
          </Button>
        </Alert>
      </div>
    )
  }

  if (!isAuthenticated || !user?.onboarding_completed) {
    return (
      <div className="flex h-svh items-center justify-center">
        <Spinner size="lg" />
      </div>
    )
  }

  return (
    <AppShell>
      <ErrorBoundary>
        <div className="h-full overflow-hidden bg-background">
          <Outlet />
        </div>
      </ErrorBoundary>
    </AppShell>
  )
}
