import { Droppable, Draggable } from '@hello-pangea/dnd'
import { Badge } from '@/components/primitives/badge'
import { KanbanCard } from './kanban-card'
import { uz } from '@/lib/uz'
import type { CrmPipelineCard } from '@/lib/types'
import type { PipelineStage } from '@/hooks/use-pipeline'

interface KanbanColumnProps {
  stage: PipelineStage
  cards: CrmPipelineCard[]
  onSelectConversation?: (conversationId: number) => void
}

const stageColors: Record<PipelineStage, 'default' | 'info' | 'warning' | 'success' | 'danger' | 'muted'> = {
  new: 'default',
  qualified: 'info',
  negotiation: 'warning',
  proposal: 'info',
  payment: 'warning',
  delivery: 'muted',
  waiting: 'muted',
  won: 'success',
  lost: 'danger',
  manual_review: 'warning',
}

export function KanbanColumn({ stage, cards, onSelectConversation }: KanbanColumnProps) {
  const label = uz.pipeline.stages[stage] || stage

  return (
    <div className="flex min-w-[16rem] shrink-0 basis-64 flex-col">
      {/* Column header */}
      <div className="mb-2 flex items-center gap-2 px-1 overflow-visible whitespace-nowrap">
        <span className="text-xs font-semibold tracking-wide uppercase text-muted-foreground truncate">
          {label}
        </span>
        <Badge variant={stageColors[stage]} className="text-[10px]">
          {cards.length}
        </Badge>
      </div>

      {/* Droppable area */}
      <Droppable droppableId={stage}>
        {(provided, snapshot) => (
          <div
            ref={provided.innerRef}
            {...provided.droppableProps}
            className={`flex-1 space-y-2 overflow-y-auto rounded-lg p-1.5 transition-colors ${
              snapshot.isDraggingOver ? 'bg-muted/50' : ''
            }`}
          >
            {cards.map((card, index) => (
              <Draggable
                key={card.conversation_id}
                draggableId={String(card.conversation_id)}
                index={index}
              >
                {(provided, snapshot) => (
                  <div
                    ref={provided.innerRef}
                    {...provided.draggableProps}
                    {...provided.dragHandleProps}
                    className={snapshot.isDragging ? 'opacity-80' : ''}
                  >
                    <KanbanCard card={card} onSelectConversation={onSelectConversation} />
                  </div>
                )}
              </Draggable>
            ))}
            {provided.placeholder}

            {cards.length === 0 && (
              <div className="rounded-lg border border-dashed p-4 text-center text-[11px] text-muted-foreground">
                {uz.pipeline.empty}
              </div>
            )}
          </div>
        )}
      </Droppable>
    </div>
  )
}
