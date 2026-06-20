import { formatRelativeTime } from '@/lib/utils'
import { uz } from '@/lib/uz'
import { Avatar } from '@/components/primitives/avatar'

interface ConversationHeaderProps {
  customerName: string
  contactType?: string
  pipelineStage?: string
  lastActiveAt?: string
}

export function ConversationHeader({
  customerName,
  contactType,
  pipelineStage,
  lastActiveAt,
}: ConversationHeaderProps) {
  const typeLabel = contactType ? uz.customer.types[contactType] : undefined
  const stageLabel = pipelineStage ? uz.pipeline.stages[pipelineStage as keyof typeof uz.pipeline.stages] : undefined

  return (
    <div className="flex items-center gap-3 border-b bg-background px-4 py-2">
      <Avatar name={customerName} size="sm" />
      <div className="min-w-0 flex-1">
        <span className="truncate text-sm font-medium text-foreground">{customerName}</span>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {typeLabel && <span>{typeLabel}</span>}
          {typeLabel && stageLabel && <span aria-hidden="true">&middot;</span>}
          {stageLabel && <span>{stageLabel}</span>}
          {(typeLabel || stageLabel) && lastActiveAt && <span aria-hidden="true">&middot;</span>}
          {lastActiveAt && <span>{formatRelativeTime(lastActiveAt)}</span>}
        </div>
      </div>
    </div>
  )
}
