import { Link } from '@tanstack/react-router'
import { Avatar } from '@/components/primitives/avatar'
import { truncate } from '@/lib/utils'
import type { Conversation } from '@/lib/types'

interface PipelineListItemProps {
  conversation: Conversation
}

export function PipelineListItem({ conversation: conv }: PipelineListItemProps) {
  const hasPendingReply = conv.has_pending_reply ?? false

  return (
    <Link
      to="/conversations/$conversationId"
      params={{ conversationId: String(conv.id) }}
      className="flex items-center gap-2.5 px-4 py-2.5 pl-9 transition-colors hover:bg-muted/30"
    >
      <Avatar name={conv.customer_name || '?'} size="sm" />
      <div className="flex-1 min-w-0">
        <span className="text-xs font-medium truncate">
          {conv.customer_name || `#${conv.id}`}
        </span>
        {conv.summary && (
          <span className="ml-2 text-[10px] text-muted-foreground">
            {truncate(conv.summary, 40)}
          </span>
        )}
      </div>
      {hasPendingReply && (
        <div
          data-testid="pending-reply-indicator"
          className="size-1.5 shrink-0 rounded-full bg-blue-400"
        />
      )}
    </Link>
  )
}
