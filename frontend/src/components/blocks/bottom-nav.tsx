import { Link, useLocation } from '@tanstack/react-router'
import {
  BrainIcon,
  ChatIcon,
  ChecklistIcon,
  DatabaseIcon,
  GearIcon,
  PlugIcon,
  RobotIcon,
  SendIcon,
  SparkIcon,
  type IconComponent,
} from '@/components/icons/nav-icons'
import { cn } from '@/lib/utils'
import { uz } from '@/lib/uz'

type NavTo = '/conversations' | '/brain' | '/sources' | '/agents' | '/intelligence' | '/actions' | '/tasks' | '/integrations' | '/settings'

const navItems: { to: NavTo; icon: IconComponent; key: 'conversations' | 'brain' | 'sources' | 'agents' | 'intelligence' | 'actions' | 'tasks' | 'integrations' | 'settings' }[] = [
  { to: '/conversations', icon: ChatIcon, key: 'conversations' },
  { to: '/brain', icon: BrainIcon, key: 'brain' },
  { to: '/sources', icon: DatabaseIcon, key: 'sources' },
  { to: '/agents', icon: RobotIcon, key: 'agents' },
  { to: '/intelligence', icon: SparkIcon, key: 'intelligence' },
  { to: '/actions', icon: SendIcon, key: 'actions' },
  { to: '/tasks', icon: ChecklistIcon, key: 'tasks' },
  { to: '/integrations', icon: PlugIcon, key: 'integrations' },
  { to: '/settings', icon: GearIcon, key: 'settings' },
]

function isActive(pathname: string, to: NavTo) {
  return pathname === to || pathname.startsWith(`${to}/`)
}

export function BottomNav() {
  const location = useLocation()

  return (
    <nav className="mx-3 mb-3 grid grid-cols-9 overflow-x-auto rounded-xl border border-border bg-background/95 p-1 shadow-xl shadow-black/10 backdrop-blur-xl">
      {navItems.map((item) => {
        const active = isActive(location.pathname, item.to)
        const mod = uz.workspaceUi.modules[item.key]
        return (
          <Link
            key={item.key}
            to={item.to}
            className={cn(
              'flex min-w-0 flex-col items-center gap-0.5 rounded-lg px-1 py-2 text-[10px] transition-colors',
              active
                ? 'bg-foreground text-background'
                : 'text-muted-foreground hover:bg-foreground/[0.04] hover:text-foreground',
            )}
          >
            <item.icon className="size-4.5" />
            <span className="max-w-full truncate">{mod.label}</span>
          </Link>
        )
      })}
    </nav>
  )
}
