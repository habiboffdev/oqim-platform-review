import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api-client'
import { queryKeys } from '@/lib/query-keys'
import type { SellerAgentReply } from '@/lib/types'

export function useSellerAgentReplyInbox() {
  return useQuery({
    queryKey: queryKeys.sellerAgentReplyInbox,
    queryFn: () => api.get<SellerAgentReply[]>('/api/ai-replies?status=draft'),
    staleTime: 30_000,
  })
}
