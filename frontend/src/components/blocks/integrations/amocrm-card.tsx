import { useRef } from 'react'
import { useSearch } from '@tanstack/react-router'
import { toast } from 'sonner'
import { uz } from '@/lib/uz'
import {
  useAmoCrmConnect,
  useAmoCrmConnectionStatus,
  useAmoCrmDisconnect,
} from '@/hooks/use-amocrm-connection'
import { useMountEffect } from '@/hooks/use-mount-effect'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'

function AmoCrmGlyph({ className }: { className?: string }) {
  // Sales-funnel mark: amoCRM is pipeline-first (lead -> stages).
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.25"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <path d="M4 5h16l-6 7.5V19l-4-2.2v-4.3z" />
    </svg>
  )
}

export function AmoCrmCard() {
  const status = useAmoCrmConnectionStatus()
  const connect = useAmoCrmConnect()
  const disconnect = useAmoCrmDisconnect()

  // OAuth callback outcome: backend redirects to /integrations?amocrm=...
  const search = useSearch({ strict: false }) as { amocrm?: string }
  const callbackHandledRef = useRef(false)
  useMountEffect(() => {
    if (callbackHandledRef.current) return
    callbackHandledRef.current = true
    if (search.amocrm === 'connected') {
      toast.success(uz.settings.amocrmConnectedToast)
    } else if (search.amocrm === 'error') {
      toast.error(uz.settings.amocrmConnectError)
    } else if (search.amocrm === 'already_connected') {
      toast.error(uz.settings.amocrmAlreadyConnected)
    }
  })

  if (status.isLoading) {
    return (
      <Card className="rounded-lg" size="sm">
        <CardHeader className="border-b border-border/70">
          <CardTitle className="flex items-center gap-2">
            <AmoCrmGlyph className="size-4 text-muted-foreground" />
            {uz.settings.amocrm}
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-4">
          <div className="h-9 animate-pulse rounded-lg bg-muted" />
        </CardContent>
      </Card>
    )
  }

  const connected = status.data?.connected ?? false
  const needsReconnect = status.data?.needs_reconnect ?? false

  return (
    <Card className="rounded-lg" size="sm">
      <CardHeader className="border-b border-border/70">
        <CardTitle className="flex items-center gap-2">
          <AmoCrmGlyph className="size-4 text-muted-foreground" />
          {uz.settings.amocrm}
          <Badge variant={connected && !needsReconnect ? 'success' : 'secondary'} size="sm">
            {connected
              ? needsReconnect
                ? uz.settings.reconnectRequired
                : uz.settings.connected
              : uz.settings.amocrmNotConnected}
          </Badge>
        </CardTitle>
        <CardDescription>{uz.settings.amocrmDescription}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 pt-4">
        {connected ? (
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm text-muted-foreground">{uz.settings.amocrmConnected}</p>
            <div className="flex gap-2">
              {needsReconnect && (
                <Button size="sm" onClick={() => connect.mutate()} disabled={connect.isPending}>
                  {uz.settings.amocrmReconnect}
                </Button>
              )}
              <Button
                size="sm"
                variant="outline"
                onClick={() => disconnect.mutate()}
                disabled={disconnect.isPending}
              >
                {uz.settings.amocrmDisconnect}
              </Button>
            </div>
          </div>
        ) : (
          <Button size="sm" onClick={() => connect.mutate()} disabled={connect.isPending}>
            {uz.settings.amocrmConnect}
          </Button>
        )}
      </CardContent>
    </Card>
  )
}
