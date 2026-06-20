import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api-client'
import { queryKeys } from '@/lib/query-keys'
import type { WorkspaceOSProjection } from '@/lib/types'

export function useWorkspaceOS(enabled = true) {
  return useQuery({
    queryKey: queryKeys.workspaceOS.state,
    queryFn: () => api.get<WorkspaceOSProjection>('/api/workspace-os/state'),
    enabled,
    staleTime: 5_000,
    refetchInterval: 10_000,
  })
}

export function useProvisionWorkspaceOS() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => api.post<WorkspaceOSProjection>('/api/workspace-os/provision'),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.workspaceOS.state, data)
    },
  })
}
