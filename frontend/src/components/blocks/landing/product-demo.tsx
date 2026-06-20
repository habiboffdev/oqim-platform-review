import { motion } from 'framer-motion'
import { Check, PencilSimple, Robot } from '@phosphor-icons/react'
import { uz } from '@/lib/uz'

const springFast = { type: 'spring' as const, stiffness: 400, damping: 30 }
const springMedium = { type: 'spring' as const, stiffness: 350, damping: 28 }
const springPop = { type: 'spring' as const, stiffness: 500, damping: 28 }

export function ProductDemo() {
  return (
    <div className="space-y-3">
      {/* Customer message — left-aligned (incoming from seller's perspective) */}
      <motion.div
        className="max-w-[85%]"
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.5, ...springFast }}
      >
        <div className="rounded-2xl rounded-tl-md bg-foreground/[0.04] px-4 py-3">
          <p className="text-[13px] leading-relaxed text-foreground">
            {uz.landing.demoCustomerMsg}
          </p>
        </div>
        <p className="mt-1 text-[11px] text-muted-foreground">
          {uz.landing.demoCustomerName} · 14:32
        </p>
      </motion.div>

      {/* AI reply — distinct from regular messages */}
      <motion.div
        initial={{ opacity: 0, y: 16, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ delay: 1.4, ...springMedium }}
      >
        <div className="rounded-2xl rounded-tl-md border border-primary/15 bg-primary/[0.025] px-4 py-3">
          {/* Reply header */}
          <div className="mb-2 flex items-center gap-1.5">
            <Robot size={14} weight="thin" className="text-primary" />
            <span className="text-[11px] font-medium text-primary">
              {uz.landing.demoReplyLabel}
            </span>
            <motion.span
              className="ml-auto inline-flex items-center rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-semibold tabular-nums text-primary"
              initial={{ opacity: 0, scale: 0.8 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ delay: 2.0, ...springPop }}
            >
              92%
            </motion.span>
          </div>

          {/* Reply body */}
          <p className="text-[13px] leading-relaxed text-foreground">
            {uz.landing.demoReplyText}
          </p>

          {/* Action buttons */}
          <motion.div
            className="mt-3 flex items-center gap-2"
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 2.2, ...springPop }}
          >
            <span className="inline-flex items-center gap-1.5 rounded-full bg-primary px-3 py-1 text-[11px] font-medium text-primary-foreground">
              <Check size={12} weight="bold" />
              {uz.landing.demoApprove}
            </span>
            <span className="inline-flex items-center gap-1.5 rounded-full border border-border px-3 py-1 text-[11px] font-medium text-muted-foreground">
              <PencilSimple size={12} weight="thin" />
              {uz.landing.demoEdit}
            </span>
          </motion.div>
        </div>
      </motion.div>
    </div>
  )
}
