import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@/lib/api-client'
import { queryKeys } from '@/lib/query-keys'
import { uz } from '@/lib/uz'
import type { SkillCandidate } from '@/lib/types'

// Live DB truth for the proposed SKILL.md candidates (GET /brain/skills/candidates),
// ordered by confidence server-side. This is authoritative over the onboarding
// projection's Redis count, which only reflects docgen progress and never updates
// on review — so the Ko'nikma badge + panel both read this.
export function useSkillCandidates(enabled = true) {
  return useQuery({
    queryKey: queryKeys.skillCandidates,
    queryFn: async () => {
      const res = await api.get<{ items: SkillCandidate[] }>('/api/brain/skills/candidates')
      return res.items
    },
    enabled,
    staleTime: 5_000,
  })
}

// Approve promotes the candidate into agent_skills; reject drops it. Both move the
// row out of `proposed`, so the card leaves the list immediately (optimistic) and
// is rolled back on error. onSettled re-syncs against the server.
function useReviewSkillCandidate(action: 'approve' | 'reject', successMessage: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: number) =>
      api.post<Record<string, unknown>>(`/api/brain/skills/candidates/${id}/${action}`),
    onMutate: async (id: number) => {
      await queryClient.cancelQueries({ queryKey: queryKeys.skillCandidates })
      const previous = queryClient.getQueryData<SkillCandidate[]>(queryKeys.skillCandidates)
      queryClient.setQueryData<SkillCandidate[]>(queryKeys.skillCandidates, (current) =>
        (current ?? []).filter((candidate) => candidate.id !== id),
      )
      return { previous }
    },
    onError: (_error, _id, context) => {
      if (context?.previous) {
        queryClient.setQueryData(queryKeys.skillCandidates, context.previous)
      }
      toast.error(uz.onboarding.documents.skillActionError)
    },
    onSuccess: () => {
      toast.success(successMessage)
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.skillCandidates })
    },
  })
}

export function useApproveSkillCandidate() {
  return useReviewSkillCandidate('approve', uz.onboarding.documents.skillApproved)
}

export function useRejectSkillCandidate() {
  return useReviewSkillCandidate('reject', uz.onboarding.documents.skillRejected)
}
