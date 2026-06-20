import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api-client'
import { queryKeys } from '@/lib/query-keys'
import type { TelegramAuthDiagnosticsResponse } from '@/lib/types'

export function useTelegramAuthDiagnostics(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: queryKeys.admin.telegramAuthAttempts,
    queryFn: () => api.get<TelegramAuthDiagnosticsResponse>('/api/admin/telegram-auth-attempts'),
    enabled: options?.enabled ?? true,
    staleTime: 10_000,
    refetchInterval: 15_000,
  })
}
