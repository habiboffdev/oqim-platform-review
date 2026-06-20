import { useRef } from 'react'
import { motion } from 'framer-motion'
import {
  ArrowsClockwise,
  Brain,
  Bell,
  Warning,
  ChatCircle,
} from '@phosphor-icons/react'
import { spring } from '@/lib/motion'
import { cn } from '@/lib/utils'
import type { ActivityEvent } from '@/hooks/use-activity-stream'

const CATEGORY_ICONS: Record<string, typeof Brain> = {
  sync: ArrowsClockwise,
  seller_agent_reply: Brain,
  sales_followup: Bell,
  message: ChatCircle,
}

const CATEGORY_COLORS: Record<string, string> = {
  sync: 'text-muted-foreground',
  seller_agent_reply: 'text-foreground',
  sales_followup: 'text-amber-600',
  message: 'text-muted-foreground',
}

function getCategoryFromType(type: string): string {
  return type.split(':')[0] || 'sync'
}

interface ActivityLogProps {
  events: ActivityEvent[]
  onClose?: () => void
}

export function ActivityLog({ events }: ActivityLogProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const reversed = [...events].reverse()

  return (
    <motion.div
      initial={{ height: 0, opacity: 0 }}
      animate={{ height: 'auto', opacity: 1 }}
      exit={{ height: 0, opacity: 0 }}
      transition={spring}
      className="max-h-56 overflow-hidden border-t border-border/40 bg-background"
    >
      <div ref={scrollRef} className="overflow-y-auto" style={{ maxHeight: 220 }}>
        {reversed.length === 0 ? (
          <div className="px-3 py-4 text-center text-[11px] text-muted-foreground/50">
            Hali hodisalar yo'q
          </div>
        ) : (
          <div className="divide-y divide-border/30">
            {reversed.map((event, i) => {
              const category = getCategoryFromType(event.type)
              const Icon = event.type.includes('error') || event.type.includes('failed')
                ? Warning
                : CATEGORY_ICONS[category] || ArrowsClockwise
              const isError = event.type.includes('error') || event.type.includes('failed')
              const colorClass = isError ? 'text-destructive' : (CATEGORY_COLORS[category] || 'text-muted-foreground')

              return (
                <div
                  key={`${event.ts}-${event.type}-${i}`}
                  className="flex items-center gap-2.5 px-3 py-1.5"
                >
                  <span className="shrink-0 font-mono text-[9px] tabular-nums text-muted-foreground/40">
                    {formatTime(event.ts)}
                  </span>
                  <Icon size={11} weight="bold" className={cn('shrink-0', colorClass)} />
                  <span className={cn('min-w-0 flex-1 truncate text-[11px]', colorClass)}>
                    {event.message}
                  </span>
                  <span className="shrink-0 text-[8px] uppercase tracking-widest text-muted-foreground/30">
                    {category}
                  </span>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </motion.div>
  )
}

function formatTime(ts: number): string {
  const d = new Date(ts * 1000)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
}
