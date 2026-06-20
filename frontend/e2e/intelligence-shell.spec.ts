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
    created_at: '2026-05-06T00:00:00Z',
    updated_at: '2026-05-06T00:00:00Z',
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
  integrations: [
    {
      provider: 'telegram_personal',
      state: 'connected',
      identity_linked: true,
      durable_connected: true,
      needs_reconnect: false,
      source: 'workspace_projection',
      external_id: 'telegram:demo',
      live_state: 'not_checked',
    },
  ],
}

const crmDashboard = {
  schema_version: 'bi_analytics_dashboard.v1',
  workspace_id: 1,
  summary: {
    customer_count: 128,
    opportunity_count: 41,
    reply_needed_count: 9,
    orders_count: 18,
    stalled_opportunity_count: 4,
  },
  breakdowns: {
    products: [{ key: 'Silver ring', orders: 8, opportunities: 13 }],
    channels: [{ key: 'Telegram', orders: 18, opportunities: 41 }],
  },
  insights: [
    {
      schema_version: 'bi_insight.v1',
      workspace_id: 1,
      insight_id: 'insight:demo',
      insight_type: 'stalled_opportunity_risk',
      answer: 'Four warm customers need a sales follow-up.',
      metrics: {},
      records: [],
      source_refs: ['autocrm:opportunity:41'],
      confidence: 0.86,
      freshness: 'projection_current',
      suggested_action_proposal_ids: [],
      degraded_reasons: [],
    },
  ],
  source_refs: ['projection:crm_intel'],
  freshness: 'projection_current',
  degraded_reasons: [],
}

const promoterPolicy = {
  schema_version: 'promoter_policy.v1',
  workspace_id: 1,
  enabled: true,
  approved: true,
  allowed_stages: ['cold', 'stalled', 'past_customer'],
  max_contacts_per_7d: 60,
  quiet_hours: {},
  source_refs: ['policy:promoter'],
  correlation_id: 'policy:promoter',
}

const actionPolicy = {
  schema_version: 'action_runtime_policy.v1',
  workspace_id: 1,
  enabled: true,
  confidence_threshold: 0.88,
  low_risk_allowlist: ['send_followup'],
  quiet_hours: {},
  escalation_destination: 'in_app',
  source_refs: ['action_runtime:policy'],
  correlation_id: 'action_runtime:policy',
}

const actionProposal = {
  schema_version: 'commercial_action_proposal.v2',
  proposal_id: 'proposal:demo',
  workspace_id: 1,
  conversation_id: 42,
  customer_id: 77,
  action_type: 'sales_followup',
  lifecycle_state: 'waiting_approval',
  execution_mode: 'ask_seller_confirmation',
  risk_level: 'medium',
  requires_approval: true,
  executor_runtime: null,
  priority: 'medium',
  confidence: 0.82,
  reason_code: 'customer_went_cold_after_price',
  source_refs: ['autocrm:customer:77'],
  payload: { message_goal: 'Reconnect with warm buyer' },
  idempotency_key: 'demo:proposal',
}

const brainFacts = {
  items: [
    {
      schema_version: 'business_brain_fact_read_model.v1',
      fact_id: 'fact:voice:demo',
      workspace_id: 1,
      fact_type: 'voice_fact',
      entity_ref: 'workspace:voice',
      value: {
        title: 'Sotuvchi ovozi',
        summary: 'Qisqa, iliq va aniq javob beradi.',
      },
      confidence: 0.9,
      status: 'active',
      risk_tier: 'low',
      source_refs: ['conversation_pair:demo'],
      freshness: { state: 'fresh' },
      valid_from: '2026-05-09T00:00:00Z',
    },
    {
      schema_version: 'business_brain_fact_read_model.v1',
      fact_id: 'fact:source:csv',
      workspace_id: 1,
      fact_type: 'business_source_fact',
      entity_ref: 'workspace:source:brain:source:csv',
      value: {
        kind: 'file',
        label: 'price-list.csv',
        input: { file_name: 'price-list.csv', content_type: 'text/csv' },
        processing: { state: 'queued', source_unit_count: 2, source_media_count: 0 },
        text_preview: 'Atlas koylak 250000 UZS',
      },
      confidence: 1,
      status: 'active',
      risk_tier: 'low',
      source_refs: ['brain:source:csv'],
      freshness: { state: 'fresh' },
      valid_from: '2026-05-09T00:00:00Z',
    },
  ],
}

const catalogWorkspace = {
  schema_version: 'catalog_workspace_projection.v1',
  workspace_id: 1,
  products: [],
}

const brainSources = {
  schema_version: 'onboarding_source_learning.v1',
  status: 'needs_review',
  percent: 50,
  summary: {
    total: 1,
    learning: 0,
    learned: 0,
    needs_review: 1,
    missing: 0,
    conflict: 0,
    retrying: 0,
    failed: 0,
  },
  sources: [
    {
      source_ref: 'brain:source:csv',
      kind: 'file',
      label: 'price-list.csv',
      status: 'needs_review',
      raw_state: 'ok',
      source_unit_count: 2,
      source_media_count: 0,
      degraded_reasons: [],
      retryable: false,
      fact_id: 'fact:source:csv',
      entity_ref: 'workspace:source:brain:source:csv',
      source_refs: ['brain:source:csv'],
    },
  ],
}

const pipeline = {
  schema_version: 'crm_pipeline.v1',
  total: 0,
  stages: [
    { stage: 'new', count: 0, cards: [] },
    { stage: 'qualified', count: 0, cards: [] },
    { stage: 'negotiation', count: 0, cards: [] },
    { stage: 'proposal', count: 0, cards: [] },
    { stage: 'payment', count: 0, cards: [] },
    { stage: 'delivery', count: 0, cards: [] },
    { stage: 'waiting', count: 0, cards: [] },
    { stage: 'won', count: 0, cards: [] },
    { stage: 'lost', count: 0, cards: [] },
    { stage: 'manual_review', count: 0, cards: [] },
  ],
}

function fulfill(route: Route, body: unknown) {
  return route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

async function mockIntelligenceShellApis(page: Page) {
  const adminRequests: string[] = []
  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname

    if (path.startsWith('/api/admin/')) {
      adminRequests.push(path)
      return route.fulfill({
        status: 403,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Founder access required' }),
      })
    }

    if (path === '/api/auth/session') return fulfill(route, session)
    if (path === '/api/conversations/pipeline') return fulfill(route, pipeline)
    if (path === '/api/conversations') return fulfill(route, { items: [], next_cursor: null })
    if (path === '/api/ai-replies') return fulfill(route, [])
    if (path === '/api/business-brain/facts') return fulfill(route, brainFacts)
    if (path === '/api/business-brain/sources') return fulfill(route, brainSources)
    if (path === '/api/catalog-intelligence/workspace') return fulfill(route, catalogWorkspace)
    if (path === '/api/bi-promoter/analytics/dashboard') return fulfill(route, crmDashboard)
    if (path === '/api/bi-promoter/promoter/policy') return fulfill(route, promoterPolicy)
    if (path === '/api/action-runtime/inbox') {
      return fulfill(route, {
        schema_version: 'action_runtime_inbox.v1',
        workspace_id: 1,
        items: [actionProposal],
      })
    }
    if (path === '/api/action-runtime/policy') return fulfill(route, actionPolicy)

    return fulfill(route, {})
  })
  return { adminRequests }
}

test.describe('Intelligence shell', () => {
  test('shows the new five-module product architecture', async ({ page }) => {
    const { adminRequests } = await mockIntelligenceShellApis(page)
    await page.goto('/')
    await expect(page).toHaveURL(/\/conversations$/)
    await expect(page.getByText('Suhbatlar').first()).toBeVisible()
    await expect(page.getByRole('button', { name: /Pipeline/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /Suhbatlar/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /Javoblar/i })).toBeVisible()
    await expect(page.getByText('Javoblar').first()).toBeVisible()
    await expect(page.getByText('Bilim').first()).toBeVisible()
    await expect(page.getByText('Mijoz holati').first()).toBeVisible()
    await expect(page.getByText('Agentlar').first()).toBeVisible()
    await expect(page.getByText('Qoralamalar')).toHaveCount(0)
    await expect(page.getByText('CRM Intel')).toHaveCount(0)

    await page.goto('/brain?tab=voice')
    await expect(page.getByText('Bilim markazi').first()).toBeVisible()
    await expect(page.getByText('Sotuvchi ovozi').first()).toBeVisible()
    await expect(page.getByText('Salomlashish, aniqlashtirish, e’tirozga javob').first()).toBeVisible()

    await page.goto('/brain?tab=sources')
    await expect(page.getByText('Manba qo‘shish').first()).toBeVisible()
    const brainMain = page.locator('main').last()
    await brainMain.evaluate((node) => {
      node.scrollTop = node.scrollHeight
    })
    await expect(brainMain).toContainText('price-list.csv')
    await expect(brainMain).toContainText('Atlas koylak 250000 UZS')

    await page.goto('/intelligence')
    await expect(page.getByRole('heading', { name: 'Mijozlar holati' })).toBeVisible()
    await expect(page.getByText('Mijoz holati xaritasi')).toBeVisible()
    await page.getByRole('button', { name: 'Qayta yozish' }).click()
    await expect(page.getByText('Qayta yozish', { exact: true }).first()).toBeVisible()

    await page.goto('/agents')
    await expect(page.getByRole('heading', { name: 'Agentlar' })).toBeVisible()
    await expect(page.locator('[data-slot="card-title"]').filter({ hasText: 'Sotuvchi agent' })).toBeVisible()
    await expect(page.locator('[data-slot="card-title"]').filter({ hasText: 'AI sozlamalari' })).toBeVisible()

    await page.goto('/settings')
    await expect(page.getByRole('heading', { name: 'Ish joyi boshqaruvi' })).toBeVisible()

    await page.goto('/conversations?mode=replies')
    await expect(page).toHaveURL(/\/conversations\?mode=replies$/)
    await expect(page.getByRole('button', { name: /Javoblar/i })).toBeVisible()
    await expect(page.getByText("Kutayotgan javoblar yo'q")).toBeVisible()
    expect(adminRequests).toEqual([])
  })

  test('keeps deleted seller pages out of the active architecture', async ({ page }) => {
    await mockIntelligenceShellApis(page)

    for (const oldRoute of ['/customers', '/orders', '/tasks', '/bi-promoter', '/crm-intel', '/drafts']) {
      await page.goto(oldRoute)
      await expect(page).toHaveURL(new RegExp(`${oldRoute.replace('/', '\\/')}$`))
      await expect(page.getByText('Not Found')).toBeVisible()
      await expect(page.getByText('CRM Intel')).toHaveCount(0)
      await expect(page.getByText('Qoralamalar')).toHaveCount(0)
    }
  })
})
