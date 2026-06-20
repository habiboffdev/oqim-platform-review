import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { api } from '@/lib/api-client'
import { uz } from '@/lib/uz'

export interface InstagramConnectionStatus {
  connected: boolean
  page_id: string | null
  expires_at: string | null
  needs_reconnect: boolean
}

export function useInstagramConnectionStatus() {
  return useQuery({
    queryKey: ['instagram-connection-status'],
    queryFn: () => api.get<InstagramConnectionStatus>('/api/instagram/auth/status'),
    staleTime: 30_000,
  })
}

export function useInstagramConnect() {
  return useMutation({
    mutationFn: () => api.get<{ authorize_url: string }>('/api/instagram/auth/start'),
    onSuccess: (data) => {
      window.location.assign(data.authorize_url)
    },
    onError: () => toast.error(uz.settings.instagramConnectError),
  })
}

export function useInstagramDisconnect() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => api.post<{ status: string }>('/api/instagram/auth/disconnect'),
    onSuccess: () => {
      toast.success(uz.settings.instagramDisconnected)
      queryClient.invalidateQueries({ queryKey: ['instagram-connection-status'] })
    },
    onError: () => toast.error(uz.settings.instagramDisconnectError),
  })
}
