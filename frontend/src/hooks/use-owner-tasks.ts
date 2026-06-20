import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api-client'
import { queryKeys } from '@/lib/query-keys'
import type {
  ActionRuntimeExecution,
  CommercialActionProposal,
  OwnerTaskItem,
  OwnerTaskProjection,
} from '@/lib/types'

const ACTOR_REF = 'ui:owner_tasks'

function correlationId(action: string, proposalId?: string) {
  const suffix = proposalId ? `:${proposalId}` : ''
  return `ui:owner_tasks:${action}${suffix}:${Date.now()}`
}

function taskPath(proposalId: string, action: string) {
  return `/api/action-runtime/tasks/${encodeURIComponent(proposalId)}/${action}`
}

function useOwnerTaskInvalidation() {
  const queryClient = useQueryClient()
  return () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.actionRuntime.tasks })
    void queryClient.invalidateQueries({ queryKey: queryKeys.actionRuntime.inbox })
    void queryClient.invalidateQueries({ queryKey: queryKeys.admin.runtimeSignals })
    void queryClient.invalidateQueries({ queryKey: queryKeys.bi.dashboard })
  }
}

export function useOwnerTasks() {
  return useQuery({
    queryKey: queryKeys.actionRuntime.tasks,
    queryFn: () => api.get<OwnerTaskProjection>('/api/action-runtime/tasks'),
    staleTime: 15_000,
  })
}

export function useAcceptOwnerTask() {
  const invalidate = useOwnerTaskInvalidation()
  return useMutation({
    mutationFn: (proposalId: string) =>
      api.post<CommercialActionProposal>(taskPath(proposalId, 'accept'), {
        actor_ref: ACTOR_REF,
        correlation_id: correlationId('accept', proposalId),
      }),
    onSuccess: invalidate,
  })
}

export function useCompleteOwnerTask() {
  const invalidate = useOwnerTaskInvalidation()
  return useMutation({
    mutationFn: (proposalId: string) =>
      api.post<ActionRuntimeExecution>(taskPath(proposalId, 'complete'), {
        actor_ref: ACTOR_REF,
        correlation_id: correlationId('complete', proposalId),
      }),
    onSuccess: invalidate,
  })
}

export function useDismissOwnerTask() {
  const invalidate = useOwnerTaskInvalidation()
  return useMutation({
    mutationFn: (proposalId: string) =>
      api.post<CommercialActionProposal>(taskPath(proposalId, 'dismiss'), {
        actor_ref: ACTOR_REF,
        reason_code: 'owner_dismissed_task',
        correlation_id: correlationId('dismiss', proposalId),
      }),
    onSuccess: invalidate,
  })
}

export function useSnoozeOwnerTask() {
  const invalidate = useOwnerTaskInvalidation()
  return useMutation({
    mutationFn: ({ proposalId, dueAt }: { proposalId: string; dueAt?: string }) =>
      api.post<OwnerTaskItem>(taskPath(proposalId, 'snooze'), {
        actor_ref: ACTOR_REF,
        due_at: dueAt,
        correlation_id: correlationId('snooze', proposalId),
      }),
    onSuccess: invalidate,
  })
}
