type SyncProjection = {
  name?: string
  mode?: 'delta' | 'reset'
  conversation_id?: number
  after_conversation_seq?: number
}

export type ProjectionSyncPlan = {
  messageTail?: {
    conversationId: number
    afterConversationSeq?: number
  }
  invalidateConversations: boolean
  invalidateLiveChats: boolean
  invalidateReplyInbox: boolean
  invalidateAllSellerAgentReplies: boolean
  detailConversationIds: number[]
  replyConversationIds: number[]
}

function sortedIds(ids: Set<number>): number[] {
  return [...ids].sort((a, b) => a - b)
}

function validConversationId(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
    ? value
    : undefined
}

function hasPlanActions(plan: ProjectionSyncPlan): boolean {
  return Boolean(
    plan.messageTail
      || plan.invalidateConversations
      || plan.invalidateLiveChats
      || plan.invalidateReplyInbox
      || plan.invalidateAllSellerAgentReplies
      || plan.detailConversationIds.length > 0
      || plan.replyConversationIds.length > 0,
  )
}

export function buildProjectionSyncPlan(
  projections: unknown,
  scopedConversationId?: number,
): ProjectionSyncPlan | null {
  if (!Array.isArray(projections)) return null

  let messageTail: ProjectionSyncPlan['messageTail']
  let invalidateConversations = false
  let invalidateLiveChats = false
  let invalidateReplyInbox = false
  const detailConversationIds = new Set<number>()
  const replyConversationIds = new Set<number>()

  for (const rawProjection of projections) {
    if (!rawProjection || typeof rawProjection !== 'object') continue
    const projection = rawProjection as SyncProjection
    const projectionConversationId = validConversationId(
      projection.conversation_id ?? scopedConversationId,
    )

    if (projection.name === 'messages' || projection.name === 'media') {
      if (projectionConversationId && !messageTail) {
        messageTail = {
          conversationId: projectionConversationId,
          afterConversationSeq: projection.mode === 'delta'
            ? projection.after_conversation_seq
            : undefined,
        }
      }
      if (projectionConversationId) detailConversationIds.add(projectionConversationId)
      if (projection.name === 'media') {
        invalidateConversations = true
        invalidateLiveChats = true
      }
      continue
    }

    if (projection.name === 'conversation_state') {
      if (projectionConversationId) detailConversationIds.add(projectionConversationId)
      invalidateConversations = true
      invalidateLiveChats = true
      continue
    }

    if (projection.name === 'seller_agent_replies') {
      invalidateReplyInbox = true
      if (projectionConversationId) replyConversationIds.add(projectionConversationId)
      continue
    }

    if (projection.name === 'read_state' || projection.name === 'conversations') {
      invalidateConversations = true
      invalidateLiveChats = true
      continue
    }
  }

  const plan = {
    messageTail,
    invalidateConversations,
    invalidateLiveChats,
    invalidateReplyInbox,
    invalidateAllSellerAgentReplies: invalidateReplyInbox && replyConversationIds.size === 0,
    detailConversationIds: sortedIds(detailConversationIds),
    replyConversationIds: sortedIds(replyConversationIds),
  }

  return hasPlanActions(plan) ? plan : null
}
