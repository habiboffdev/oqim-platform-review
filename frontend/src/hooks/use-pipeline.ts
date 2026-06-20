import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@/lib/api-client'
import { uz } from '@/lib/uz'
import type { CrmPipelineProjection, CrmStageProjection } from '@/lib/types'

export const PIPELINE_STAGES: CrmStageProjection['stage'][] = [
  'new',
  'qualified',
  'negotiation',
  'proposal',
  'payment',
  'delivery',
  'waiting',
  'won',
  'lost',
  'manual_review',
]
export type PipelineStage = CrmStageProjection['stage']

export function usePipeline() {
  return useQuery({
    queryKey: ['pipeline'],
    queryFn: () => api.get<CrmPipelineProjection>('/api/conversations/pipeline'),
    staleTime: 30_000,
  })
}

export function useUpdatePipelineStage() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ conversationId, stage }: { conversationId: number; stage: string }) =>
      api.patch(`/api/conversations/${conversationId}`, { pipeline_stage: stage }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pipeline'] })
      queryClient.invalidateQueries({ queryKey: ['conversations'] })
      toast.success(uz.common.saved)
    },
    onError: () => { toast.error(uz.common.error) },
  })
}
