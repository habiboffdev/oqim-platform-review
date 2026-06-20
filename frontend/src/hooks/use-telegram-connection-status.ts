import { useQuery } from '@tanstack/react-query'

import { api } from '@/lib/api-client'
import type { TelegramConnectionStatus } from '@/lib/types'

export function useTelegramConnectionStatus() {
  return useQuery({
    queryKey: ['telegram-connection-status'],
    queryFn: () => api.get<TelegramConnectionStatus>('/api/telegram/auth/status'),
    staleTime: 5_000,
    refetchInterval: 10_000,
  })
}
