import { useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api-client'
import { queryKeys } from '@/lib/query-keys'
import type { OnboardingRuntimeProjection } from '@/lib/types'

export function useOnboardingRuntime(enabled = true) {
  const queryClient = useQueryClient()

  useEffect(() => {
    if (!enabled || typeof window === 'undefined' || !('EventSource' in window)) return
    const stream = new EventSource('/api/onboarding/runtime/stream', { withCredentials: true })
    stream.addEventListener('runtime', (event) => {
      try {
        queryClient.setQueryData(
          queryKeys.onboarding.runtime,
          JSON.parse((event as MessageEvent).data) as OnboardingRuntimeProjection,
        )
      } catch {
        queryClient.invalidateQueries({ queryKey: queryKeys.onboarding.runtime })
      }
    })
    stream.addEventListener('runtime.error', () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.onboarding.runtime })
    })
    stream.addEventListener('runtime.heartbeat', () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.onboarding.runtime })
    })
    return () => stream.close()
  }, [enabled, queryClient])

  return useQuery({
    queryKey: queryKeys.onboarding.runtime,
    queryFn: () => api.get<OnboardingRuntimeProjection>('/api/onboarding/runtime'),
    enabled,
    staleTime: 5_000,
    refetchInterval: (query) => {
      const runtime = query.state.data
      if (!runtime) return 5_000
      const learning = runtime.source_learning?.summary.learning ?? 0
      const retrying = runtime.source_learning?.summary.retrying ?? 0
      if (runtime.is_running || learning > 0 || retrying > 0) return 1_500
      return 5_000
    },
  })
}
