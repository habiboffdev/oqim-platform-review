import { expect, test } from '@playwright/test'
import {
  authenticateSeededSession,
  openProtectedRoute,
  smokeCredentialsFromEnv,
} from './helpers/authenticated-session'

const credentials = smokeCredentialsFromEnv('OQIM_ADMIN_SMOKE')
const founderSession = {
  schema_version: 'auth_session_projection.v1',
  authenticated: true,
  workspace: {
    id: 1,
    phone_number: '+998901234567',
    name: 'OQIM Founder',
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
    name: 'Founder',
    phone_number: '+998901234567',
    is_founder: true,
  },
  platform_role: 'founder',
  is_founder: true,
  onboarding_completed: true,
  integrations: [],
}

const runtimeSignals = {
  schema_version: 'runtime_signals.v1',
  workspace_id: 1,
  period_days: 1,
  event_spine: {
    status: 'ok',
    error: null,
    publish_failures: 0,
    global_divergences: {},
    workspace_divergences: {},
    persist_shadow: {},
    persist_shadow_ready: true,
    persist_shadow_blockers: [],
  },
  seller_agent_reply_freshness: {
    replies_total: 3,
    expired_count: 0,
    suppressed_count: 0,
    freshness_loss_count: 0,
    freshness_loss_rate: 0,
    suppressed_reasons: {},
  },
  media: {
    ai_relevant_media_total: 0,
    hydrated_count: 0,
    pending_count: 0,
    deferred_count: 0,
    unavailable_count: 0,
    due_count: 0,
    leased_count: 0,
    stale_lease_count: 0,
    stuck_count: 0,
  },
  delivery: {
    active_count: 0,
    unknown_count: 0,
    failed_count: 0,
    retryable_count: 0,
    stale_unknown_count: 0,
  },
  conversation_hydration: {
    active_count: 0,
    queued_count: 0,
    running_count: 0,
    deferred_count: 0,
    failed_count: 0,
    stale_lease_count: 0,
    retryable_count: 0,
  },
  seller_agent_queue: {
    active_candidates: 0,
    open_candidates: 0,
    ready_candidates: 0,
    leased_candidates: 0,
    generating_candidates: 0,
    failed_candidates: 0,
    suppressed_candidates: 0,
    superseded_candidates: 0,
  },
  autopilot: {
    decisions_total: 0,
    allowed_count: 0,
    blocked_count: 0,
    scheduled_count: 0,
    sent_count: 0,
    delivery_failed_count: 0,
    delivery_unknown_count: 0,
    blocked_reasons: {},
  },
  action_runtime: {
    degraded_total: 0,
    degraded_by_action: {},
  },
  quotas: {
    seller_agent_max_inflight: 2,
    seller_agent_max_ready_claims_per_tick: 1,
    media_max_claims_per_workspace: 2,
    scheduled_send_max_claims_per_workspace: 2,
    universal_extraction_daily_count: 0,
    universal_extraction_daily_cap: 0,
    universal_extraction_exceeded: false,
  },
  usage_accounting: {
    daily_input_tokens: 1200,
    daily_output_tokens: 300,
    daily_total_tokens: 1500,
    daily_operation_count: 7,
    daily_estimated_cost_micros: 1110,
    by_operation: {
      seller_agent: 1100,
      retrieval_core: 400,
    },
    by_provider: {
      gemini: 1500,
    },
    by_operation_estimated_cost_micros: {
      seller_agent: 900,
      retrieval_core: 210,
    },
    by_provider_estimated_cost_micros: {
      gemini: 1110,
    },
    cost_policy: {
      gemini: { input: 300, output: 2500 },
    },
    daily_history: [
      { date: '2026-05-03', input_tokens: 0, output_tokens: 0, total_tokens: 0, operation_count: 0, estimated_cost_micros: 0 },
      { date: '2026-05-04', input_tokens: 120, output_tokens: 20, total_tokens: 140, operation_count: 1, estimated_cost_micros: 86 },
      { date: '2026-05-05', input_tokens: 300, output_tokens: 80, total_tokens: 380, operation_count: 2, estimated_cost_micros: 290 },
      { date: '2026-05-06', input_tokens: 500, output_tokens: 100, total_tokens: 600, operation_count: 3, estimated_cost_micros: 400 },
      { date: '2026-05-07', input_tokens: 400, output_tokens: 120, total_tokens: 520, operation_count: 3, estimated_cost_micros: 420 },
      { date: '2026-05-08', input_tokens: 600, output_tokens: 160, total_tokens: 760, operation_count: 4, estimated_cost_micros: 580 },
      { date: '2026-05-09', input_tokens: 700, output_tokens: 190, total_tokens: 890, operation_count: 5, estimated_cost_micros: 685 },
      { date: '2026-05-10', input_tokens: 800, output_tokens: 200, total_tokens: 1000, operation_count: 5, estimated_cost_micros: 740 },
      { date: '2026-05-11', input_tokens: 900, output_tokens: 240, total_tokens: 1140, operation_count: 6, estimated_cost_micros: 870 },
      { date: '2026-05-12', input_tokens: 1000, output_tokens: 260, total_tokens: 1260, operation_count: 6, estimated_cost_micros: 950 },
      { date: '2026-05-13', input_tokens: 1100, output_tokens: 270, total_tokens: 1370, operation_count: 7, estimated_cost_micros: 1005 },
      { date: '2026-05-14', input_tokens: 900, output_tokens: 200, total_tokens: 1100, operation_count: 5, estimated_cost_micros: 770 },
      { date: '2026-05-15', input_tokens: 1000, output_tokens: 260, total_tokens: 1260, operation_count: 6, estimated_cost_micros: 950 },
      { date: '2026-05-16', input_tokens: 1200, output_tokens: 300, total_tokens: 1500, operation_count: 7, estimated_cost_micros: 1110 },
    ],
  },
  slo: {
    status: 'ok',
    message_visible_under_1s_status: 'ok',
    message_visible_p95_ms: 420,
    message_visible_sample_count: 12,
    seller_agent_or_degraded_under_20s_status: 'ok',
    oldest_seller_agent_wait_seconds: null,
    media_hydration_lag_seconds: null,
    workspace_deadletter_length: 0,
    replay_drift_status: 'ok',
    replay_drift_count: 0,
  },
  dependencies: {
    status: 'ok',
    database: 'connected',
    redis: 'connected',
    errors: {},
  },
  worker_lifecycle: {
    status: 'ok',
    error: null,
    roles: {
      seller_agent_dispatch: {
        role: 'seller_agent_dispatch',
        lifecycle_model: 'supervised',
        proof_status: 'proved',
        active: true,
        owner: 'worker:1',
        ttl_seconds: 12,
        contended_count: 0,
        lost_count: 0,
        supervisor_status: 'running',
        heartbeat_stale: false,
        restart_count: 0,
        last_error: null,
      },
    },
  },
  repair: {
    status: 'ok',
    degraded_reasons: [],
    actions: [],
  },
  operator_report: {
    status: 'ok',
    workspace_id: 1,
    summary: 'Runtime healthy.',
    finding_count: 0,
    critical_count: 0,
    warning_count: 0,
    findings: [],
  },
}

test.describe('Founder runtime smoke', () => {
  test.skip(
    !credentials,
    'Admin smoke env vars are required: OQIM_ADMIN_SMOKE_PHONE and OQIM_ADMIN_SMOKE_PASSWORD.',
  )

  test('founder can open runtime console without stale-session network wall', async ({ page }) => {
    await authenticateSeededSession(page, credentials!)

    await openProtectedRoute(page, '/founder/runtime')
    await expect(page.getByRole('heading', { name: /founder runtime/i })).toBeVisible()
    await expect(page.getByText(/service health/i).first()).toBeVisible()
    await expect(page.getByText(/Runtime console is restricted/i)).toHaveCount(0)
    await expect(page.getByText(/Tarmoq xatosi/i)).toHaveCount(0)
    await expect(page.getByText(/Dashboard or runtime signals could not be loaded/i)).toHaveCount(0)
  })
})

test('founder runtime shows spend, cost trend, and usage accounting from runtime signals', async ({ page }) => {
  await page.route('**/api/auth/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(founderSession),
    }),
  )
  await page.route('**/api/admin/runtime-signals', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(runtimeSignals),
    }),
  )

  await page.goto('/founder/runtime')

  await expect(page.getByRole('heading', { name: /founder runtime/i })).toBeVisible()
  await expect(page.getByText('Est. spend')).toBeVisible()
  await expect(page.getByText('$0.0011').first()).toBeVisible()
  await expect(page.getByText('Usage accounting')).toBeVisible()
  await expect(page.getByText('14-day cost trend')).toBeVisible()
  await expect(page.getByRole('cell', { name: 'seller_agent', exact: true })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'retrieval_core', exact: true })).toBeVisible()
  await expect(page.getByText('configured', { exact: true })).toBeVisible()
  await expect(page.getByText(/Runtime console is restricted/i)).toHaveCount(0)
  await expect(page.getByText(/Tarmoq xatosi/i)).toHaveCount(0)
})
