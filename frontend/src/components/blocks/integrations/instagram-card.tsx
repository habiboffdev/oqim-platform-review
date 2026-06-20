import { useRef } from 'react'
import { useSearch } from '@tanstack/react-router'
import { toast } from 'sonner'
import { uz } from '@/lib/uz'
import {
  useInstagramConnect,
  useInstagramConnectionStatus,
  useInstagramDisconnect,
} from '@/hooks/use-instagram-connection'
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

function InstagramGlyph({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.25"
      className={className}
      aria-hidden="true"
    >
      <rect x="3" y="3" width="18" height="18" rx="5" />
      <circle cx="12" cy="12" r="4" />
      <circle cx="17.2" cy="6.8" r="0.9" fill="currentColor" stroke="none" />
    </svg>
  )
}

export function InstagramCard() {
  const status = useInstagramConnectionStatus()
  const connect = useInstagramConnect()
  const disconnect = useInstagramDisconnect()

  // OAuth callback outcome: backend redirects to /integrations?instagram=...
  const search = useSearch({ strict: false }) as { instagram?: string }
  const callbackHandledRef = useRef(false)
  useMountEffect(() => {
    if (callbackHandledRef.current) return
    callbackHandledRef.current = true
    if (search.instagram === 'connected') {
      toast.success(uz.settings.instagramConnectedToast)
    } else if (search.instagram === 'error') {
      toast.error(uz.settings.instagramConnectError)
    } else if (search.instagram === 'already_connected') {
      toast.error(uz.settings.instagramAlreadyConnected)
    }
  })

  if (status.isLoading) {
    return (
      <Card className="rounded-lg" size="sm">
        <CardHeader className="border-b border-border/70">
          <CardTitle className="flex items-center gap-2">
            <InstagramGlyph className="size-4 text-muted-foreground" />
            {uz.settings.instagram}
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
          <InstagramGlyph className="size-4 text-muted-foreground" />
          {uz.settings.instagram}
          <Badge variant={connected && !needsReconnect ? 'success' : 'secondary'} size="sm">
            {connected
              ? needsReconnect
                ? uz.settings.reconnectRequired
                : uz.settings.connected
              : uz.settings.instagramNotConnected}
          </Badge>
        </CardTitle>
        <CardDescription>{uz.settings.instagramDescription}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 pt-4">
        {connected ? (
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm text-muted-foreground">{uz.settings.instagramConnected}</p>
            <div className="flex gap-2">
              {needsReconnect && (
                <Button size="sm" onClick={() => connect.mutate()} disabled={connect.isPending}>
                  {uz.settings.instagramReconnect}
                </Button>
              )}
              <Button
                size="sm"
                variant="outline"
                onClick={() => disconnect.mutate()}
                disabled={disconnect.isPending}
              >
                {uz.settings.instagramDisconnect}
              </Button>
            </div>
          </div>
        ) : (
          <Button size="sm" onClick={() => connect.mutate()} disabled={connect.isPending}>
            {uz.settings.instagramConnect}
          </Button>
        )}
        <p className="text-xs text-muted-foreground">{uz.settings.instagramPilotHint}</p>
      </CardContent>
    </Card>
  )
}
