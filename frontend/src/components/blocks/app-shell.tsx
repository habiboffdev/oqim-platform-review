import { useState, type ReactNode } from 'react'
import { Link, useLocation } from '@tanstack/react-router'
import {
  BrainIcon,
  CaretDownIcon,
  ChatIcon,
  ChecklistIcon,
  DatabaseIcon,
  GearIcon,
  KanbanIcon,
  PlugIcon,
  RobotIcon,
  SearchIcon,
  SendIcon,
  SparkIcon,
  type IconComponent,
} from '@/components/icons/nav-icons'
import { BottomNav } from './bottom-nav'
import { RightRail } from './right-rail'
import { StatusBar } from './activity/status-bar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { useAuth } from '@/lib/auth-context'
import { useTelegramConnectionStatus } from '@/hooks/use-telegram-connection-status'
import { uz } from '@/lib/uz'
import { cn } from '@/lib/utils'

type NavTo = '/conversations' | '/brain' | '/sources' | '/agents' | '/intelligence' | '/actions' | '/tasks' | '/integrations' | '/settings' | '/crm'

type NavItem = {
  icon: IconComponent
  to: NavTo
  key: 'conversations' | 'brain' | 'sources' | 'agents' | 'intelligence' | 'actions' | 'tasks' | 'integrations' | 'settings' | 'crm'
}

const NAV: NavItem[] = [
  { icon: ChatIcon, to: '/conversations', key: 'conversations' },
  { icon: BrainIcon, to: '/brain', key: 'brain' },
  { icon: DatabaseIcon, to: '/sources', key: 'sources' },
  { icon: RobotIcon, to: '/agents', key: 'agents' },
  { icon: SparkIcon, to: '/intelligence', key: 'intelligence' },
  { icon: SendIcon, to: '/actions', key: 'actions' },
  { icon: ChecklistIcon, to: '/tasks', key: 'tasks' },
  { icon: PlugIcon, to: '/integrations', key: 'integrations' },
  { icon: KanbanIcon, to: '/crm', key: 'crm' },
  { icon: GearIcon, to: '/settings', key: 'settings' },
]

function isActive(pathname: string, to: NavTo) {
  return pathname === to || pathname.startsWith(`${to}/`)
}

export function AppShell({ children }: { children: ReactNode }) {
  const { session, user } = useAuth()
  const location = useLocation()
  const [railOpen, setRailOpen] = useState(false)
  // Telegram health reflects the LIVE sidecar session, not the stored
  // session.integrations flag — that flag stays "connected" after a session
  // lapses or is revoked, which was the source of the false "Telegram tayyor".
  const { data: telegramStatus } = useTelegramConnectionStatus()
  // Default to "connecting" while the status is still loading so a connected
  // workspace doesn't flash "Qayta ulash kerak" on every page load.
  const telegramState = telegramStatus?.state ?? 'connecting'
  const telegramHealthy = telegramState === 'connected'
  const telegramPending = telegramState === 'connecting' || telegramState === 'reconnecting'
  const telegramOk = telegramHealthy || telegramPending
  const workspaceName = session?.workspace.name || user?.name || 'OQIM workspace'
  // Pages without their own context aside show the BI rail inline as the right
  // column; every other page reaches it via the persistent launcher → sheet, so
  // a page's own aside is never crowded out. See concepts right-rail-spec.md.
  const railInline =
    location.pathname.startsWith('/intelligence') ||
    location.pathname.startsWith('/integrations') ||
    location.pathname.startsWith('/settings')

  return (
    <div className={cn(
      'grid h-svh min-h-0 grid-cols-1 overflow-hidden bg-background text-foreground md:grid-cols-[240px_1fr]',
      railInline && 'lg:grid-cols-[240px_1fr_320px]',
    )}>
      <aside className="hidden h-svh flex-col border-r border-border/60 bg-foreground/[0.02] md:flex">
        <button
          type="button"
          className="flex w-full items-center justify-between gap-2 border-b border-border/60 px-3 py-2.5 text-left transition-colors hover:bg-foreground/[0.03]"
        >
          <div className="flex min-w-0 items-center gap-2.5">
            <div className="flex size-7 items-center justify-center rounded-md bg-foreground text-[10px] font-semibold text-background">
              OQ
            </div>
            <div className="min-w-0">
              <div className="truncate text-sm font-medium">{workspaceName}</div>
              <div className="truncate font-mono text-[9px] uppercase tracking-[0.22em] text-muted-foreground">
                {telegramOk ? uz.workspaceUi.connected : 'Qayta ulash'}
              </div>
            </div>
          </div>
          <CaretDownIcon className="size-3.5 opacity-60" />
        </button>

        <div className="px-3 pt-2.5">
          <div className="flex items-center gap-2 rounded-md border border-border/60 bg-background/60 px-2.5 py-1.5">
            <SearchIcon className="size-3.5 opacity-50" />
            <span className="flex-1 truncate text-xs text-muted-foreground">
              {uz.workspaceUi.search}
            </span>
            <kbd className="rounded border border-border/60 bg-background/80 px-1 font-mono text-[9px] text-muted-foreground">
              /
            </kbd>
          </div>
        </div>

        <nav className="mt-2 flex-1 overflow-y-auto px-2 pb-4">
          <ul className="flex flex-col gap-0.5">
            {NAV.map((item) => {
              const active = isActive(location.pathname, item.to)
              const mod = uz.workspaceUi.modules[item.key]
              return (
                <li key={item.key}>
                  <Link
                    to={item.to}
                    className={cn(
                      'flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-sm transition-colors',
                      active
                        ? 'bg-foreground/[0.06] text-foreground'
                        : 'text-muted-foreground hover:bg-foreground/[0.03] hover:text-foreground',
                    )}
                  >
                    <item.icon className="size-4 shrink-0 opacity-70" />
                    <span className="truncate">{mod.label}</span>
                  </Link>
                </li>
              )
            })}
          </ul>
        </nav>

        <div className="flex items-center gap-2 border-t border-border/60 px-3 py-2.5">
          <div className="flex size-7 items-center justify-center rounded-full bg-foreground text-[11px] font-medium text-background">
            {(user?.name || 'O').slice(0, 1).toUpperCase()}
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium">{user?.name || 'Sotuvchi'}</div>
            <div className="flex items-center gap-1.5 truncate text-xs text-muted-foreground">
              <span className={cn('size-1.5 rounded-full', telegramOk ? 'bg-success' : 'bg-warning')} />
              {telegramOk ? 'Onlayn' : 'Qayta ulash'}
            </div>
          </div>
          <Link
            to="/settings"
            className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-foreground/[0.04] hover:text-foreground"
            aria-label="Settings"
          >
            <GearIcon className="size-4" />
          </Link>
        </div>
      </aside>

      <div className="grid min-h-0 grid-rows-[1fr_auto] overflow-hidden">
        <main className="min-h-0 min-w-0 overflow-hidden pb-16 md:pb-0">
          <div className="h-full min-h-0 overflow-hidden bg-background">{children}</div>
        </main>

        <div className="hidden md:block">
          <div className="flex h-9 items-center justify-between border-t border-border/60 bg-background px-4">
            <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
              <span className="font-mono uppercase tracking-[0.22em]">{uz.workspaceUi.runtime}</span>
              {telegramOk ? (
                <Badge variant={telegramHealthy ? 'success' : 'warning'} className="h-5 rounded-full">
                  {telegramHealthy ? 'Telegram tayyor' : 'Ulanmoqda...'}
                </Badge>
              ) : (
                <a
                  href="/onboarding?reconnect=telegram"
                  className="rounded-full outline-none transition-opacity hover:opacity-80"
                >
                  <Badge variant="warning" className="h-5 rounded-full">Qayta ulash kerak</Badge>
                </a>
              )}
            </div>
            <StatusBar />
          </div>
        </div>

        <div className="fixed inset-x-0 bottom-0 z-50 md:hidden">
          <BottomNav />
        </div>
      </div>

      <div
        className={cn(
          'fixed bottom-20 right-4 z-40 md:bottom-4',
          railInline && 'lg:hidden',
        )}
      >
        <Button
          type="button"
          variant="default"
          size="sm"
          className="h-10 rounded-full px-3 shadow-lg"
          aria-label="BI panelni ochish"
          onClick={() => setRailOpen(true)}
        >
          <SparkIcon className="size-4" />
          BI
        </Button>
      </div>
      <Sheet open={railOpen} onOpenChange={setRailOpen}>
        <SheetContent className="w-[min(100vw,24rem)] p-0 sm:max-w-sm" showCloseButton={false}>
          <SheetHeader className="sr-only">
            <SheetTitle>{uz.workspaceUi.rightRail.title}</SheetTitle>
            <SheetDescription>
              Agent jarayoni, ruxsat kutayotgan amallar va BI agent buyrug‘i.
            </SheetDescription>
          </SheetHeader>
          <RightRail variant="sheet" onCommandSubmitted={() => setRailOpen(false)} />
        </SheetContent>
      </Sheet>
      {railInline ? <RightRail /> : null}
    </div>
  )
}
