import { describe, expect, it } from 'vitest'

import { buildProjectionSyncPlan } from './sync-projections'

describe('sync projection planning', () => {
  it('returns null for payloads without projection arrays', () => {
    expect(buildProjectionSyncPlan(undefined, 12)).toBeNull()
    expect(buildProjectionSyncPlan({ name: 'messages' }, 12)).toBeNull()
    expect(buildProjectionSyncPlan([], 12)).toBeNull()
  })

  it('plans bounded message-tail reconciliation from the first message projection', () => {
    expect(buildProjectionSyncPlan([
      { name: 'messages', mode: 'delta', conversation_id: 8, after_conversation_seq: 44 },
      { name: 'messages', mode: 'delta', conversation_id: 9, after_conversation_seq: 55 },
      { name: 'media', conversation_id: 10 },
    ])).toEqual({
      messageTail: { conversationId: 8, afterConversationSeq: 44 },
      invalidateConversations: true,
      invalidateLiveChats: true,
      invalidateReplyInbox: false,
      invalidateAllSellerAgentReplies: false,
      detailConversationIds: [8, 9, 10],
      replyConversationIds: [],
    })
  })

  it('uses scoped conversation id when projection rows omit scope', () => {
    expect(buildProjectionSyncPlan([
      { name: 'messages', mode: 'reset' },
      { name: 'conversation_state' },
      { name: 'seller_agent_replies' },
    ], 33)).toEqual({
      messageTail: { conversationId: 33, afterConversationSeq: undefined },
      invalidateConversations: true,
      invalidateLiveChats: true,
      invalidateReplyInbox: true,
      invalidateAllSellerAgentReplies: false,
      detailConversationIds: [33],
      replyConversationIds: [33],
    })
  })

  it('plans message-tail reconciliation for media projection changes', () => {
    expect(buildProjectionSyncPlan([
      { name: 'media', mode: 'delta', conversation_id: 8, after_conversation_seq: 44 },
    ])).toEqual({
      messageTail: { conversationId: 8, afterConversationSeq: 44 },
      invalidateConversations: true,
      invalidateLiveChats: true,
      invalidateReplyInbox: false,
      invalidateAllSellerAgentReplies: false,
      detailConversationIds: [8],
      replyConversationIds: [],
    })
  })

  it('plans global invalidations without inventing conversation scope', () => {
    expect(buildProjectionSyncPlan([
      { name: 'read_state' },
      { name: 'conversations' },
      { name: 'seller_agent_replies' },
    ])).toEqual({
      messageTail: undefined,
      invalidateConversations: true,
      invalidateLiveChats: true,
      invalidateReplyInbox: true,
      invalidateAllSellerAgentReplies: true,
      detailConversationIds: [],
      replyConversationIds: [],
    })
  })

  it('ignores malformed and unknown projections', () => {
    expect(buildProjectionSyncPlan([
      null,
      'bad',
      { name: 'unknown', conversation_id: 4 },
      { name: 'legacy_reply_projection', conversation_id: 4 },
      { name: 'messages', conversation_id: -1 },
    ])).toBeNull()
  })
})
