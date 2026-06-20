import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@/lib/api-client'
import { queryKeys } from '@/lib/query-keys'
import type {
  BICommandRequest,
  BICommandResult,
  BIAnalyticsDashboard,
  BIInvestigationResult,
  PromoterCampaignPlan,
  PromoterPolicy,
} from '@/lib/types'

export function useBIAnalyticsDashboard() {
  return useQuery({
    queryKey: queryKeys.bi.dashboard,
    queryFn: () => api.get<BIAnalyticsDashboard>('/api/bi-promoter/analytics/dashboard'),
    staleTime: 30_000,
  })
}

export function usePromoterPolicy() {
  return useQuery({
    queryKey: queryKeys.bi.policy,
    queryFn: () => api.get<PromoterPolicy>('/api/bi-promoter/promoter/policy'),
    staleTime: 30_000,
  })
}

export function useBICommandMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: BICommandRequest) =>
      api.post<BICommandResult>('/api/bi-promoter/commands', input),
    onSuccess: (result) => {
      toast.success(result.message_uz)
      void queryClient.invalidateQueries({ queryKey: queryKeys.bi.commands })
      void queryClient.invalidateQueries({ queryKey: queryKeys.actionRuntime.inbox })
      void queryClient.invalidateQueries({ queryKey: queryKeys.actionRuntime.tasks })
      void queryClient.invalidateQueries({ queryKey: queryKeys.agents.all })
      void queryClient.invalidateQueries({ queryKey: queryKeys.workspaceOS.state })
      void queryClient.invalidateQueries({ queryKey: queryKeys.bi.dashboard })
    },
    onError: () => {
      toast.error("BI buyrug'i bajarilmadi. Qayta urinib ko'ring.")
    },
  })
}

export function useBIInvestigationMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => {
      const stamp = Date.now()
      return api.post<BIInvestigationResult>('/api/bi-promoter/investigations', {
        investigation_ref: `ui:investigation:${stamp}`,
        topic: 'Review projection risks, stalled opportunities, and needed fixes',
        source_refs: ['ui:bi_promoter:investigation'],
        correlation_id: `ui:bi:investigation:${stamp}`,
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.bi.dashboard })
    },
  })
}

export function usePromoterPlanMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => {
      const stamp = Date.now()
      return api.post<PromoterCampaignPlan>(
        '/api/bi-promoter/promoter/campaigns/plan-from-projections',
        {
          campaign_ref: `ui:campaign:${stamp}`,
          approval_state: 'proposed',
          message_goal: 'Reconnect with eligible customers using current offers',
          offer_refs: [],
          source_refs: ['ui:bi_promoter:campaign'],
          correlation_id: `ui:promoter:campaign:${stamp}`,
        },
      )
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.bi.dashboard })
    },
  })
}
