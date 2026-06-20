import { useState, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  PaperPlaneTilt,
  X,
  Warning,
  ArrowClockwise,
  Check,
  XCircle,
} from '@phosphor-icons/react'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/primitives/spinner'
import { ConfidenceBadge } from './confidence-badge'
import { ReplyBubble } from './reply-bubble'
import { TypedChipBar } from './typed-chip-bar'
import {
  useApproveSellerAgentReply,
  useEditSellerAgentReply,
  useDismissSellerAgentReply,
} from '@/hooks/use-seller-agent-replies'
import { overlaySlideUp } from '@/lib/motion'
import { uz } from '@/lib/uz'
import type { SellerAgentReply, TypedChip } from '@/lib/types'

const DISMISS_REASONS = ['bad_tone', 'incorrect_fact', 'other'] as const

type DeliveryPhase = 'idle' | 'sending' | 'sent' | 'failed'

interface ComposeOverlayProps {
  reply: SellerAgentReply
  isStale?: boolean
  staleMessageCount?: number
  onRefresh?: () => void
}

export function ComposeOverlay({ reply, isStale = false, staleMessageCount = 0, onRefresh }: ComposeOverlayProps) {
  const approve = useApproveSellerAgentReply()
  const edit = useEditSellerAgentReply()
  const dismiss = useDismissSellerAgentReply()

  const [showDismissReasons, setShowDismissReasons] = useState(false)
  // Track whether the user initiated approve for this reply.
  const approveInitiatedRef = useRef<number | null>(null)
  const [retryCount, setRetryCount] = useState(0)
  const [isRegenerating, setIsRegenerating] = useState(false)

  const bubbles = reply.split_messages?.length
    ? reply.split_messages
    : [reply.draft_content]

  const typedChips: TypedChip[] = Array.isArray(reply.chips)
    ? reply.chips.filter((c): c is TypedChip => typeof c === 'object' && 'type' in c)
    : []

  // Derive delivery phase from reply.status plus local tracking.
  const deliveryPhase: DeliveryPhase = (() => {
    if (approve.isPending || edit.isPending) return 'sending'
    if (reply.status === 'approved' || reply.status === 'sending') return 'sending'
    if (reply.status === 'sent') return 'sent'
    if (reply.status === 'draft' && approveInitiatedRef.current === reply.id) return 'failed'
    return 'idle'
  })()

  const isActing = deliveryPhase === 'sending' || dismiss.isPending
  const maxRetries = 1

  async function handleApprove() {
    approveInitiatedRef.current = reply.id
    // Server-side delivery via ChannelRouter (#113) — approve endpoint sends the message
    approve.mutate(reply.id)
  }

  function handleRetry() {
    if (retryCount >= maxRetries) return
    setRetryCount((c) => c + 1)
    handleApprove()
  }

  function handleDismissClick() {
    setShowDismissReasons(true)
  }

  function handleDismissWithReason(reason?: string) {
    dismiss.mutate({ replyId: reply.id, reason })
    setShowDismissReasons(false)
  }

  function handleBubbleEdit(index: number, newText: string) {
    approveInitiatedRef.current = reply.id
    if (reply.split_messages?.length) {
      const updated = [...reply.split_messages]
      updated[index] = newText
      edit.mutate({ replyId: reply.id, content: updated.join('\n') })
    } else {
      edit.mutate({ replyId: reply.id, content: newText })
    }
  }

  // Sent state: show checkmark, then parent will unmount after TanStack refetch
  if (deliveryPhase === 'sent') {
    return (
      <motion.div
        key={`${reply.id}-sent`}
        initial={{ opacity: 1 }}
        animate={{ opacity: 0 }}
        transition={{ delay: 1.5, duration: 0.5 }}
        className="border-t border-border/40 bg-gradient-to-t from-emerald-50/30 to-background px-4 py-4"
      >
        <div className="flex items-center justify-end gap-2">
          <motion.div
            initial={{ scale: 0 }}
            animate={{ scale: 1 }}
            transition={{ type: 'spring', stiffness: 500, damping: 25 }}
          >
            <Check size={16} weight="thin" className="text-emerald-600" />
          </motion.div>
          <span className="text-sm font-medium text-emerald-700">{uz.compose.sent}</span>
        </div>
      </motion.div>
    )
  }

  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={reply.id}
        {...overlaySlideUp}
        className="border-t border-border/40 bg-gradient-to-t from-muted/20 to-background px-4 py-4"
      >
        {/* Stale reply warning banner */}
        {isStale && deliveryPhase === 'idle' && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            className="mb-3 flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2"
          >
            <Warning size={14} weight="thin" className="shrink-0 text-amber-600" />
            <span className="flex-1 text-xs text-amber-800">
              {staleMessageCount > 0 ? `${staleMessageCount} ` : ''}{uz.compose.staleWarning}
            </span>
            {onRefresh && (
              <button
                onClick={onRefresh}
                disabled={isRegenerating}
                className="flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-amber-700 transition-colors hover:bg-amber-100 disabled:opacity-40"
              >
                <ArrowClockwise size={12} weight="thin" className={isRegenerating ? 'animate-spin' : ''} />
                {uz.compose.staleRegenerate}
              </button>
            )}
          </motion.div>
        )}

        {/* Delivery failed banner */}
        {deliveryPhase === 'failed' && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            className="mb-3 flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2"
          >
            <XCircle size={14} weight="thin" className="shrink-0 text-red-600" />
            <span className="flex-1 text-xs text-red-800">{uz.compose.failed}</span>
            {retryCount < maxRetries ? (
              <button
                onClick={handleRetry}
                disabled={approve.isPending}
                className="flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-red-700 transition-colors hover:bg-red-100 disabled:opacity-40"
              >
                <ArrowClockwise size={12} weight="thin" />
                {uz.compose.retry}
              </button>
            ) : (
              <span className="text-[11px] text-red-500">{uz.compose.retryExhausted}</span>
            )}
          </motion.div>
        )}

        {/* Header row: confidence badge + reply label + dismiss */}
        <div className="mb-3 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <ConfidenceBadge score={reply.confidence_score} />
            <span className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
              {uz.compose.replyReady}
            </span>
          </div>
          <div className="flex items-center gap-1">
            <motion.button
              onClick={handleDismissClick}
              disabled={isActing}
              whileHover={{ scale: 1.1 }}
              whileTap={{ scale: 0.9 }}
              className="rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40"
              aria-label={uz.compose.dismiss}
            >
              <X size={16} weight="thin" />
            </motion.button>
          </div>
        </div>

        {/* Dismiss reason selector (shown on dismiss click) */}
        <AnimatePresence>
          {showDismissReasons && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              className="mb-3 overflow-hidden"
            >
              <div className="rounded-lg border border-border/50 bg-muted/30 p-2.5">
                <span className="mb-2 block text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                  {uz.compose.dismissReason}
                </span>
                <div className="flex flex-wrap gap-1.5">
                  {DISMISS_REASONS.map((reason) => (
                    <button
                      key={reason}
                      onClick={() => handleDismissWithReason(reason)}
                      disabled={dismiss.isPending}
                      className="rounded-full border border-border bg-background px-3 py-1 text-[11px] font-medium text-foreground shadow-sm transition-all hover:border-foreground/30 hover:shadow-md active:scale-95 disabled:opacity-40"
                    >
                      {uz.compose.dismissReasons[reason]}
                    </button>
                  ))}
                  <button
                    onClick={() => handleDismissWithReason()}
                    disabled={dismiss.isPending}
                    className="rounded-full border border-dashed border-border px-3 py-1 text-[11px] text-muted-foreground transition-all hover:border-foreground/40 hover:text-foreground active:scale-95 disabled:opacity-40"
                  >
                    {uz.compose.dismiss}
                  </button>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Reply bubbles with shimmer when regenerating */}
        <div className="relative mb-3 space-y-0.5">
          {isRegenerating && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="pointer-events-none absolute inset-0 z-10 overflow-hidden rounded-xl"
            >
              <div className="absolute inset-0 animate-pulse bg-gradient-to-r from-transparent via-foreground/[0.04] to-transparent" />
              <div className="absolute inset-0 bg-background/40 backdrop-blur-[1px]" />
              <div className="absolute inset-0 flex items-center justify-center gap-2">
                <Spinner size="sm" />
                <span className="text-xs font-medium text-muted-foreground">{uz.compose.regenerating}</span>
              </div>
            </motion.div>
          )}
          {bubbles.map((text, i) => (
            <ReplyBubble
              key={`${reply.id}-${i}`}
              text={text}
              index={i}
              onEdit={(newText) => handleBubbleEdit(i, newText)}
            />
          ))}
        </div>

        {/* Typed action chips -- only render if there are chips */}
        {typedChips.length > 0 && (
          <div className="mb-4">
            <TypedChipBar
              chips={typedChips}
              replyId={reply.id}
              conversationId={reply.conversation_id}
              isRegenerating={isRegenerating}
              isStale={isStale}
              onRefresh={onRefresh}
              onRegenerateStateChange={setIsRegenerating}
            />
          </div>
        )}

        {/* Footer: send button with delivery status */}
        <div className="flex justify-end">
          {deliveryPhase === 'sending' ? (
            <div className="flex items-center gap-2 px-5 py-2">
              <Spinner size="sm" />
              <span className="text-sm text-muted-foreground">{uz.compose.sending}</span>
            </div>
          ) : (
            <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.97 }}>
              <Button
                onClick={handleApprove}
                disabled={isActing || isRegenerating}
                className="gap-2 px-5 shadow-sm"
              >
                <PaperPlaneTilt size={15} weight="thin" />
                {uz.compose.send}
              </Button>
            </motion.div>
          )}
        </div>
      </motion.div>
    </AnimatePresence>
  )
}
