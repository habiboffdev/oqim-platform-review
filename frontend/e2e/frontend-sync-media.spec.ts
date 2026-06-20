import { expect, test, type Page, type Route } from '@playwright/test'

type TailMode = 'media-initial' | 'media-unavailable' | 'state-initial' | 'state-updated'

const conversation = {
  id: 38,
  customer_id: 38,
  customer_name: 'Media Smoke Customer',
  channel: 'telegram_dm',
  telegram_chat_id: 70038,
  pipeline_stage: 'new',
  needs_attention: false,
  last_message_at: '2026-04-30T09:00:00Z',
  unread_count: 0,
  latest_conversation_seq: 10,
  latest_conversation_revision: 1,
  created_at: '2026-04-30T08:00:00Z',
  last_message_text: '[photo]',
}

const initialMessage = {
  id: 501,
  conversation_id: 38,
  sender_type: 'customer',
  content: '[photo]',
  channel: 'telegram_dm',
  telegram_message_id: 9501,
  is_read: true,
  media_type: 'photo',
  media_preview_url: '/api/media/38/501?thumb=true',
  media_full_url: '/api/media/38/501',
  media_metadata: { width: 640, height: 480 },
  media_runtime: {
    hydration_status: 'pending',
    asset_state: 'metadata_only',
    semantic_state: 'pending',
    action_state: 'pending',
  },
  created_at: '2026-04-30T09:00:00Z',
  conversation_seq: 10,
  conversation_revision: 1,
}

const unavailableMessage = {
  ...initialMessage,
  media_runtime: {
    hydration_status: 'unavailable',
    asset_state: 'unavailable',
    semantic_state: 'unavailable',
    action_state: 'failed',
  },
  conversation_seq: 11,
  conversation_revision: 2,
}

const stateInitialMessages = [
  {
    id: 601,
    conversation_id: 38,
    sender_type: 'customer',
    content: 'Message to be deleted',
    channel: 'telegram_dm',
    telegram_message_id: 9601,
    is_read: false,
    created_at: '2026-04-30T09:10:00Z',
    conversation_seq: 20,
    conversation_revision: 1,
  },
  {
    id: 602,
    conversation_id: 38,
    sender_type: 'customer',
    content: 'Original customer question',
    channel: 'telegram_dm',
    telegram_message_id: 9602,
    is_read: false,
    created_at: '2026-04-30T09:11:00Z',
    conversation_seq: 21,
    conversation_revision: 1,
  },
]

const stateUpdatedMessages = [
  {
    ...stateInitialMessages[1],
    content: 'Edited after reconnect',
    is_read: true,
    edited_at: '2026-04-30T09:12:00Z',
    conversation_seq: 22,
    conversation_revision: 2,
  },
]

const stateDraft = {
  id: 701,
  conversation_id: 38,
  trigger_message_id: 602,
  confidence_score: 0.92,
  status: 'draft',
  draft_content: 'Reconnect proof draft is ready.',
  chips: null,
  split_messages: null,
  is_auto_sent: false,
  customer_name: 'Media Smoke Customer',
  telegram_chat_id: 70038,
  trigger_message_text: 'Edited after reconnect',
  created_at: '2026-04-30T09:13:00Z',
}

const tinyPng = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=',
  'base64',
)

async function installMockWebSocket(page: Page) {
  await page.addInitScript(() => {
    type Handler = ((event: unknown) => void) | null

    class MockWebSocket extends EventTarget {
      static CONNECTING = 0
      static OPEN = 1
      static CLOSING = 2
      static CLOSED = 3

      url: string
      readyState = MockWebSocket.CONNECTING
      onopen: Handler = null
      onmessage: Handler = null
      onclose: Handler = null
      onerror: Handler = null

      constructor(url: string) {
        super()
        this.url = url
        ;(window as any).__oqimWs.sockets.push(this)
        setTimeout(() => {
          this.readyState = MockWebSocket.OPEN
          this.onopen?.(new Event('open'))
        }, 0)
      }

      send(raw: string) {
        try {
          ;(window as any).__oqimWs.sent.push(JSON.parse(raw))
        } catch {
          ;(window as any).__oqimWs.sent.push(raw)
        }
      }

      close() {
        this.readyState = MockWebSocket.CLOSED
        this.onclose?.({ code: 1006 })
      }

      serverMessage(payload: Record<string, unknown>) {
        this.onmessage?.({ data: JSON.stringify(payload) })
      }
    }

    ;(window as any).__oqimWs = {
      sockets: [] as MockWebSocket[],
      sent: [] as unknown[],
      latest() {
        return this.sockets[this.sockets.length - 1]
      },
      closeLatest() {
        this.latest()?.close()
      },
      emitLatest(payload: Record<string, unknown>) {
        this.latest()?.serverMessage(payload)
      },
    }
    ;(window as any).WebSocket = MockWebSocket
  })
}

function currentConversation(tailMode: TailMode) {
  const stateMode = tailMode === 'state-initial' || tailMode === 'state-updated'
  const updated = tailMode === 'media-unavailable' || tailMode === 'state-updated'
  return {
    ...conversation,
    unread_count: tailMode === 'state-initial' ? 3 : 0,
    last_message_text: stateMode
      ? (updated ? 'Edited after reconnect' : 'Original customer question')
      : '[photo]',
    latest_conversation_seq: stateMode
      ? (updated ? 22 : 21)
      : (updated ? 11 : 10),
    latest_conversation_revision: updated ? 2 : 1,
    has_pending_draft: tailMode === 'state-updated',
  }
}

function currentMessages(tailMode: TailMode) {
  if (tailMode === 'state-initial') {
    return {
      items: stateInitialMessages,
      has_older: false,
      latest_conversation_seq: 21,
      latest_conversation_revision: 1,
    }
  }
  if (tailMode === 'state-updated') {
    return {
      items: stateUpdatedMessages,
      has_older: false,
      latest_conversation_seq: 22,
      latest_conversation_revision: 2,
    }
  }

  const message = tailMode === 'media-initial' ? initialMessage : unavailableMessage
  return {
    items: [message],
    has_older: false,
    latest_conversation_seq: message.conversation_seq,
    latest_conversation_revision: message.conversation_revision,
  }
}

async function installApiFixture(page: Page, tailMode: { current: TailMode }) {
  await page.route('**/api/**', async (route: Route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname

    if (path.startsWith('/api/media/')) {
      await route.fulfill({
        status: 200,
        contentType: 'image/png',
        body: tinyPng,
      })
      return
    }

    if (path === '/api/auth/me') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 1,
          phone_number: '+998901234567',
          name: 'Smoke Workspace',
          telegram_connected: true,
          onboarding_completed: true,
          created_at: '2026-04-30T08:00:00Z',
          updated_at: '2026-04-30T08:00:00Z',
        }),
      })
      return
    }

    if (path === '/api/conversations') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: [currentConversation(tailMode.current)], next_cursor: null }),
      })
      return
    }

    if (path === '/api/conversations/38') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(currentConversation(tailMode.current)),
      })
      return
    }

    if (path === '/api/conversations/38/messages') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(currentMessages(tailMode.current)),
      })
      return
    }

    if (path === '/api/conversations/38/hydrate') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true }),
      })
      return
    }

    if (path === '/api/conversations/38/ai-replies') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(tailMode.current === 'state-updated' ? [stateDraft] : []),
      })
      return
    }

    if (path === '/api/ai-replies') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      })
      return
    }

    if (path === '/api/onboarding/progress' || path === '/api/telegram/ingestion-progress') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ phase: 'done', completed: true, voice_profile_ready: true }),
      })
      return
    }

    if (path === '/api/telegram/status') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ connected: true, state: 'connected' }),
      })
      return
    }

    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({}),
    })
  })
}

test.describe('frontend sync browser smoke', () => {
  test('media UI state survives websocket reconnect from canonical projection state', async ({ page }) => {
    const tailMode = { current: 'media-initial' as TailMode }
    await installMockWebSocket(page)
    await installApiFixture(page, tailMode)

    await page.goto('/conversations/38')

    await expect(page.getByText('Media Smoke Customer').first()).toBeVisible()
    await expect(page.getByText("Media tayyorlanmoqda")).toBeVisible()
    await expect(page.getByText("Media mavjud emas")).toHaveCount(0)

    await page.evaluate(() => (window as any).__oqimWs.closeLatest())
    await expect.poll(
      () => page.evaluate(() => (window as any).__oqimWs.sockets.length),
    ).toBeGreaterThan(1)
    await expect.poll(
      () => page.evaluate(() => (window as any).__oqimWs.sent),
    ).toContainEqual(expect.objectContaining({
      type: 'session.resume',
      conversation_id: 38,
    }))
    await expect.poll(
      () => page.evaluate(() => (window as any).__oqimWs.sent),
    ).toContainEqual({ type: 'chat_opened', conversation_id: 38 })

    tailMode.current = 'media-unavailable'
    await page.evaluate(() => {
      ;(window as any).__oqimWs.emitLatest({
        type: 'session.delta',
        sequence_id: 44,
        data: {
          action: 'refresh_scoped_runtime_delta',
          conversation_id: 38,
          latest_conversation_seq: 11,
          latest_conversation_revision: 2,
          projections: [
            {
              name: 'media',
              mode: 'delta',
              conversation_id: 38,
              after_conversation_seq: 10,
            },
          ],
          conversation_state: {
            last_message_text: '[photo]',
            last_message_at: '2026-04-30T09:01:00Z',
            latest_conversation_seq: 11,
            latest_conversation_revision: 2,
            unread_count: 0,
          },
        },
      })
    })

    await expect(page.getByText("Media mavjud emas")).toBeVisible()
    await expect(page.getByText("Media tayyorlanmoqda")).toHaveCount(0)
  })

  test('read, edit, delete, and draft UI converge after reconnect projection reset', async ({ page }) => {
    const tailMode = { current: 'state-initial' as TailMode }
    await page.setViewportSize({ width: 1440, height: 900 })
    await installMockWebSocket(page)
    await installApiFixture(page, tailMode)

    await page.goto('/conversations/38')

    const conversationRow = page.getByRole('link', { name: /Media Smoke Customer/ })
    await expect(conversationRow).toContainText('Original customer question')
    await expect(conversationRow).toContainText('3')
    await expect(page.getByText('Message to be deleted')).toBeVisible()
    await expect(page.getByText('Original customer question').last()).toBeVisible()
    await expect(page.getByText('Reconnect proof draft is ready.')).toHaveCount(0)

    await page.evaluate(() => (window as any).__oqimWs.closeLatest())
    await expect.poll(
      () => page.evaluate(() => (window as any).__oqimWs.sockets.length),
    ).toBeGreaterThan(1)

    tailMode.current = 'state-updated'
    await page.evaluate(() => {
      ;(window as any).__oqimWs.emitLatest({
        type: 'session.reset_required',
        sequence_id: 55,
        data: {
          action: 'refresh_scoped_runtime',
          conversation_id: 38,
          latest_conversation_seq: 22,
          latest_conversation_revision: 2,
          projections: [
            { name: 'messages', mode: 'reset', conversation_id: 38 },
            { name: 'read_state', mode: 'reset', conversation_id: 38 },
            { name: 'conversation_state', mode: 'reset', conversation_id: 38 },
            { name: 'drafts', mode: 'reset', conversation_id: 38 },
          ],
          conversation_state: {
            last_message_text: 'Edited after reconnect',
            last_message_at: '2026-04-30T09:12:00Z',
            latest_conversation_seq: 22,
            latest_conversation_revision: 2,
            unread_count: 0,
          },
        },
      })
    })

    await expect(conversationRow).toContainText('Edited after reconnect')
    await expect(conversationRow).not.toContainText('Original customer question')
    await expect(conversationRow).not.toContainText('3')
    await expect(page.getByText('Edited after reconnect').last()).toBeVisible()
    await expect(page.getByText('Original customer question')).toHaveCount(0)
    await expect(page.getByText('Message to be deleted')).toHaveCount(0)
    await expect(page.getByText('Reconnect proof draft is ready.')).toBeVisible()
  })
})
