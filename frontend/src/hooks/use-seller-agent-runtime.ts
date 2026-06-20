import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@/lib/api-client'
import { queryKeys } from '@/lib/query-keys'
import { uz } from '@/lib/uz'
import type { SellerAgentRuntimeStatus, SellerAgentRuntimeUpdate } from '@/lib/types'

export function useSellerAgentRuntime(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: queryKeys.admin.sellerAgentRuntime,
    queryFn: () => api.get<SellerAgentRuntimeStatus>('/api/admin/seller-agent-runtime'),
    enabled: options?.enabled ?? true,
    staleTime: 5_000,
    refetchInterval: 10_000,
  })
}

export function useUpdateSellerAgentRuntime() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: SellerAgentRuntimeUpdate) =>
      api.patch<SellerAgentRuntimeStatus>('/api/admin/seller-agent-runtime', data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.admin.sellerAgentRuntime })
      toast.success(uz.common.saved)
    },
    onError: () => {
      toast.error(uz.common.error)
    },
  })
}
