import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@/lib/api-client'
import { queryKeys } from '@/lib/query-keys'
import { uz } from '@/lib/uz'
import type { LlmPolicyStatus, LlmPolicyUpdate } from '@/lib/types'

export function useLlmPolicies(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: queryKeys.admin.llmPolicies,
    queryFn: () => api.get<LlmPolicyStatus>('/api/admin/llm-policies'),
    enabled: options?.enabled ?? true,
    staleTime: 5_000,
    refetchInterval: 15_000,
  })
}

export function useUpdateLlmPolicies() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: LlmPolicyUpdate) =>
      api.patch<LlmPolicyStatus>('/api/admin/llm-policies', data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.admin.llmPolicies })
      toast.success(uz.common.saved)
    },
    onError: () => {
      toast.error(uz.common.error)
    },
  })
}
