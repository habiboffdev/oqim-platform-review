import { expect, test } from '@playwright/test'

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

const proposal = {
  schema_version: 'commercial_action_proposal.v2',
  proposal_id: 'proposal-ui-smoke',
  workspace_id: 1,
  conversation_id: 42,
  customer_id: 77,
  action_type: 'promoter_outreach',
  lifecycle_state: 'waiting_approval',
  execution_mode: 'ask_seller_confirmation',
  risk_level: 'medium',
  requires_approval: true,
  executor_runtime: null,
  priority: 'medium',
  confidence: 0.78,
  reason_code: 'promoter_campaign_requires_seller_approval',
  source_refs: ['autocrm:customer:77', 'business_brain:catalog:ring'],
  payload: {
    message_goal: 'Reconnect with eligible customers using current offers',
    draft_brief: {
      message_goal: 'Reconnect with eligible customers using current offers',
    },
  },
  idempotency_key: 'promoter:1:campaign:42:77',
  correlation_id: 'ui:smoke',
  trace_id: 'trace:smoke',
}

const inbox = {
  schema_version: 'action_runtime_inbox.v1',
  workspace_id: 1,
  items: [proposal],
}

const policy = {
  schema_version: 'action_runtime_policy.v1',
  workspace_id: 1,
  enabled: false,
  confidence_threshold: 0.95,
  low_risk_allowlist: [],
  quiet_hours: {},
  escalation_destination: 'in_app',
  source_refs: ['action_runtime:default_policy'],
  correlation_id: 'action_runtime:default_policy',
}

const timeline = {
  schema_version: 'agent_run_timeline.v1',
  workspace_id: 1,
  run_id: 'seller-agent-run:ui-smoke',
  run: {
    schema_version: 'agent_run.v1',
    run_id: 'seller-agent-run:ui-smoke',
    workspace_id: 1,
    agent_id: 3,
    agent_kind: 'seller',
    trigger_ref: 'message:ui-smoke',
    conversation_id: 42,
    customer_id: 77,
    state: 'waiting_approval',
    permission_mode: 'ask_always',
    cache_key: null,
    correlation_id: 'ui:smoke',
    idempotency_key: 'idem:ui-smoke',
    source_refs: ['message:ui-smoke'],
    started_at: '2026-05-18T00:00:00Z',
    completed_at: null,
  },
  events: [
    {
      schema_version: 'agent_run_event.v1',
      event_id: 'ui-smoke-owner',
      run_id: 'seller-agent-run:ui-smoke',
      workspace_id: 1,
      sequence: 1,
      event_type: 'owner_progress.created',
      visibility: 'owner',
      owner_label: 'Brain va katalog tekshirildi',
      owner_detail: '2 ta dalil topildi.',
      tool_name: null,
      tool_state: null,
      action_proposal_id: null,
      source_refs: ['fact:ui-smoke'],
      payload: {},
      correlation_id: 'ui:smoke',
      idempotency_key: 'idem:ui-smoke:owner',
      created_at: '2026-05-18T00:00:01Z',
    },
    {
      schema_version: 'agent_run_event.v1',
      event_id: 'ui-smoke-internal',
      run_id: 'seller-agent-run:ui-smoke',
      workspace_id: 1,
      sequence: 2,
      event_type: 'tool.call.started',
      visibility: 'internal',
      owner_label: '',
      owner_detail: '',
      tool_name: 'catalog.search',
      tool_state: 'called',
      action_proposal_id: null,
      source_refs: [],
      payload: {},
      correlation_id: 'ui:smoke',
      idempotency_key: 'idem:ui-smoke:internal',
      created_at: '2026-05-18T00:00:02Z',
    },
    {
      schema_version: 'agent_run_event.v1',
      event_id: 'ui-smoke-customer-action',
      run_id: 'seller-agent-run:ui-smoke',
      workspace_id: 1,
      sequence: 3,
      event_type: 'customer_status.proposed',
      visibility: 'customer_action',
      owner_label: 'Mijozga holat xabari taklif qilindi',
      owner_detail: 'Bu yakuniy javob emas.',
      tool_name: null,
      tool_state: null,
      action_proposal_id: 'proposal-ui-smoke',
      source_refs: ['proposal:proposal-ui-smoke'],
      payload: {},
      correlation_id: 'ui:smoke',
      idempotency_key: 'idem:ui-smoke:customer-action',
      created_at: '2026-05-18T00:00:03Z',
    },
  ],
}

test.describe('Action Inbox', () => {
  test('renders proposal lifecycle from Action Runtime projections', async ({ page }) => {
    await page.route('**/api/auth/session', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(session),
      }),
    )
    await page.route('**/api/action-runtime/inbox', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(inbox),
      }),
    )
    await page.route('**/api/action-runtime/policy', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(policy),
      }),
    )
    await page.route('**/api/action-runtime/proposals/proposal-ui-smoke/timeline', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(timeline),
      }),
    )

    await page.goto('/actions')

    await expect(page.getByText('Amallar').first()).toBeVisible()
    await expect(page.getByText('Qayta jalb qilish').first()).toBeVisible()
    await expect(page.getByText('Reconnect with eligible customers using current offers').first()).toBeVisible()
    await expect(page.getByText('Jarayon')).toBeVisible()
    await expect(page.getByText('Brain va katalog tekshirildi')).toBeVisible()
    await expect(page.getByText('Mijozga holat xabari taklif qilindi')).toBeVisible()
    await expect(page.getByText('tool.call.started')).toHaveCount(0)
    await expect(page.getByText('catalog.search')).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Tasdiqlab bajarish' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Rad etish' })).toBeVisible()
  })
})
