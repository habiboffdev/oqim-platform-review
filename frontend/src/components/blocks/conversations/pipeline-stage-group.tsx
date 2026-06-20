import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { CaretDown } from '@phosphor-icons/react'
import { cn } from '@/lib/utils'
import { uz } from '@/lib/uz'
import { PipelineListItem } from './pipeline-list-item'
import type { PipelineStage } from '@/hooks/use-pipeline'
import type { Conversation } from '@/lib/types'

const STAGE_COLORS: Record<string, string> = {
  new: 'bg-blue-400',
  qualified: 'bg-sky-400',
  talking: 'bg-sky-400',
  negotiating: 'bg-amber-400',
  waiting: 'bg-violet-400',
  won: 'bg-emerald-400',
  lost: 'bg-zinc-400',
  cold: 'bg-zinc-400',
}

const COLLAPSED_BY_DEFAULT: PipelineStage[] = ['won', 'lost']

interface PipelineStageGroupProps {
  stage: PipelineStage
  conversations: Conversation[]
}

export function PipelineStageGroup({ stage, conversations }: PipelineStageGroupProps) {
  const [isOpen, setIsOpen] = useState(!COLLAPSED_BY_DEFAULT.includes(stage))

  if (conversations.length === 0) return null

  const stageLabel = uz.pipeline.stages[stage] || stage

  return (
    <div>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex w-full items-center gap-2 px-4 py-2 bg-muted/20 transition-colors hover:bg-muted/40"
      >
        <motion.div
          animate={{ rotate: isOpen ? 0 : -90 }}
          transition={{ duration: 0.15 }}
        >
          <CaretDown size={10} weight="thin" className="text-muted-foreground" />
        </motion.div>
        <div className={cn('size-1.5 rounded-full', STAGE_COLORS[stage] || 'bg-zinc-400')} />
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          {stageLabel}
        </span>
        <span className="text-[10px] text-muted-foreground/60">{conversations.length}</span>
      </button>

      <AnimatePresence initial={false}>
        {isOpen && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            {conversations.map((conv) => (
              <PipelineListItem key={conv.id} conversation={conv} />
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
