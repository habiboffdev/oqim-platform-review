import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@/lib/api-client'
import { queryKeys } from '@/lib/query-keys'
import { uz } from '@/lib/uz'
import type { Agent } from '@/lib/types'

export function useAgents() {
  return useQuery({
    queryKey: queryKeys.agents.all,
    queryFn: () => api.get<Agent[]>('/api/agents'),
    staleTime: 60_000,
  })
}

export function useUpdateAgent() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...data }: { id: number } & Partial<Agent>) =>
      api.put(`/api/agents/${id}`, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.agents.all })
      toast.success(uz.common.saved)
    },
    onError: () => { toast.error(uz.common.error) },
  })
}

export function useCustomerAgent() {
  const { data: agents } = useAgents()
  return agents?.find((a) => a.agent_type === 'customer') ?? agents?.[0] ?? null
}

export function useSetDefaultAgent() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api.post(`/api/agents/${id}/default`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.agents.all })
      toast.success(uz.common.saved)
    },
    onError: () => { toast.error(uz.common.error) },
  })
}
