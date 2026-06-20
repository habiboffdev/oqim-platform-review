import {
  useQuery,
  useInfiniteQuery,
  useMutation,
  useQueryClient,
  type InfiniteData,
} from '@tanstack/react-query'
import { api } from '@/lib/api-client'
import { queryKeys } from '@/lib/query-keys'
import type {
  Conversation,
  ConversationFilters,
  LiveChat,
  LiveChatsResponse,
  PaginatedConversations,
} from '@/lib/types'

function trimAuthoritativeConversationPages(
  data: InfiniteData<PaginatedConversations>,
): InfiniteData<PaginatedConversations> {
  const trimmedPages: PaginatedConversations[] = []
  const trimmedPageParams: unknown[] = []

  for (const [index, page] of data.pages.entries()) {
    trimmedPages.push(page)
    trimmedPageParams.push(data.pageParams[index])
    if (!page.next_cursor) break
  }

  return {
    ...data,
    pages: trimmedPages,
    pageParams: trimmedPageParams,
  }
}

export function useConversations() {
  return useQuery({
    queryKey: queryKeys.conversations.all,
    queryFn: async () => {
      const res = await api.get<PaginatedConversations>('/api/conversations')
      return res.items
    },
    staleTime: 30_000,
  })
}

export function useLiveChats(filter?: { contact_type?: string }) {
  return useQuery({
    queryKey: queryKeys.liveChats,
    queryFn: async () => {
      const res = await api.get<LiveChatsResponse>('/api/conversations/live')
      return res.chats
    },
    staleTime: 5_000,
    refetchInterval: 10_000, // Poll every 10s for near-real-time ordering + unread updates
    select: filter?.contact_type
      ? (chats: LiveChat[]) => chats.filter((c) => c.contact_type === filter.contact_type)
      : undefined,
  })
}

export function useInfiniteConversations(filters?: ConversationFilters) {
  return useInfiniteQuery({
    queryKey: queryKeys.conversations.list(filters),
    queryFn: async ({ pageParam }) => {
      const params = new URLSearchParams()
      params.set('limit', '50')
      if (pageParam) params.set('cursor', pageParam)
      if (filters?.contact_type) params.set('contact_type', filters.contact_type)
      if (filters?.has_pending_reply) params.set('has_pending_reply', 'true')
      return api.get<PaginatedConversations>(`/api/conversations?${params.toString()}`)
    },
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    select: trimAuthoritativeConversationPages,
    staleTime: 5_000,
    refetchOnMount: 'always',
    refetchInterval: 10_000,
  })
}

export function useMarkAsRead() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (conversationId: number) =>
      api.post(`/api/conversations/${conversationId}/mark-read`),
    onMutate: async (conversationId) => {
      await queryClient.cancelQueries({ queryKey: queryKeys.conversations.all })
      const snapshot = queryClient.getQueriesData<InfiniteData<PaginatedConversations>>({
        queryKey: queryKeys.conversations.all,
      })
      queryClient.setQueriesData<InfiniteData<PaginatedConversations>>(
        { queryKey: queryKeys.conversations.all },
        (old) => {
          if (!old?.pages) return old
          return {
            ...old,
            pages: old.pages.map((page) => ({
              ...page,
              items: page.items.map((c) =>
                c.id === conversationId ? { ...c, unread_count: 0 } : c,
              ),
            })),
          }
        },
      )
      return { snapshot }
    },
    onError: (_err, _vars, context) => {
      context?.snapshot?.forEach(([key, data]) => {
        if (data) queryClient.setQueryData(key, data)
      })
    },
  })
}

export function useHydrateConversation() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (conversationId: number) =>
      api.post(`/api/conversations/${conversationId}/hydrate`, { limit: 50 }),
    onSuccess: (_data, conversationId) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all })
      queryClient.invalidateQueries({ queryKey: queryKeys.conversations.detail(conversationId) })
      queryClient.invalidateQueries({ queryKey: queryKeys.messages.list(conversationId) })
    },
  })
}

export function useConversation(id: number | undefined) {
  return useQuery({
    queryKey: queryKeys.conversations.detail(id!),
    queryFn: () => api.get<Conversation>(`/api/conversations/${id}`),
    enabled: !!id,
    staleTime: Infinity,
    refetchOnMount: 'always',
    refetchInterval: 60_000,
  })
}

export function useConversationByTelegramChat(telegramChatId: number | undefined) {
  return useQuery({
    queryKey: ['conversation-by-tg-chat', telegramChatId],
    queryFn: () => api.get<Conversation>(`/api/conversations/by-telegram-chat/${telegramChatId}`),
    enabled: !!telegramChatId,
    staleTime: 60_000,
  })
}
