import { useState, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  ArrowsClockwise,
  Brain,
  Bell,
  Warning,
  ChatCircle,
  CaretUp,
  CaretDown,
} from '@phosphor-icons/react'
import { useActivityStream, type ActivityEvent } from '@/hooks/use-activity-stream'
import { spring } from '@/lib/motion'
import { cn } from '@/lib/utils'
import { ActivityLog } from './activity-log'

const CATEGORY_ICONS: Record<string, typeof Brain> = {
  sync: ArrowsClockwise,
  seller_agent_reply: Brain,
  sales_followup: Bell,
  message: ChatCircle,
}

function getCategoryFromType(type: string): string {
  return type.split(':')[0] || 'sync'
}

function getIcon(event: ActivityEvent) {
  if (event.type.includes('error') || event.type.includes('failed')) return Warning
  return CATEGORY_ICONS[getCategoryFromType(event.type)] || ArrowsClockwise
}

export function StatusBar() {
  const { latestEvent, events, eventCount } = useActivityStream()
  const [logOpen, setLogOpen] = useState(false)
  const [seenCount, setSeenCount] = useState(0)
  const barRef = useRef<HTMLDivElement>(null)

  const unseenCount = eventCount - seenCount

  const handleToggleLog = () => {
    if (!logOpen) setSeenCount(eventCount)
    setLogOpen(!logOpen)
  }

  if (!latestEvent && eventCount === 0) return null

  const Icon = latestEvent ? getIcon(latestEvent) : ArrowsClockwise
  const isError = latestEvent?.type.includes('error') || latestEvent?.type.includes('failed')

  return (
    <>
      {/* Activity log panel */}
      <AnimatePresence>
        {logOpen && (
          <ActivityLog events={events} onClose={() => setLogOpen(false)} />
        )}
      </AnimatePresence>

      {/* Status bar */}
      <div
        ref={barRef}
        className="flex h-7 items-center border-t border-border/40 bg-muted/30 px-3"
      >
        {/* Latest event */}
        <AnimatePresence mode="wait">
          {latestEvent && (
            <motion.div
              key={latestEvent.ts + latestEvent.type}
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={spring}
              className="flex min-w-0 flex-1 items-center gap-2"
            >
              <Icon
                size={12}
                weight="bold"
                className={cn(
                  'shrink-0',
                  isError ? 'text-destructive' : 'text-muted-foreground',
                )}
              />
              <span className={cn(
                'truncate text-[11px]',
                isError ? 'text-destructive' : 'text-muted-foreground',
              )}>
                {latestEvent.message}
              </span>
              <span className="shrink-0 font-mono text-[9px] tabular-nums text-muted-foreground/50">
                {formatTime(latestEvent.ts)}
              </span>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Expand/collapse button */}
        <button
          onClick={handleToggleLog}
          className="ml-2 flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          {unseenCount > 0 && (
            <span className="flex size-4 items-center justify-center rounded-full bg-foreground text-[8px] font-bold tabular-nums text-background">
              {unseenCount > 99 ? '99' : unseenCount}
            </span>
          )}
          {logOpen ? (
            <CaretDown size={10} weight="bold" />
          ) : (
            <CaretUp size={10} weight="bold" />
          )}
        </button>
      </div>
    </>
  )
}

function formatTime(ts: number): string {
  const d = new Date(ts * 1000)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
}
