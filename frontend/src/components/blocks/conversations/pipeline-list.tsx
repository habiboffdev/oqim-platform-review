import { Kanban } from '@phosphor-icons/react'
import { EmptyState } from '@/components/primitives/empty-state'
import { Skeleton } from '@/components/ui/skeleton'
import { KanbanBoard } from '@/components/blocks/pipeline/kanban-board'
import { usePipeline } from '@/hooks/use-pipeline'
import { uz } from '@/lib/uz'

interface PipelineListProps {
  search: string
  onSelectConversation?: (conversationId: number) => void
}

export function PipelineList({ onSelectConversation }: PipelineListProps) {
  const { data: pipeline, isLoading } = usePipeline()

  if (isLoading) {
    return (
      <div className="flex gap-3 p-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="min-w-[16rem] shrink-0 basis-64 space-y-2">
            <Skeleton className="h-6 w-20" />
            <Skeleton className="h-24 w-full rounded-lg" />
            <Skeleton className="h-24 w-full rounded-lg" />
          </div>
        ))}
      </div>
    )
  }

  if (!pipeline) {
    return (
      <EmptyState
        icon={Kanban}
        title={uz.pipeline.empty}
        description={uz.pipeline.emptyDescription}
      />
    )
  }

  return (
    <div className="h-full">
      <KanbanBoard pipeline={pipeline} onSelectConversation={onSelectConversation} />
    </div>
  )
}
