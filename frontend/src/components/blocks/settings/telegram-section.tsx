import { TelegramLogo } from '@phosphor-icons/react'
import { useNavigate } from '@tanstack/react-router'
import { Badge } from '@/components/primitives/badge'
import { Button } from '@/components/ui/button'
import { useAuth } from '@/lib/auth-context'
import { useTelegramConnectionStatus } from '@/hooks/use-telegram-connection-status'
import { uz } from '@/lib/uz'

export function TelegramSection() {
  const { user, isLoading } = useAuth()
  const { data: status, isLoading: isStatusLoading } = useTelegramConnectionStatus()
  const navigate = useNavigate()

  const state = status?.state ?? 'disconnected'
  const connected = state === 'connected'
  const needsReconnect = status?.needsReconnect ?? false
  const telegramIssueText = status?.identityMismatch
    ? uz.settings.telegramIdentityMismatch
    : status?.lastError === 'telegram_identity_unverified'
      ? uz.settings.telegramIdentityUnverified
      : status?.lastError
        ? uz.settings.telegramConnectionIssue
        : null
  const statusLabel = {
    connected: uz.settings.connected,
    disconnected: uz.settings.notConnected,
    connecting: uz.settings.reconnecting,
    reconnecting: uz.settings.reconnecting,
    degraded: uz.settings.degraded,
    failed: uz.settings.failed,
    revoked: uz.settings.reconnectRequired,
    stale: uz.settings.reconnectRequired,
  }[state] ?? uz.settings.notConnected
  const effectiveStatusLabel = needsReconnect ? uz.settings.reconnectRequired : statusLabel
  const connectedPhone = status?.phone ?? user?.phone_number
  const showLoading = isLoading || isStatusLoading

  return (
    <section className="rounded-xl border border-border bg-card p-5">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <TelegramLogo size={18} weight="thin" className="text-muted-foreground" />
          <h2 className="text-sm font-medium">{uz.settings.telegram}</h2>
        </div>
        {!showLoading && (
          <Badge variant={connected ? 'success' : needsReconnect || state !== 'disconnected' ? 'warning' : 'muted'}>
            {effectiveStatusLabel}
          </Badge>
        )}
      </div>

      {showLoading ? (
        <div className="h-9 animate-pulse rounded-lg bg-muted" />
      ) : connected || state === 'degraded' ? (
        <div className="flex items-center justify-between">
          <div className="space-y-1">
            <p className="text-sm text-muted-foreground">{uz.settings.telegramConnected}</p>
            {connectedPhone && (
              <p className="text-sm font-medium">{connectedPhone}</p>
            )}
            {state === 'degraded' && telegramIssueText && (
              <p className="text-xs text-amber-600">{telegramIssueText}</p>
            )}
          </div>
          <Button
            size="sm"
            variant="outline"
            onClick={() => navigate({ to: '/onboarding' })}
          >
            {uz.settings.reconnect}
          </Button>
        </div>
      ) : state === 'connecting' || state === 'reconnecting' ? (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">{uz.settings.reconnecting}</p>
          {telegramIssueText && (
            <p className="text-xs text-amber-600">{telegramIssueText}</p>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            {needsReconnect ? uz.settings.telegramSessionExpired : uz.settings.telegramNotConnected}
          </p>
          {needsReconnect && telegramIssueText && (
            <p className="text-xs text-amber-600">{telegramIssueText}</p>
          )}
          <Button
            size="sm"
            onClick={() => navigate({ to: '/onboarding' })}
          >
            {needsReconnect ? uz.settings.reconnect : uz.settings.connect}
          </Button>
        </div>
      )}
    </section>
  )
}
