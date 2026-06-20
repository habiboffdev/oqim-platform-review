import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api-client'
import { queryKeys } from '@/lib/query-keys'
import type {
  AgentRunFeed,
  AgentRunState,
  AgentRunTimeline,
  ActionRuntimeDecision,
  ActionRuntimeExecution,
  ActionRuntimeInbox,
  ActionRuntimePolicy,
  CommercialActionProposal,
} from '@/lib/types'

const ACTOR_REF = 'ui:action_inbox'
const ACTIVE_AGENT_RUN_STATES = new Set<AgentRunState>([
  'queued',
  'running',
  'waiting_approval',
  'waiting_tool',
])

function correlationId(action: string, proposalId?: string) {
  const suffix = proposalId ? `:${proposalId}` : ''
  return `ui:action_inbox:${action}${suffix}:${Date.now()}`
}

function proposalPath(proposalId: string, action: string) {
  return `/api/action-runtime/proposals/${encodeURIComponent(proposalId)}/${action}`
}

function useActionRuntimeInvalidation() {
  const queryClient = useQueryClient()
  return () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.actionRuntime.inbox })
    void queryClient.invalidateQueries({ queryKey: queryKeys.actionRuntime.recentRuns })
    void queryClient.invalidateQueries({ queryKey: queryKeys.admin.runtimeSignals })
    void queryClient.invalidateQueries({ queryKey: queryKeys.bi.dashboard })
  }
}

export function useActionRuntimeInbox() {
  return useQuery({
    queryKey: queryKeys.actionRuntime.inbox,
    queryFn: () => api.get<ActionRuntimeInbox>('/api/action-runtime/inbox'),
    staleTime: 15_000,
  })
}

export function useActionRuntimePolicy() {
  return useQuery({
    queryKey: queryKeys.actionRuntime.policy,
    queryFn: () => api.get<ActionRuntimePolicy>('/api/action-runtime/policy'),
    staleTime: 30_000,
  })
}

export function useActionProposalTimeline(proposalId: string | null | undefined) {
  return useQuery({
    queryKey: queryKeys.actionRuntime.timeline(proposalId),
    queryFn: () =>
      api.get<AgentRunTimeline>(
        `/api/action-runtime/proposals/${encodeURIComponent(String(proposalId))}/timeline`,
      ),
    enabled: Boolean(proposalId),
    staleTime: 10_000,
  })
}

export function useRecentAgentRuns() {
  return useQuery({
    queryKey: queryKeys.actionRuntime.recentRuns,
    queryFn: () => api.get<AgentRunFeed>('/api/action-runtime/agent-runs/recent'),
    staleTime: 10_000,
    refetchInterval: (query) => recentAgentRunsRefetchInterval(query.state.data),
  })
}

export function recentAgentRunsRefetchInterval(feed: AgentRunFeed | undefined): number | false {
  if (!feed) return 5_000
  const hasActiveRun = feed.timelines.some((timeline) => {
    const state = timeline.run?.state
    return state ? ACTIVE_AGENT_RUN_STATES.has(state) : false
  })
  return hasActiveRun ? 1_500 : false
}

export function useProcessActionProposal() {
  const invalidate = useActionRuntimeInvalidation()
  return useMutation({
    mutationFn: (proposalId: string) =>
      api.post<ActionRuntimeDecision>('/api/action-runtime/process', {
        proposal_id: proposalId,
        correlation_id: correlationId('process', proposalId),
      }),
    onSuccess: invalidate,
  })
}

export function useApproveActionProposal() {
  const invalidate = useActionRuntimeInvalidation()
  return useMutation({
    mutationFn: (proposalId: string) =>
      api.post<CommercialActionProposal>(proposalPath(proposalId, 'approve'), {
        actor_ref: ACTOR_REF,
        correlation_id: correlationId('approve', proposalId),
      }),
    onSuccess: invalidate,
  })
}

export function useRejectActionProposal() {
  const invalidate = useActionRuntimeInvalidation()
  return useMutation({
    mutationFn: ({
      proposalId,
      reasonCode = 'seller_rejected_from_action_inbox',
    }: {
      proposalId: string
      reasonCode?: string
    }) =>
      api.post<CommercialActionProposal>(proposalPath(proposalId, 'reject'), {
        actor_ref: ACTOR_REF,
        reason_code: reasonCode,
        correlation_id: correlationId('reject', proposalId),
      }),
    onSuccess: invalidate,
  })
}

export function useEditActionProposalDraft() {
  const invalidate = useActionRuntimeInvalidation()
  return useMutation({
    mutationFn: ({
      proposalId,
      draftText,
    }: {
      proposalId: string
      draftText: string
    }) =>
      api.post<CommercialActionProposal>(proposalPath(proposalId, 'draft'), {
        actor_ref: ACTOR_REF,
        draft_text: draftText,
        correlation_id: correlationId('draft-edit', proposalId),
      }),
    onSuccess: invalidate,
  })
}

export function useExecuteActionProposal() {
  const invalidate = useActionRuntimeInvalidation()
  return useMutation({
    mutationFn: (proposalId: string) =>
      api.post<ActionRuntimeExecution>(proposalPath(proposalId, 'execute'), {
        actor_ref: ACTOR_REF,
        correlation_id: correlationId('execute', proposalId),
      }),
    onSuccess: invalidate,
  })
}

export function useRequeueActionProposal() {
  const invalidate = useActionRuntimeInvalidation()
  return useMutation({
    mutationFn: (proposalId: string) =>
      api.post<CommercialActionProposal>(proposalPath(proposalId, 'requeue'), {
        patch_payload: {},
        actor_ref: ACTOR_REF,
        correlation_id: correlationId('requeue', proposalId),
      }),
    onSuccess: invalidate,
  })
}
