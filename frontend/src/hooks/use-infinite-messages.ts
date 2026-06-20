import { useInfiniteQuery } from '@tanstack/react-query'

import { api } from '@/lib/api-client'
import { queryKeys } from '@/lib/query-keys'
import { recordPaginatedMessagesCursor } from '@/lib/sync-session'
import type { PaginatedMessages } from '@/lib/types'

export function getOlderMessagesPageParam(firstPage: PaginatedMessages): number | undefined {
  if (!firstPage?.has_older) return undefined
  // `history_gap` means Telegram may have older remote history, not that the
  // local projection has another page. Keep the gap visible, but do not turn it
  // into a local `before_id` loop that can replace a valid tail with an empty page.
  if (firstPage.history_gap) return undefined
  return firstPage.items[0]?.id
}

export function useInfiniteMessages(
  conversationId: number | undefined,
  options?: { enabled?: boolean },
) {
  return useInfiniteQuery({
    queryKey: queryKeys.messages.list(conversationId!),
    queryFn: async ({ pageParam }): Promise<PaginatedMessages> => {
      const params = new URLSearchParams({ limit: '50' })
      if (pageParam) params.set('before_id', String(pageParam))
      const page = await api.get<PaginatedMessages>(
        `/api/conversations/${conversationId}/messages?${params}`,
      )
      recordPaginatedMessagesCursor(conversationId, page)
      return page
    },
    enabled: !!conversationId && (options?.enabled ?? true),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: () => undefined,
    getPreviousPageParam: getOlderMessagesPageParam,
    staleTime: Infinity,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: 'always',
    refetchInterval: options?.enabled === false ? false : 5_000,
    maxPages: 20,
  })
}
