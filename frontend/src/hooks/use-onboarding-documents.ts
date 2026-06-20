import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@/lib/api-client'
import { queryKeys } from '@/lib/query-keys'
import { uz } from '@/lib/uz'
import { useMountEffect } from '@/hooks/use-mount-effect'
import type { OnboardingDocumentsProjection } from '@/lib/types'

export function useOnboardingDocuments(enabled = true) {
  return useQuery({
    queryKey: queryKeys.onboardingDocuments,
    queryFn: () => api.get<OnboardingDocumentsProjection>('/api/onboarding/documents'),
    enabled,
    staleTime: 5_000,
  })
}

export function useOnboardingDocumentsStream(enabled = true) {
  const queryClient = useQueryClient()

  // Mirror use-onboarding-runtime's EventSource→cache bridge, scoped to the
  // mount of the documents phase. The phase mounts/unmounts as a unit, so a
  // mount-only subscription with an `enabled` guard is sufficient.
  useMountEffect(() => {
    if (!enabled || typeof window === 'undefined' || !('EventSource' in window)) return
    const stream = new EventSource('/api/onboarding/documents/stream', { withCredentials: true })
    stream.addEventListener('documents', (event) => {
      try {
        queryClient.setQueryData(
          queryKeys.onboardingDocuments,
          JSON.parse((event as MessageEvent).data) as OnboardingDocumentsProjection,
        )
      } catch {
        queryClient.invalidateQueries({ queryKey: queryKeys.onboardingDocuments })
      }
      // Skills are learned during the same docgen run; refetch the live candidate
      // list so the Ko'nikma badge + panel surface them without a manual refresh.
      void queryClient.invalidateQueries({ queryKey: queryKeys.skillCandidates })
    })
    stream.addEventListener('documents.error', () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.onboardingDocuments })
    })
    stream.addEventListener('documents.heartbeat', () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.onboardingDocuments })
    })
    return () => stream.close()
  })
}

export function useGenerateOnboardingDocuments() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => api.post<{ status: string }>('/api/onboarding/documents/generate'),
    onSuccess: () => {
      toast.success(uz.onboarding.documents.generateStarted)
      void queryClient.invalidateQueries({ queryKey: queryKeys.onboardingDocuments })
    },
    onError: () => {
      toast.error(uz.onboarding.documents.generateFailed)
    },
  })
}

// Per-agent AGENT.md regeneration from a single owner_input string (the shared
// payload behind the Defaults / Speak / Upload paths). Backend regenerates the
// system-generated sections and preserves owner-locked ones, then the onboarding
// documents stream re-emits the agent block — so we invalidate that projection.
export function useGenerateAgentMd(agentId: number | undefined) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (ownerInput: string) => {
      if (!agentId) throw new Error('agent_id_unavailable')
      return api.post<Record<string, unknown>>(
        `/api/brain/agents/${agentId}/agent-md/generate`,
        { owner_input: ownerInput },
      )
    },
    onSuccess: () => {
      toast.success(uz.onboarding.documents.agentPaths.generateSuccess)
      void queryClient.invalidateQueries({ queryKey: queryKeys.onboardingDocuments })
    },
    onError: () => {
      toast.error(uz.onboarding.documents.agentPaths.generateError)
    },
  })
}
