import { TelegramLogo, ArrowClockwise, CheckCircle, Warning, CircleNotch } from '@phosphor-icons/react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { useTelegramConnectionStatus } from '@/hooks/use-telegram-connection-status'

const STATE_CONFIG: Record<string, { label: string; color: string; icon: typeof CheckCircle }> = {
  connected: { label: "Ulangan", color: "text-green-600", icon: CheckCircle },
  disconnected: { label: "Uzilgan", color: "text-muted-foreground", icon: Warning },
  connecting: { label: "Ulanmoqda...", color: "text-amber-500", icon: CircleNotch },
  reconnecting: { label: "Qayta ulanmoqda...", color: "text-amber-500", icon: CircleNotch },
  degraded: { label: "Muammo bor", color: "text-amber-500", icon: Warning },
  failed: { label: "Xatolik", color: "text-destructive", icon: Warning },
  revoked: { label: "Qayta ulash kerak", color: "text-destructive", icon: Warning },
  stale: { label: "Eskirgan sessiya", color: "text-amber-500", icon: Warning },
}

export function TelegramStatus() {
  const { data: status, refetch } = useTelegramConnectionStatus()

  const state = status?.state ?? 'connecting'
  const cfg = STATE_CONFIG[state] ?? STATE_CONFIG.disconnected
  const Icon = cfg.icon
  const needsReconnect = state !== 'connected' && state !== 'connecting' && state !== 'reconnecting'

  return (
    <div className="rounded-lg border p-4">
      <div className="flex items-center gap-3">
        <TelegramLogo size={24} weight="thin" className="text-muted-foreground" />
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">Telegram</span>
            <span className={cn("flex items-center gap-1 text-xs", cfg.color)}>
              <Icon size={12} weight={cfg.icon === CircleNotch ? "bold" : "thin"}
                className={cfg.icon === CircleNotch ? "animate-spin" : ""} />
              {cfg.label}
            </span>
          </div>
          {status?.phone && (
            <p className="text-xs text-muted-foreground">{status.phone}</p>
          )}
        </div>
        <div className="flex items-center gap-1">
          {needsReconnect && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => { window.location.href = '/onboarding?reconnect=telegram' }}
            >
              Qayta ulash
            </Button>
          )}
          <Button variant="ghost" size="icon" onClick={() => refetch()} className="size-8">
            <ArrowClockwise size={14} weight="thin" />
          </Button>
        </div>
      </div>
    </div>
  )
}
