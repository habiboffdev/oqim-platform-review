import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api-client'
import { queryKeys } from '@/lib/query-keys'
import type { RuntimeSignalsResponse } from '@/lib/types'

export function useRuntimeSignals(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: queryKeys.admin.runtimeSignals,
    queryFn: () => api.get<RuntimeSignalsResponse>('/api/admin/runtime-signals'),
    enabled: options?.enabled ?? true,
    staleTime: 15_000,
    refetchInterval: 30_000,
  })
}
