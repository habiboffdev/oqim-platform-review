import { expect, test, type Page, type Route } from '@playwright/test'

const session = {
  schema_version: 'auth_session_projection.v1',
  authenticated: true,
  workspace: {
    id: 1,
    phone_number: '+998901234567',
    name: 'OQIM Demo',
    type: 'seller',
    monthly_revenue_band: null,
    subscription_tier: 'pilot',
    telegram_connected: true,
    onboarding_completed: true,
    created_at: '2026-05-16T00:00:00Z',
    updated_at: '2026-05-16T00:00:00Z',
  },
  user: {
    id: 1,
    name: 'Demo Seller',
    phone_number: '+998901234567',
    is_founder: false,
  },
  platform_role: 'business_owner',
  is_founder: false,
  onboarding_completed: true,
  integrations: [],
}

const facts = {
  items: [
    {
      schema_version: 'business_brain_fact_read_model.v1',
      fact_id: 'knowledge:shipping-conflict',
      workspace_id: 1,
      fact_type: 'knowledge_fact',
      entity_ref: 'business:support:shipping',
      status: 'conflict',
      confidence: 0.72,
      value: {
        topic: 'Yetkazish',
        answer: 'Yetkazish muddati ikki xil aytilgan.',
      },
      source_refs: ['business_source:telegram:shipping'],
      freshness: { state: 'fresh' },
      risk_tier: 'medium',
      valid_from: '2026-05-16T00:00:00Z',
    },
    {
      schema_version: 'business_brain_fact_read_model.v1',
      fact_id: 'knowledge:shipping-copy',
      workspace_id: 1,
      fact_type: 'knowledge_fact',
      entity_ref: 'business:support:shipping-copy',
      status: 'proposed',
      confidence: 0.7,
      value: {
        topic: 'Yetkazish muddati',
        answer: 'Toshkent ichida bir kunda yetkaziladi.',
      },
      source_refs: ['business_source:telegram:shipping-copy'],
      freshness: { state: 'fresh' },
      risk_tier: 'medium',
      valid_from: '2026-05-16T00:00:00Z',
    },
    {
      schema_version: 'business_brain_fact_read_model.v1',
      fact_id: 'knowledge:no-source',
      workspace_id: 1,
      fact_type: 'knowledge_fact',
      entity_ref: 'business:support:no-source',
      status: 'degraded',
      confidence: 0.6,
      value: {
        topic: 'Kafolat',
        answer: 'Kafolat sharti noaniq.',
      },
      source_refs: [],
      freshness: { state: 'fresh' },
      risk_tier: 'medium',
      valid_from: '2026-05-16T00:00:00Z',
    },
  ],
}

const sources = {
  schema_version: 'onboarding_source_learning.v1',
  status: 'learned',
  percent: 100,
  summary: {
    total: 0,
    learning: 0,
    learned: 0,
    needs_review: 0,
    missing: 0,
    conflict: 0,
    retrying: 0,
    failed: 0,
  },
  sources: [],
}

function fulfill(route: Route, body: unknown) {
  return route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

async function mockBrainApis(page: Page) {
  await page.route('**/api/**', (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/auth/session') return fulfill(route, session)
    if (path === '/api/business-brain/facts') return fulfill(route, facts)
    if (path === '/api/business-brain/sources') return fulfill(route, sources)
    if (path === '/api/catalog-intelligence/workspace') {
      return fulfill(route, {
        schema_version: 'catalog_workspace_projection.v1',
        workspace_id: 1,
        products: [],
      })
    }
    if (path === '/api/action-runtime/inbox') {
      return fulfill(route, {
        schema_version: 'action_runtime_inbox.v1',
        workspace_id: 1,
        items: [],
      })
    }
    if (path === '/api/ai-replies') return fulfill(route, [])
    return fulfill(route, {})
  })
}

test('Brain knowledge management shows owner, source, and repair action without raw internals', async ({ page }) => {
  await mockBrainApis(page)

  await page.goto('/brain?tab=knowledge')

  await expect(page.getByRole('heading', { name: 'Bilim markazi' })).toBeVisible()
  await expect(page.getByText('Bilim bazasi')).toBeVisible()
  await expect(page.getByText('To‘ldirish kerak')).toBeVisible()
  await expect(page.getByText(/Bo‘lim: Bilim bazasi/).first()).toBeVisible()
  await expect(page.getByText(/Manba: O‘qilgan manba/).first()).toBeVisible()
  await expect(page.getByText(/Kerak: Konfliktni birlashtirish/)).toBeVisible()
  await expect(page.getByText(/Manba: Dalil yo‘q/)).toBeVisible()
  await expect(page.getByText(/Kerak: Dalil qo‘shish/)).toBeVisible()
  await expect(page.getByText('Yetkazish', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('Kafolat', { exact: true }).first()).toBeVisible()

  await expect(page.getByText(/business_source:telegram/)).toHaveCount(0)
  await expect(page.getByText(/Company Brain/)).toHaveCount(0)
  await expect(page.getByText(/OQIM Intelligence/)).toHaveCount(0)
})
