import { motion, AnimatePresence } from 'framer-motion'
import { X } from '@phosphor-icons/react'
import { ChatViewer } from '@/components/blocks/chat/chat-viewer'
import { ConversationHeader } from '@/components/blocks/chat/conversation-header'
import { ComposeOverlay } from '@/components/blocks/compose/compose-overlay'
import { useConversations } from '@/hooks/use-conversations'
import { useLatestSellerAgentReply } from '@/hooks/use-seller-agent-replies'
import { uz } from '@/lib/uz'

interface ChatSheetProps {
  conversationId: number | null
  onClose: () => void
}

export function ChatSheet({ conversationId, onClose }: ChatSheetProps) {
  const { data: conversations } = useConversations()
  const reply = useLatestSellerAgentReply(conversationId ?? undefined)
  const convo = conversations?.find((c) => c.id === conversationId)
  const pipelineStage = convo?.crm_stage?.stage

  return (
    <AnimatePresence>
      {conversationId && convo && (
        <motion.div
          initial={{ width: 0, opacity: 0 }}
          animate={{ width: '40%', opacity: 1 }}
          exit={{ width: 0, opacity: 0 }}
          transition={{ type: 'spring', stiffness: 400, damping: 35 }}
          className="relative flex h-full shrink-0 flex-col border-l overflow-hidden"
        >
          {/* Rich header with close button */}
          <div className="relative">
            <ConversationHeader
              customerName={convo.customer_name}
              contactType={convo.contact_type}
              pipelineStage={pipelineStage}
              lastActiveAt={convo.last_message_at}
            />
            <button
              onClick={onClose}
              aria-label={uz.common.close}
              className="absolute right-3 top-1/2 -translate-y-1/2 rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <X size={16} weight="thin" />
            </button>
          </div>

          {/* Chat messages */}
          <div className="flex-1 overflow-hidden">
            <ChatViewer conversationId={conversationId} />
          </div>

          {/* Seller Agent reply overlay */}
          {reply && <ComposeOverlay reply={reply} />}
        </motion.div>
      )}
    </AnimatePresence>
  )
}
