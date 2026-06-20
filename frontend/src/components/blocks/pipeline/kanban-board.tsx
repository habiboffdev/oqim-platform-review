import { DragDropContext, type DropResult } from '@hello-pangea/dnd'
import { KanbanColumn } from './kanban-column'
import { PIPELINE_STAGES, useUpdatePipelineStage } from '@/hooks/use-pipeline'
import type { CrmPipelineProjection } from '@/lib/types'

interface KanbanBoardProps {
  pipeline: CrmPipelineProjection
  onSelectConversation?: (conversationId: number) => void
}

export function KanbanBoard({ pipeline, onSelectConversation }: KanbanBoardProps) {
  const updateStage = useUpdatePipelineStage()
  const columns = new Map(pipeline.stages.map((column) => [column.stage, column]))
  const visibleStages = visiblePipelineStages(pipeline)

  function handleDragEnd(result: DropResult) {
    if (!result.destination) return
    if (result.source.droppableId === result.destination.droppableId) return

    const conversationId = Number(result.draggableId)
    const newStage = result.destination.droppableId

    updateStage.mutate({ conversationId, stage: newStage })
  }

  return (
    <DragDropContext onDragEnd={handleDragEnd}>
      <div className="flex h-full gap-3 overflow-x-auto px-6 pb-6">
        {visibleStages.map((stage) => (
          <KanbanColumn
            key={stage}
            stage={stage}
            cards={columns.get(stage)?.cards ?? []}
            onSelectConversation={onSelectConversation}
          />
        ))}
      </div>
    </DragDropContext>
  )
}

export function visiblePipelineStages(pipeline: CrmPipelineProjection) {
  const columns = new Map(pipeline.stages.map((column) => [column.stage, column]))
  const stagesWithCards = PIPELINE_STAGES.filter((stage) => (columns.get(stage)?.cards.length ?? 0) > 0)
  return stagesWithCards.length > 0 ? stagesWithCards : PIPELINE_STAGES
}
