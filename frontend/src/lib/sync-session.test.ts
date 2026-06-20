import { beforeEach, describe, expect, it } from 'vitest'

import {
  buildSessionResumePayload,
  getConversationSyncCursor,
  recordConversationSyncCursor,
  resetSyncSessionCursorsForTests,
} from './sync-session'

describe('sync-session cursors', () => {
  beforeEach(() => {
    resetSyncSessionCursorsForTests()
  })

  it('persists canonical conversation cursors across module reads', async () => {
    recordConversationSyncCursor(38, {
      conversationSeq: 10,
      conversationRevision: 12,
    })
    resetSyncSessionCursorsForTests({ clearStorage: false })

    expect(getConversationSyncCursor(38)).toEqual({
      conversationSeq: 10,
      conversationRevision: 12,
    })
  })

  it('ignores malformed stored cursor payloads', async () => {
    window.sessionStorage.setItem(
      'oqim:sync-session:v1',
      JSON.stringify({
        38: { conversationSeq: '999', conversationRevision: 999 },
        39: { conversationRevision: 7 },
      }),
    )
    resetSyncSessionCursorsForTests({ clearStorage: false })

    expect(getConversationSyncCursor(38)).toBeUndefined()
    expect(getConversationSyncCursor(39)).toBeUndefined()
  })

  it('does not move backwards when an older canonical cursor arrives', () => {
    recordConversationSyncCursor(38, {
      conversationSeq: 10,
      conversationRevision: 10,
    })
    recordConversationSyncCursor(38, {
      conversationSeq: 9,
      conversationRevision: 99,
    })

    expect(getConversationSyncCursor(38)).toEqual({
      conversationSeq: 10,
      conversationRevision: 10,
    })
  })

  it('builds reconnect payloads from canonical sync cursors only', () => {
    recordConversationSyncCursor(38, {
      conversationSeq: 12,
      conversationRevision: 14,
    })

    expect(buildSessionResumePayload(38, 99)).toEqual({
      type: 'session.resume',
      last_sequence: 99,
      conversation_id: 38,
      last_seen_conversation_seq: 12,
      last_seen_conversation_revision: 14,
    })
  })

  it('builds scoped reconnect payloads without guessed cursors', () => {
    expect(buildSessionResumePayload(39, 7)).toEqual({
      type: 'session.resume',
      last_sequence: 7,
      conversation_id: 39,
    })
  })

  it('builds workspace-level reconnect payloads without conversation scope', () => {
    expect(buildSessionResumePayload(undefined, 3)).toEqual({
      type: 'session.resume',
      last_sequence: 3,
    })
  })
})
