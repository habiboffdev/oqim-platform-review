import type { QueryClient, InfiniteData } from '@tanstack/react-query'

import { reconcileActiveTail } from '@/lib/active-tail-sync'
import { queryKeys } from '@/lib/query-keys'
import { buildSessionResumePayload } from '@/lib/sync-session'
import { uz } from '@/lib/uz'
import type { PaginatedMessages } from '@/lib/types'

type MutableRef<T> = {
  current: T
}

type WebSocketLifecycleManager = {
  on: (
    event: 'reconnect',
    handler: (data: Record<string, unknown>) => void,
  ) => () => void
  send: (payload: Record<string, unknown>) => void
}

type ActivityDispatcher = (event: Record<string, unknown>) => void

export type RegisterReconnectLifecycleOptions = {
  wsManager: WebSocketLifecycleManager
  activeConversationRef: MutableRef<number | undefined>
  lastSequenceRef: MutableRef<number>
  dispatchActivityEvent: ActivityDispatcher
}

export function registerReconnectLifecycle({
  wsManager,
  activeConversationRef,
  lastSequenceRef,
  dispatchActivityEvent,
}: RegisterReconnectLifecycleOptions): () => void {
  return wsManager.on('reconnect', () => {
    const currentId = activeConversationRef.current

    dispatchActivityEvent({
      type: 'sync:checking',
      scope: 'system',
      message: uz.activity.sessionRecoveryChecking,
      data: {
        conversation_id: currentId,
        last_sequence: lastSequenceRef.current,
      },
      ts: Math.floor(Date.now() / 1000),
    })

    wsManager.send(buildSessionResumePayload(currentId, lastSequenceRef.current) as unknown as Record<string, unknown>)

    if (currentId) {
      wsManager.send({ type: 'chat_opened', conversation_id: currentId })
    }
  })
}

export type RegisterActiveConversationLifecycleOptions = {
  wsManager: Pick<WebSocketLifecycleManager, 'send'>
  queryClient: QueryClient
  activeConversationId: number
}

export function registerActiveConversationLifecycle({
  wsManager,
  queryClient,
  activeConversationId,
}: RegisterActiveConversationLifecycleOptions): () => void {
  wsManager.send({ type: 'chat_opened', conversation_id: activeConversationId })

  const cached = queryClient.getQueryData<InfiniteData<PaginatedMessages>>(
    queryKeys.messages.list(activeConversationId),
  )
  if (cached?.pages?.length) {
    void reconcileActiveTail(queryClient, activeConversationId)
  }

  return () => {
    wsManager.send({ type: 'chat_closed', conversation_id: activeConversationId })
  }
}
