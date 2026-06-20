import { motion } from 'framer-motion'
import { shimmerPulse } from '@/lib/motion'

/**
 * Shimmer skeleton that replaces conversation preview text
 * when the Seller Agent is preparing a reply ("ai_thinking" state).
 */
export function ShimmerRow() {
  return (
    <motion.div {...shimmerPulse} className="flex flex-col gap-1.5 py-0.5">
      <div className="h-2 w-[70%] rounded-full bg-foreground/[0.06]" />
      <div className="h-2 w-[45%] rounded-full bg-foreground/[0.06]" />
    </motion.div>
  )
}
