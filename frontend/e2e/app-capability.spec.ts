import { expect, test } from '@playwright/test'
import {
  authenticateSeededSession,
  openProtectedRoute,
  smokeCredentialsFromEnv,
} from './helpers/authenticated-session'

const credentials = smokeCredentialsFromEnv()
const conversationId = process.env.OQIM_SMOKE_CONVERSATION_ID
const customerName = process.env.OQIM_SMOKE_CUSTOMER_NAME ?? 'Smoke Customer'
const firstMessage = process.env.OQIM_SMOKE_FIRST_MESSAGE ?? 'Smoke salom, iPhone bormi?'
const latestMessage = process.env.OQIM_SMOKE_LATEST_MESSAGE ?? 'Smoke ha, 128GB bor.'

test.describe('App capability smoke', () => {
  test.skip(
    !credentials || !conversationId,
    'OQIM smoke fixture env vars are required. Run `oqim test app-smoke`.',
  )

  test('seller can open seeded conversation through an authenticated browser session', async ({ page }) => {
    const health = await page.request.get('http://localhost:8001/health/detailed')
    expect(health.ok(), 'backend detailed health should be reachable').toBeTruthy()
    const healthBody = await health.json()
    expect(healthBody.database, 'database should be connected').toBe('connected')
    expect(healthBody.redis, 'redis should be connected').toBe('connected')

    await authenticateSeededSession(page, credentials!)
    await openProtectedRoute(page, '/conversations')
    await expect(page.getByRole('link', { name: /suhbatlar/i })).toBeVisible()

    const conversationsResponse = await page.request.get('/api/conversations?limit=50')
    expect(conversationsResponse.ok(), '/api/conversations should return 200').toBeTruthy()
    const conversations = await conversationsResponse.json()
    expect(
      conversations.items.some((item: { id: number; last_message_text?: string }) =>
        item.id === Number(conversationId) && item.last_message_text === latestMessage,
      ),
      'seeded conversation should be present with canonical latest preview',
    ).toBeTruthy()

    await openProtectedRoute(page, `/conversations/${conversationId}`)

    await expect(page.getByPlaceholder(/suhbatlarni qidirish/i)).toBeVisible()
    await expect(page.getByText(customerName).first()).toBeVisible()
    await expect(page.getByText(firstMessage)).toBeVisible()
    await expect(page.getByText(latestMessage).last()).toBeVisible()
    await expect(page.getByPlaceholder(/xabar yozing/i)).toBeVisible()
    await expect(page.getByText(/xizmat vaqtincha ishlamayapti/i)).toHaveCount(0)
  })
})
