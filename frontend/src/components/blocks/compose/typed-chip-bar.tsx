import { useState, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  PaperPlaneTilt,
  ArrowRight,
  PencilSimple,
  MagnifyingGlass,
  Lightning,
  ArrowBendUpRight,
  ArrowClockwise,
  SlidersHorizontal,
} from '@phosphor-icons/react'
import { Button } from '@/components/ui/button'
import { chipStagger, chipItem } from '@/lib/motion'
import { uz } from '@/lib/uz'
import {
  useApproveSellerAgentReply,
  useApproveAndStage,
  useRegenerateSellerAgentReply,
  useSendQuickReply,
  useDismissSellerAgentReply,
} from '@/hooks/use-seller-agent-replies'
import type { TypedChip } from '@/lib/types'

const CHIP_ICONS: Record<string, React.ElementType> = {
  approve: PaperPlaneTilt,
  approve_and_stage: ArrowRight,
  edit_instruction: PencilSimple,
  tool_action: MagnifyingGlass,
  quick_reply: Lightning,
  escalate: ArrowBendUpRight,
  tone_shift: SlidersHorizontal,
}

const CHIP_STYLES: Record<string, string> = {
  approve: 'bg-primary text-primary-foreground shadow-sm hover:bg-primary/90',
  approve_and_stage: 'bg-primary text-primary-foreground shadow-sm hover:bg-primary/90',
  edit_instruction: 'border border-border bg-background text-foreground hover:border-foreground/30 hover:shadow-md',
  tool_action: 'border border-border bg-background text-foreground hover:border-foreground/30 hover:shadow-md',
  quick_reply: 'border border-dashed border-border bg-background text-muted-foreground hover:border-foreground/40 hover:text-foreground',
  escalate: 'border border-destructive/30 text-destructive hover:bg-destructive/5',
  tone_shift: 'border border-border bg-background text-muted-foreground hover:border-foreground/30 hover:text-foreground hover:shadow-md',
}

interface TypedChipBarProps {
  chips: TypedChip[]
  replyId: number
  conversationId: number
  isRegenerating: boolean
  isStale?: boolean
  onRefresh?: () => void
  onVersionCreated?: (instruction: string) => void
  onRegenerateStateChange?: (isRegenerating: boolean) => void
}

export function TypedChipBar({
  chips,
  replyId,
  conversationId,
  isRegenerating,
  isStale = false,
  onRefresh,
  onVersionCreated,
  onRegenerateStateChange,
}: TypedChipBarProps) {
  const [customInput, setCustomInput] = useState('')
  const [showCustom, setShowCustom] = useState(false)

  const approve = useApproveSellerAgentReply()
  const approveAndStage = useApproveAndStage()
  const regenerate = useRegenerateSellerAgentReply()
  const quickReply = useSendQuickReply()
  const dismiss = useDismissSellerAgentReply()

  // Sync regenerate pending state to parent for shimmer overlay
  const prevPending = useRef(false)
  if (prevPending.current !== regenerate.isPending) {
    prevPending.current = regenerate.isPending
    onRegenerateStateChange?.(regenerate.isPending)
  }

  const isActing = approve.isPending || approveAndStage.isPending || quickReply.isPending || dismiss.isPending

  function requestRegenerate(
    instruction: string,
    options?: { onSuccess?: () => void },
  ) {
    regenerate.mutate(
      { replyId, instruction },
      {
        onSuccess: () => {
          onVersionCreated?.(instruction)
          options?.onSuccess?.()
        },
      },
    )
  }

  function handleChip(chip: TypedChip) {
    switch (chip.type) {
      case 'approve':
        approve.mutate(replyId)
        break
      case 'approve_and_stage':
        approveAndStage.mutate({
          replyId,
          conversationId,
          stage: chip.payload.stage as string,
        })
        break
      case 'edit_instruction':
        requestRegenerate(chip.payload.instruction as string)
        break
      case 'tool_action':
        regenerate.mutate({ replyId, instruction: `Run ${chip.payload.tool}: ${JSON.stringify(chip.payload.args)}` })
        break
      case 'quick_reply':
        quickReply.mutate({ conversationId, text: chip.payload.text as string })
        break
      case 'escalate':
        dismiss.mutate({ replyId })
        break
      case 'tone_shift': {
        const instruction = `Adjust tone: ${chip.payload.axis} ${chip.payload.direction === 'up' ? 'increase' : 'decrease'}`
        requestRegenerate(instruction)
        break
      }
    }
  }

  function handleCustomSubmit() {
    const trimmed = customInput.trim()
    if (trimmed) {
      requestRegenerate(trimmed, {
        onSuccess: () => {
          setCustomInput('')
          setShowCustom(false)
        },
      })
    }
  }

  return (
    <div className="space-y-2.5">
      <motion.div
        {...chipStagger}
        className="flex flex-wrap gap-1.5 overflow-x-auto scrollbar-none"
      >
        {isStale && onRefresh && (
          <motion.button
            {...chipItem}
            onClick={onRefresh}
            className="h-8 rounded-full border border-foreground/30 bg-foreground/5 px-3 text-[11px] font-medium text-foreground transition-all hover:bg-foreground/10 active:scale-95"
          >
            <ArrowClockwise size={12} weight="thin" className="mr-1.5 inline-block animate-pulse" />
            {uz.sellerAgentReplies.refresh}
          </motion.button>
        )}

        {chips.map((chip, i) => {
          const Icon = CHIP_ICONS[chip.type] || PencilSimple
          const style = CHIP_STYLES[chip.type] || CHIP_STYLES.edit_instruction

          return (
            <motion.button
              key={`${chip.type}-${i}`}
              {...chipItem}
              onClick={() => handleChip(chip)}
              disabled={isActing || isRegenerating}
              className={`h-8 rounded-full px-3 text-[11px] font-medium transition-all active:scale-95 disabled:opacity-40 ${style} ${isStale ? 'opacity-50' : ''}`}
            >
              <Icon size={12} weight="thin" className="mr-1.5 inline-block" />
              {chip.label}
            </motion.button>
          )
        })}

        <motion.button
          {...chipItem}
          onClick={() => setShowCustom(!showCustom)}
          disabled={isActing || isRegenerating}
          className="h-8 rounded-full border border-dashed border-border px-3 text-[11px] text-muted-foreground transition-all hover:border-foreground/40 hover:text-foreground disabled:opacity-40"
        >
          <PencilSimple size={11} weight="thin" className="mr-1 inline-block" />
          {uz.compose.customInstruction}
        </motion.button>
      </motion.div>

      <AnimatePresence>
        {showCustom && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden"
          >
            <div className="flex gap-2">
              <input
                value={customInput}
                onChange={(e) => setCustomInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleCustomSubmit()}
                placeholder={uz.compose.customInstruction}
                aria-label={uz.compose.customInstruction}
                className="h-8 flex-1 rounded-lg border border-border bg-background px-3 text-xs outline-none placeholder:text-muted-foreground transition-colors focus:border-foreground/30 focus:ring-1 focus:ring-foreground/10"
                autoFocus
              />
              <Button
                size="xs"
                onClick={handleCustomSubmit}
                disabled={!customInput.trim() || isRegenerating}
                aria-label={uz.compose.send}
                className="gap-1"
              >
                <ArrowRight size={12} weight="thin" />
              </Button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
