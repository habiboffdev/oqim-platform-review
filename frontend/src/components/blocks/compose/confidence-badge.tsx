import { motion } from 'framer-motion'
import { ShieldCheck, ShieldWarning, Warning } from '@phosphor-icons/react'
import { GREEN_CONFIDENCE_THRESHOLD, YELLOW_CONFIDENCE_THRESHOLD } from '@/lib/constants'
import { cn } from '@/lib/utils'
import { uz } from '@/lib/uz'

type ConfidenceLevel = 'green' | 'yellow' | 'red'

interface ConfidenceBadgeProps {
  score: number
  className?: string
}

function getLevel(score: number): ConfidenceLevel {
  if (score >= GREEN_CONFIDENCE_THRESHOLD) return 'green'
  if (score >= YELLOW_CONFIDENCE_THRESHOLD) return 'yellow'
  return 'red'
}

const levels: Record<ConfidenceLevel, {
  icon: typeof ShieldCheck
  label: string
  bg: string
  text: string
  ring: string
}> = {
  green: {
    icon: ShieldCheck,
    label: uz.compose.confidence.green,
    bg: 'bg-confidence-green/10',
    text: 'text-confidence-green',
    ring: 'ring-confidence-green/30',
  },
  yellow: {
    icon: ShieldWarning,
    label: uz.compose.confidence.yellow,
    bg: 'bg-confidence-yellow/10',
    text: 'text-confidence-yellow',
    ring: 'ring-confidence-yellow/30',
  },
  red: {
    icon: Warning,
    label: uz.compose.confidence.red,
    bg: 'bg-confidence-red/10',
    text: 'text-confidence-red',
    ring: 'ring-confidence-red/30',
  },
}

export function ConfidenceBadge({ score, className }: ConfidenceBadgeProps) {
  const level = getLevel(score)
  const { icon: Icon, label, bg, text, ring } = levels[level]

  return (
    <motion.span
      initial={{ opacity: 0, scale: 0.9 }}
      animate={{ opacity: 1, scale: 1 }}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium',
        'ring-1 ring-inset',
        bg, text, ring,
        className,
      )}
    >
      <Icon size={13} weight="thin" />
      {label}
    </motion.span>
  )
}
