import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import {
  Microphone,
  Users,
  Storefront,
  BookOpen,
  CheckCircle,
  CircleNotch,
  WarningCircle,
  X,
} from '@phosphor-icons/react'
import { staggerContainer, staggerItem } from '@/lib/motion'
import { uz } from '@/lib/uz'
import { cn } from '@/lib/utils'
import { useAuth } from '@/lib/auth-context'
import { wsManager } from '@/lib/websocket'
import type { IngestionProgress } from '@/lib/types'

interface ChecklistItem {
  key: string
  icon: typeof Microphone
  label: string
  done: boolean
  active: boolean
  warning?: boolean
}

export function LearningChecklist({ collapsed }: { collapsed: boolean }) {
  const { user } = useAuth()
  const [progress, setProgress] = useState<IngestionProgress | null>(null)
  const [dismissed, setDismissed] = useState(false)

  useEffect(() => {
    const unsub = wsManager.on('ingestion_progress', (data) => {
      setProgress(data as unknown as IngestionProgress)
    })
    return unsub
  }, [])

  // Don't show if onboarding is complete and no active ingestion
  if (!user || user.onboarding_completed && !progress) return null
  // Don't show if dismissed
  if (dismissed) return null
  // Don't show if all done
  if (progress?.completed) {
    // Auto-dismiss after 5 seconds
    return <CompletedBanner collapsed={collapsed} onDismiss={() => setDismissed(true)} />
  }
  // Don't show if no progress data yet (ingestion hasn't started)
  if (!progress) return null

  const items = buildChecklist(progress)
  const doneCount = items.filter((i) => i.done).length

  if (collapsed) {
    // Minimal: just show progress ring
    return (
      <div className="mx-auto mb-2 flex flex-col items-center gap-1">
        <div className="relative flex h-8 w-8 items-center justify-center">
          <svg className="h-8 w-8 -rotate-90" viewBox="0 0 32 32">
            <circle cx="16" cy="16" r="12" fill="none" stroke="currentColor" strokeWidth="2" className="text-border" />
            <circle
              cx="16" cy="16" r="12" fill="none" stroke="currentColor" strokeWidth="2"
              className="text-primary transition-all duration-500"
              strokeDasharray={`${(doneCount / items.length) * 75.4} 75.4`}
              strokeLinecap="round"
            />
          </svg>
          <span className="absolute text-[9px] font-mono font-bold text-muted-foreground">
            {doneCount}/{items.length}
          </span>
        </div>
      </div>
    )
  }

  return (
    <div className="mx-2 mb-2">
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        className="rounded-xl border border-border bg-card p-3"
      >
        {/* Header */}
        <div className="mb-2.5 flex items-center justify-between">
          <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
            {uz.onboarding.scan.backgroundLearning}
          </span>
          <button
            onClick={() => setDismissed(true)}
            className="rounded p-0.5 text-muted-foreground/50 transition-colors hover:text-muted-foreground"
          >
            <X size={12} weight="thin" />
          </button>
        </div>

        {/* Items */}
        <motion.div {...staggerContainer} initial="initial" animate="animate" className="flex flex-col gap-1.5">
          {items.map((item) => (
            <motion.div
              key={item.key}
              {...staggerItem}
              className="flex items-center gap-2.5"
            >
              {item.warning ? (
                <WarningCircle size={16} weight="thin" className="shrink-0 text-amber-500" />
              ) : item.done ? (
                <CheckCircle size={16} weight="thin" className="shrink-0 text-green-500" />
              ) : item.active ? (
                <CircleNotch size={16} weight="thin" className="shrink-0 animate-spin text-primary" />
              ) : (
                <div className="h-4 w-4 shrink-0 rounded-full border border-border" />
              )}
              <span
                className={cn(
                  'text-[13px]',
                  item.warning
                    ? 'font-medium text-amber-600'
                    : item.done
                      ? 'text-muted-foreground line-through'
                      : item.active
                        ? 'text-foreground font-medium'
                        : 'text-muted-foreground',
                )}
              >
                {item.label}
              </span>
            </motion.div>
          ))}
        </motion.div>

        {/* Progress bar */}
        <div className="mt-3 h-1 overflow-hidden rounded-full bg-border">
          <motion.div
            className="h-full rounded-full bg-primary"
            initial={{ width: 0 }}
            animate={{ width: `${(doneCount / items.length) * 100}%` }}
            transition={{ type: 'spring', stiffness: 300, damping: 30 }}
          />
        </div>
      </motion.div>
    </div>
  )
}

function CompletedBanner({ collapsed, onDismiss }: { collapsed: boolean; onDismiss: () => void }) {
  useEffect(() => {
    const timer = setTimeout(onDismiss, 5000)
    return () => clearTimeout(timer)
  }, [onDismiss])

  if (collapsed) return null

  return (
    <div className="mx-2 mb-2">
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.95 }}
        className="flex items-center gap-2 rounded-xl border border-green-500/10 bg-green-500/[0.03] p-3"
      >
        <CheckCircle size={16} weight="thin" className="shrink-0 text-green-500" />
        <span className="text-[13px] text-green-600">{uz.onboarding.completed}</span>
      </motion.div>
    </div>
  )
}

function buildChecklist(progress: IngestionProgress): ChecklistItem[] {
  const phase = progress.phase

  return [
    {
      key: 'voice',
      icon: Microphone,
      label: uz.onboarding.voiceProfile,
      done: progress.voice_profile_ready,
      active: phase === 'generating_voice_profile',
      warning: progress.voice_profile_degraded,
    },
    {
      key: 'contacts',
      icon: Users,
      label: uz.onboarding.sectionContacts,
      done: progress.contacts_found > 0 && !['reading_dialogs', 'classifying_contacts'].includes(phase),
      active: ['reading_dialogs', 'classifying_contacts'].includes(phase),
    },
    {
      key: 'products',
      icon: Storefront,
      label: uz.onboarding.products,
      done: progress.products_extracted > 0,
      active: phase === 'scanning_channels',
    },
    {
      key: 'knowledge',
      icon: BookOpen,
      label: uz.onboarding.knowledge,
      done: progress.knowledge_items > 0,
      active: phase === 'extracting_knowledge',
    },
  ]
}
