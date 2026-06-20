import { expect, test } from '@playwright/test'
import {
  authenticateSeededSession,
  openProtectedRoute,
  smokeCredentialsFromEnv,
} from './helpers/authenticated-session'

const credentials = smokeCredentialsFromEnv()
const telegramUserId = process.env.OQIM_SMOKE_TELEGRAM_USER_ID
const sidecarKey = process.env.OQIM_SMOKE_SIDECAR_KEY

test.describe('Fake Telegram runtime smoke', () => {
  test.skip(
    !credentials || !telegramUserId || !sidecarKey,
    'Fake Telegram fixture env vars are required. Run `oqim test fake-telegram-smoke`.',
  )

  test('GramJS-shaped webhook creates canonical conversation visible in browser', async ({ page }) => {
    const health = await page.request.get('http://localhost:8001/health/detailed')
    expect(health.ok(), 'backend detailed health should be reachable').toBeTruthy()

    await authenticateSeededSession(page, credentials!)
    await openProtectedRoute(page, '/conversations')
    await expect(page.getByRole('link', { name: /suhbatlar/i })).toBeVisible()

    const csrfCookie = (await page.context().cookies()).find((cookie) => cookie.name === 'oqim_csrf')
    const messageId = Math.floor(Date.now() / 1000)
    const fakeText = `Fake Telegram inbound ${messageId}: iPhone 15 Pro bormi?`
    const webhook = await page.request.post('/api/webhook/telegram', {
      data: {
        sellerUserId: telegramUserId,
        chatId: `4101${messageId}`,
        senderId: `7001${messageId}`,
        senderName: 'Fake Telegram Buyer',
        messageId,
        text: fakeText,
        date: messageId,
        isOutgoing: false,
      },
      headers: {
        'X-Sidecar-Key': sidecarKey!,
        'x-csrf-token': csrfCookie?.value ?? '',
      },
    })
    const webhookBody = await webhook.json()
    expect(
      webhook.ok(),
      `fake Telegram webhook should append to EventSpine: ${webhook.status()} ${JSON.stringify(webhookBody)}`,
    ).toBeTruthy()
    expect(webhookBody.source_of_truth).toBe('event_spine')

    let conversationId: number | null = null
    let latestProjection = ''
    for (let attempt = 0; attempt < 80; attempt += 1) {
      const response = await page.request.get('/api/conversations?limit=50')
      expect(response.ok(), '/api/conversations should stay available').toBeTruthy()
      const body = await response.json()
      latestProjection = JSON.stringify(body.items.slice(0, 5))
      const found = body.items.find((item: { id: number; last_message_text?: string }) =>
        item.last_message_text === fakeText,
      )
      if (found) {
        conversationId = found.id
        break
      }
      await page.waitForTimeout(250)
    }
    expect(
      conversationId,
      `EventSpine persist consumer should project fake inbound. Latest projection: ${latestProjection}`,
    ).not.toBeNull()

    await openProtectedRoute(page, `/conversations/${conversationId}`)
    await expect(page.getByText('Fake Telegram Buyer').first()).toBeVisible()
    await expect(page.getByTestId('virtuoso-item-list').getByText(fakeText)).toBeVisible()
    await expect(page.getByPlaceholder(/xabar yozing/i)).toBeVisible()
    await expect(page.getByText(/xizmat vaqtincha ishlamayapti/i)).toHaveCount(0)
  })
})
