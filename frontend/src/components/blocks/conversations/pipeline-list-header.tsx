import { useState } from 'react'
import { Kanban } from '@phosphor-icons/react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { KanbanBoard } from '@/components/blocks/pipeline/kanban-board'
import { PIPELINE_STAGES } from '@/hooks/use-pipeline'
import { uz } from '@/lib/uz'
import type { PipelineStage } from '@/hooks/use-pipeline'
import type { Conversation, CrmPipelineProjection, CrmStageProjection } from '@/lib/types'

interface PipelineListHeaderProps {
  totalCount: number
  grouped: Record<PipelineStage, Conversation[]>
}

export function PipelineListHeader({ totalCount, grouped }: PipelineListHeaderProps) {
  const [kanbanOpen, setKanbanOpen] = useState(false)
  const pipeline: CrmPipelineProjection = {
    schema_version: 'crm_pipeline.v1',
    total: totalCount,
    stages: PIPELINE_STAGES.map((stage) => ({
      stage,
      count: grouped[stage]?.length ?? 0,
      cards: (grouped[stage] ?? []).map((conversation) => ({
        conversation_id: conversation.id,
        customer_id: conversation.customer_id,
        customer_name: conversation.customer_name,
        channel: conversation.channel,
        stage: conversation.crm_stage ?? fallbackStage(stage),
        last_message_text: conversation.last_message_text,
        last_message_at: conversation.last_message_at,
        unread_count: conversation.unread_count,
        has_pending_reply: Boolean(conversation.has_pending_reply),
        latest_reply_confidence: conversation.latest_reply_confidence,
        contact_type: conversation.contact_type,
        needs_attention: conversation.needs_attention,
        deal_value: conversation.deal_value,
      })),
    })),
  }

  return (
    <>
      <div className="flex items-center justify-between px-4 py-2 border-b">
        <span className="text-xs text-muted-foreground">
          {totalCount} {uz.pipeline.conversations}
        </span>
        <Button
          size="xs"
          variant="outline"
          onClick={() => setKanbanOpen(true)}
          className="gap-1.5 text-[11px]"
        >
          <Kanban size={13} weight="thin" />
          {uz.pipeline.kanbanView}
        </Button>
      </div>

      <Dialog open={kanbanOpen} onOpenChange={setKanbanOpen}>
        <DialogContent className="max-w-[95vw] h-[85vh] p-0">
          <KanbanBoard pipeline={pipeline} />
        </DialogContent>
      </Dialog>
    </>
  )
}

function fallbackStage(stage: PipelineStage): CrmStageProjection {
  return {
    schema_version: 'crm_stage.v1',
    stage,
    source: 'defaulted',
    products_interested: [],
    needs_attention: false,
    field_provenance: {},
  }
}
