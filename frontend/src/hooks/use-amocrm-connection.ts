import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { api } from '@/lib/api-client'
import { uz } from '@/lib/uz'

export interface AmoCrmConnectionStatus {
  connected: boolean
  provider_account_ref: string | null
  expires_at: string | null
  needs_reconnect: boolean
}

export function useAmoCrmConnectionStatus() {
  return useQuery({
    queryKey: ['amocrm-connection-status'],
    queryFn: () => api.get<AmoCrmConnectionStatus>('/api/amocrm/auth/status'),
    staleTime: 30_000,
  })
}

export function useAmoCrmConnect() {
  return useMutation({
    mutationFn: () => api.get<{ authorize_url: string }>('/api/amocrm/auth/start'),
    onSuccess: (data) => {
      window.location.assign(data.authorize_url)
    },
    onError: () => toast.error(uz.settings.amocrmConnectError),
  })
}

export function useAmoCrmDisconnect() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => api.post<{ status: string }>('/api/amocrm/auth/disconnect'),
    onSuccess: () => {
      toast.success(uz.settings.amocrmDisconnected)
      queryClient.invalidateQueries({ queryKey: ['amocrm-connection-status'] })
    },
    onError: () => toast.error(uz.settings.amocrmDisconnectError),
  })
}
