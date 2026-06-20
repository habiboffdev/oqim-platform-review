import { describe, expect, it } from 'vitest'

import { visiblePipelineStages } from './kanban-board'
import type { CrmPipelineProjection } from '@/lib/types'

function pipeline(stages: CrmPipelineProjection['stages']): CrmPipelineProjection {
  return {
    schema_version: 'crm_pipeline.v1',
    total: stages.reduce((sum, stage) => sum + stage.count, 0),
    stages,
  }
}

describe('visiblePipelineStages', () => {
  it('hides empty stages once OQIM has classified the pipeline', () => {
    expect(
      visiblePipelineStages(
        pipeline([
          { stage: 'new', count: 108, cards: [{} as never] },
          { stage: 'payment', count: 27, cards: [{} as never] },
          { stage: 'delivery', count: 11, cards: [{} as never] },
          { stage: 'manual_review', count: 3, cards: [{} as never] },
          { stage: 'proposal', count: 0, cards: [] },
        ]),
      ),
    ).toEqual(['new', 'payment', 'delivery', 'manual_review'])
  })
})
