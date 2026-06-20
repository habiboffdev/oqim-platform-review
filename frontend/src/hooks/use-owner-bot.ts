import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { api } from '@/lib/api-client'
import { uz } from '@/lib/uz'

export interface OwnerBotStatus {
  provisioned: boolean
  bot_username: string | null
  deep_link: string | null
  owner_chat_bound: boolean
}

export function useOwnerBotStatus() {
  return useQuery({
    queryKey: ['owner-bot-status'],
    queryFn: () => api.get<OwnerBotStatus>('/api/agent-control/owner-bot'),
    staleTime: 30_000,
  })
}

export interface OwnerBotProvisionInput {
  name: string
  username?: string
}

export function useOwnerBotProvision() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: OwnerBotProvisionInput) =>
      api.post<{ bot_username: string; deep_link: string }>(
        '/api/agent-control/owner-bot/provision',
        input,
      ),
    onSuccess: () => {
      toast.success(uz.settings.ownerBotCreated)
      queryClient.invalidateQueries({ queryKey: ['owner-bot-status'] })
    },
    onError: () => toast.error(uz.settings.ownerBotCreateError),
  })
}

export function useOwnerBotBindLink() {
  return useMutation({
    mutationFn: () =>
      api.post<{ deep_link: string }>('/api/agent-control/owner-bot/bind-link'),
    onError: () => toast.error(uz.settings.ownerBotLinkError),
  })
}

export function useOwnerBotUnbind() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => api.post<{ ok: boolean }>('/api/agent-control/owner-bot/unbind'),
    onSuccess: () => {
      toast.success(uz.settings.ownerBotUnbound)
      queryClient.invalidateQueries({ queryKey: ['owner-bot-status'] })
    },
    onError: () => toast.error(uz.settings.ownerBotUnbindError),
  })
}
