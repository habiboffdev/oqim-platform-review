import { motion } from 'framer-motion'
import { Robot, ArrowRight, X } from '@phosphor-icons/react'
import { slideUp } from '@/lib/motion'
import { uz } from '@/lib/uz'

interface GhostBannerProps {
  count: number
  onViewAll: () => void
  onDismiss: () => void
}

/**
 * "AI sent N replies while you were away" banner.
 * Shows at the top of the conversations page when autopilot sent messages
 * while the seller was absent.
 */
export function GhostBanner({ count, onViewAll, onDismiss }: GhostBannerProps) {
  if (count < 3) return null

  return (
    <motion.div
      {...slideUp}
      className="mx-4 mb-3 flex items-center justify-between rounded-xl border border-emerald-500/20 bg-emerald-500/5 px-4 py-3"
    >
      <div className="flex items-center gap-2.5">
        <Robot size={18} weight="thin" className="text-emerald-600" />
        <span className="text-sm text-foreground">
          <span className="font-medium">AI {count} ta</span> {uz.sellerAgentReplies.ghostSummary}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <button
          onClick={onViewAll}
          className="flex items-center gap-1 text-xs font-medium text-emerald-600 transition-colors hover:text-emerald-700"
        >
          {uz.sellerAgentReplies.ghostViewAll}
          <ArrowRight size={12} weight="thin" />
        </button>
        <button
          onClick={onDismiss}
          className="text-muted-foreground transition-colors hover:text-foreground"
          aria-label={uz.compose.dismiss}
        >
          <X size={14} weight="thin" />
        </button>
      </div>
    </motion.div>
  )
}
