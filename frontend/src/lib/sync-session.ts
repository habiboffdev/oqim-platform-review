import type { Message, PaginatedMessages } from './types'

export interface ConversationSyncCursor {
  conversationSeq: number
  conversationRevision?: number
}

export interface SessionResumePayload {
  type: 'session.resume'
  last_sequence: number
  conversation_id?: number
  last_seen_conversation_seq?: number
  last_seen_conversation_revision?: number
}

const STORAGE_KEY = 'oqim:sync-session:v1'
const conversationCursors = new Map<number, ConversationSyncCursor>()
let loadedStoredCursors = false

function toFiniteNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function getSessionStorage(): Storage | undefined {
  if (typeof window === 'undefined') return undefined
  try {
    return window.sessionStorage
  } catch {
    return undefined
  }
}

function loadStoredCursors() {
  if (loadedStoredCursors) return
  loadedStoredCursors = true

  const storage = getSessionStorage()
  if (!storage) return

  try {
    const raw = storage.getItem(STORAGE_KEY)
    if (!raw) return
    const parsed = JSON.parse(raw) as Record<string, unknown>
    for (const [rawConversationId, rawCursor] of Object.entries(parsed)) {
      const conversationId = Number(rawConversationId)
      if (!Number.isInteger(conversationId) || conversationId <= 0) continue
      if (!rawCursor || typeof rawCursor !== 'object') continue

      const cursor = rawCursor as Record<string, unknown>
      const conversationSeq = toFiniteNumber(cursor.conversationSeq)
      const conversationRevision = toFiniteNumber(cursor.conversationRevision)
      if (conversationSeq === undefined) continue

      conversationCursors.set(conversationId, {
        conversationSeq,
        conversationRevision,
      })
    }
  } catch {
    storage.removeItem(STORAGE_KEY)
  }
}

function persistStoredCursors() {
  const storage = getSessionStorage()
  if (!storage) return

  try {
    if (conversationCursors.size === 0) {
      storage.removeItem(STORAGE_KEY)
      return
    }
    const serialized: Record<string, ConversationSyncCursor> = {}
    for (const [conversationId, cursor] of conversationCursors.entries()) {
      serialized[String(conversationId)] = cursor
    }
    storage.setItem(STORAGE_KEY, JSON.stringify(serialized))
  } catch {
    // Session recovery is best-effort; canonical API cursors will repopulate it.
  }
}

export function getConversationSyncCursor(
  conversationId: number | undefined,
): ConversationSyncCursor | undefined {
  if (!conversationId) return undefined
  loadStoredCursors()
  return conversationCursors.get(conversationId)
}

export function buildSessionResumePayload(
  conversationId: number | undefined,
  lastSequence: number,
): SessionResumePayload {
  const payload: SessionResumePayload = {
    type: 'session.resume',
    last_sequence: lastSequence,
  }

  if (!conversationId) return payload

  payload.conversation_id = conversationId
  const cursor = getConversationSyncCursor(conversationId)
  if (!cursor) return payload

  payload.last_seen_conversation_seq = cursor.conversationSeq
  if (typeof cursor.conversationRevision === 'number') {
    payload.last_seen_conversation_revision = cursor.conversationRevision
  }
  return payload
}

export function recordConversationSyncCursor(
  conversationId: number | undefined,
  cursor: {
    conversationSeq?: number | null
    conversationRevision?: number | null
  },
) {
  if (!conversationId) return
  loadStoredCursors()

  const seq = toFiniteNumber(cursor.conversationSeq)
  const revision = toFiniteNumber(cursor.conversationRevision)
  const previous = conversationCursors.get(conversationId)

  if (seq === undefined) {
    if (previous && revision !== undefined) {
      conversationCursors.set(conversationId, {
        ...previous,
        conversationRevision: Math.max(previous.conversationRevision ?? 0, revision),
      })
      persistStoredCursors()
    }
    return
  }

  if (previous && previous.conversationSeq > seq) return

  const nextRevision = previous && previous.conversationSeq === seq && revision !== undefined
    ? Math.max(previous.conversationRevision ?? 0, revision)
    : revision ?? previous?.conversationRevision

  conversationCursors.set(conversationId, {
    conversationSeq: seq,
    conversationRevision: nextRevision,
  })
  persistStoredCursors()
}

export function recordPaginatedMessagesCursor(
  conversationId: number | undefined,
  page: PaginatedMessages | undefined,
) {
  if (!page) return
  recordConversationSyncCursor(conversationId, {
    conversationSeq: page.latest_conversation_seq ?? undefined,
    conversationRevision: page.latest_conversation_revision ?? undefined,
  })
}

export function recordMessageCursor(
  conversationId: number | undefined,
  message: Pick<Message, 'conversation_seq' | 'conversation_revision'>,
) {
  recordConversationSyncCursor(conversationId, {
    conversationSeq: message.conversation_seq,
    conversationRevision: message.conversation_revision,
  })
}

export function resetSyncSessionCursorsForTests(
  options: { clearStorage?: boolean } = {},
) {
  conversationCursors.clear()
  loadedStoredCursors = false
  if (options.clearStorage !== false) {
    getSessionStorage()?.removeItem(STORAGE_KEY)
  }
}
