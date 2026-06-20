import { describe, expect, it } from 'vitest'

import { activeConversationIdFromRoute } from './active-conversation-route'

describe('activeConversationIdFromRoute', () => {
  it('uses the browser pathname before stale route params', () => {
    expect(activeConversationIdFromRoute({
      pathname: '/conversations/38',
      param: '12',
    })).toBe(38)
  })

  it('falls back to route params outside a detail URL', () => {
    expect(activeConversationIdFromRoute({
      pathname: '/conversations',
      param: '12',
    })).toBe(12)
  })

  it('ignores invalid IDs', () => {
    expect(activeConversationIdFromRoute({
      pathname: '/conversations/nope',
      param: '0',
    })).toBeUndefined()
  })
})
