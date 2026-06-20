import type { QueryClient } from '@tanstack/react-query'
import { queryKeys } from '@/lib/query-keys'
import { uz } from '@/lib/uz'
import { reconcileActiveTail } from '@/lib/active-tail-sync'
import {
  applyProjectionSync,
  patchConversationState,
} from '@/lib/projection-client'

type ActivityDispatch = (data: Record<string, unknown>) => void

export type ApplyWebSocketSyncSessionOptions = {
  queryClient: QueryClient
  rawSyncData: Record<string, unknown>
  activeConversationId?: number
  sequenceId?: number
  dispatchActivityEvent: ActivityDispatch
  recordSequence?: (sequenceId: number) => void
}

function refreshScopedRuntimeState(
  queryClient: QueryClient,
  conversationId?: number,
  options?: { afterConversationSeq?: number },
) {
  queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all })
  queryClient.invalidateQueries({ queryKey: queryKeys.liveChats })
  queryClient.invalidateQueries({ queryKey: queryKeys.sellerAgentReplyInbox })

  if (conversationId) {
    queryClient.invalidateQueries({
      queryKey: queryKeys.sellerAgentReplies.byConversation(conversationId),
    })
    queryClient.invalidateQueries({
      queryKey: queryKeys.conversations.detail(conversationId),
    })
    void reconcileActiveTail(queryClient, conversationId, options)
  } else {
    queryClient.invalidateQueries({ queryKey: queryKeys.sellerAgentReplies.all })
  }
}

export function applyWebSocketSyncSession({
  queryClient,
  rawSyncData,
  activeConversationId,
  sequenceId,
  dispatchActivityEvent,
  recordSequence,
}: ApplyWebSocketSyncSessionOptions) {
  const syncData = (rawSyncData.data as Record<string, unknown> | undefined) || rawSyncData
  const scopedConversationId = (syncData.conversation_id as number | undefined) ?? activeConversationId

  dispatchActivityEvent({
    type: 'sync:complete',
    scope: 'system',
    message: uz.activity.sessionRecoveryComplete,
    data: {
      action: syncData.action,
      sequence_id: sequenceId,
    },
    ts: Math.floor(Date.now() / 1000),
  })

  if (sequenceId) recordSequence?.(sequenceId)
  if (scopedConversationId) {
    const conversationState = syncData.conversation_state as Record<string, unknown> | undefined
    if (conversationState) {
      patchConversationState(queryClient, scopedConversationId, {
        ...conversationState,
        latest_conversation_seq: syncData.latest_conversation_seq
          ?? conversationState.latest_conversation_seq,
        latest_conversation_revision: syncData.latest_conversation_revision
          ?? conversationState.latest_conversation_revision,
      })
    }
  }

  if (applyProjectionSync(queryClient, syncData, scopedConversationId)) {
    return
  }
  if (syncData.action === 'invalidate_all' || syncData.action === 'refresh_scoped_runtime') {
    refreshScopedRuntimeState(queryClient, scopedConversationId)
  }
  if (syncData.action === 'refresh_scoped_runtime_delta') {
    const afterConversationSeq = syncData.after_conversation_seq as number | undefined
    refreshScopedRuntimeState(queryClient, scopedConversationId, {
      afterConversationSeq,
    })
  }
}
