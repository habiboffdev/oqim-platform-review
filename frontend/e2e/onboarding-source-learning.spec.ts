import { expect, test, type Page } from '@playwright/test'

const session = {
  schema_version: 'auth_session_projection.v1',
  authenticated: true,
  workspace: {
    id: 7,
    phone_number: '+998991234567',
    name: '',
    type: 'fashion',
    monthly_revenue_band: null,
    subscription_tier: 'pilot',
    telegram_connected: true,
    onboarding_completed: false,
    created_at: '2026-05-06T00:00:00Z',
    updated_at: '2026-05-06T00:00:00Z',
  },
  platform_role: 'business_owner',
  is_founder: false,
  onboarding_completed: false,
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

const runtime = {
  schema_version: 'onboarding_runtime.v1',
  workspace_id: 7,
  state: 'running',
  phase: 'learning_sources',
  percent: 74,
  current_stage_id: 'source_learning',
  stages: [],
  is_running: true,
  is_terminal: false,
  is_retryable: false,
  is_dlq: false,
  can_requeue: false,
  lease_expired: false,
  attempt_count: 1,
  max_attempts: 3,
  progress: {
    workspace_id: 7,
    phase: 'learning_sources',
    percent: 74,
    contacts_found: 548,
    customers_identified: 50,
    history_learning_conversation_limit: 50,
    history_learning_message_limit: 12,
    history_replayed_conversations: 50,
    history_replayed_messages: 320,
    products_extracted: 2,
    knowledge_items: 1,
    voice_profile_ready: false,
    voice_discoveries: [],
    completed: false,
    errors: [],
  },
  source_learning: {
    schema_version: 'onboarding_source_learning.v1',
    status: 'needs_review',
    percent: 82,
    summary: {
      total: 4,
      learning: 1,
      learned: 2,
      needs_review: 2,
      missing: 0,
      conflict: 0,
      retrying: 1,
      failed: 0,
    },
    sources: [
      {
        source_ref: 'onboarding:source:0',
        fact_id: 'business_source:onboarding:source:0',
        kind: 'pdf',
        label: 'catalog.pdf',
        status: 'needs_review',
        retryable: false,
        source_unit_count: 4,
        source_media_count: 2,
        degraded_reasons: [],
      },
    ],
  },
  learned_review: {
    schema_version: 'onboarding_learned_review.v1',
    status: 'needs_review',
    summary: {
      products: 2,
      knowledge: 1,
      rules: 0,
      voice: 0,
      integrations: 0,
      media: 2,
      offers: 2,
      total_review_items: 3,
    },
    products: [
      {
        product_ref: 'catalog_product:binafsha-sumka',
        fact_id: 'catalog_product:binafsha-sumka',
        title: 'Binafsha charm sumka',
        category: 'sumka',
        description: 'Binafsha rang charm sumka.',
        confidence: 0.86,
        risk_tier: 'medium',
        source_refs: ['source_unit:onboarding:web'],
        offers: [{ price: { amount: 180000, currency: 'UZS' } }],
        media: [{ url: 'https://nafis.example/sumka.jpg' }],
      },
      {
        product_ref: 'catalog_product:binafsha-sumka-copy',
        fact_id: 'catalog_product:binafsha-sumka-copy',
        title: 'Binafsha sumka kopiya',
        category: 'sumka',
        description: 'Binafsha rang charm sumka.',
        confidence: 0.72,
        risk_tier: 'medium',
        source_refs: ['source_unit:onboarding:web'],
        offers: [],
        media: [],
      },
    ],
    knowledge: [
      {
        fact_id: 'knowledge:onboarding:delivery',
        fact_type: 'knowledge_fact',
        entity_ref: 'business:delivery',
        topic: 'Yetkazib berish',
        answer: 'Toshkent ichida yetkazib berish bor.',
        summary: null,
        requirement: null,
        rule: null,
        details: {},
        observations: [],
        confidence: 0.83,
        risk_tier: 'medium',
        source_refs: ['source_unit:onboarding:delivery'],
      },
    ],
    rules: [],
    voice: [],
    integrations: [],
  },
}

const workspaceOS = {
  schema_version: 'workspace_os_projection.v1',
  workspace_id: 7,
  workspace_name: 'Nafis Liboslar',
  onboarding_completed: false,
  telegram_connected: true,
  generated_at: '2026-05-17T00:00:00Z',
  readiness: {
    status: 'needs_review',
    percent: 68,
    issues: [
      {
        code: 'review_pending',
        severity: 'warning',
        target_kind: 'action',
        target_ref: 'review:learned',
        title_uz: 'Tasdiq kerak',
        detail_uz: 'OQIM topgan ma’lumotlarni tekshirib chiqing.',
        action_label_uz: 'Ko‘rib chiqish',
      },
    ],
  },
  agents: [],
  documents: {
    business_section_count: 4,
    agent_section_count: 3,
    skill_section_count: 2,
    owner_edited_section_count: 0,
    missing_business_sections: [],
    business_md_ready: true,
  },
  sources: {
    status: 'learning',
    summary: {
      total: 2,
      learned: 1,
      retrying: 1,
      failed: 0,
    },
    sources: [
      {
        source_ref: 'onboarding:source:telegram',
        kind: 'telegram_channel',
        purpose: 'brain_data',
        label: '@nafis_shop',
        status: 'learning',
        raw_state: 'learning',
        source_unit_count: 12,
        source_media_count: 2,
        degraded_reasons: [],
        retryable: false,
      },
      {
        source_ref: 'onboarding:source:pdf',
        kind: 'pdf',
        purpose: 'brain_data',
        label: 'catalog.pdf',
        status: 'retrying',
        raw_state: 'retrying',
        source_unit_count: 0,
        source_media_count: 1,
        degraded_reasons: ['missing_file_content'],
        retryable: true,
      },
    ],
  },
  actions: {
    needs_approval: 2,
    scheduled: 0,
    running: 0,
    done: 0,
    failed: 0,
    rejected: 0,
  },
  tasks: {
    proposed: 1,
    active: 0,
    done: 0,
    failed: 0,
  },
}

async function mockOnboardingApis(page: Page) {
  await page.route('**/api/**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({}),
    }),
  )
  await page.route('**/api/auth/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(session),
    }),
  )
  await page.route('**/api/telegram/status', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        connected: true,
        state: 'connected',
        identityLinked: true,
        needsReconnect: false,
      }),
    }),
  )
  await page.route('**/api/onboarding/runtime', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(runtime),
    }),
  )
  await page.route('**/api/workspace-os/state', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(workspaceOS),
    }),
  )
  await page.route('**/api/workspace-os/provision', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(workspaceOS),
    }),
  )
  await page.route('**/api/telegram/channels', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        count: 2,
        channels: [
          {
            id: 91,
            name: 'Nafis kanal',
            username: 'nafis_shop',
            member_count: 1200,
            is_own: true,
            is_broadcast: true,
          },
          {
            id: 92,
            name: 'Ikkinchi kanal',
            username: 'ikkinchi_shop',
            member_count: 320,
            is_own: false,
            is_broadcast: true,
          },
        ],
      }),
    }),
  )
  await page.route('**/api/telegram/start-ingestion', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ok: true }),
    }),
  )
}

test.describe('Onboarding source learning workbench', () => {
  test('shows the approved source, learned artifact, and status rail layout', async ({ page }) => {
    await mockOnboardingApis(page)

    await page.goto('/onboarding')

    await expect(page.getByText('Manba qo‘shish', { exact: true })).toBeVisible()
    await expect(page.getByText('OQIM nimalarni o‘rgandi')).toBeVisible()
    await expect(page.getByText('Jarayon')).toBeVisible()
    await expect(page.getByText('2 ta manba')).toBeVisible()
    await expect(page.getByText('5 ta taklif')).toBeVisible()
    await expect(page.getByText('Binafsha charm sumka')).toBeVisible()
    await page.getByRole('button', { name: /Bilim bazasi/ }).click()
    await expect(page.getByText('Toshkent ichida yetkazib berish bor.')).toBeVisible()

    await page.getByRole('button', { name: /@nafis_shop/ }).click()
    await expect(page.getByLabel('Telegram kanal')).toHaveValue('@nafis_shop')

    await page.getByRole('tab', { name: 'Hammasi' }).click()
    await expect(page.getByRole('button', { name: /@ikkinchi_shop/ })).toBeVisible()
    await page.getByRole('button', { name: /@ikkinchi_shop/ }).click()
    await expect(page.getByLabel('Telegram kanal')).toHaveValue('@nafis_shop\n@ikkinchi_shop')

    await page.getByLabel('Qaysi sanadan').fill('2026-05-01')
    await page.getByLabel('Qaysi sanagacha').fill('2026-05-17')
    await page.getByRole('button', { name: /Sayt/ }).click()
    await page.getByLabel('Sayt').fill('https://nafis.example/shop')
    await expect(page.getByLabel('Sayt')).toHaveValue('https://nafis.example/shop')
  })

  test('records voice guidance and sends the expected onboarding payload', async ({ page }) => {
    const reviewActions: unknown[] = []
    let completePayload: Record<string, unknown> | null = null

    await page.addInitScript(() => {
      const stream = {
        getTracks: () => [{ stop: () => undefined }],
      }
      Object.defineProperty(navigator, 'mediaDevices', {
        configurable: true,
        value: {
          getUserMedia: async () => stream,
        },
      })
      class FakeMediaRecorder {
        stream: typeof stream
        mimeType = 'audio/webm'
        ondataavailable?: (event: { data: Blob }) => void
        onstop?: () => void

        constructor(nextStream: typeof stream) {
          this.stream = nextStream
        }

        start() {
          this.ondataavailable?.({ data: new Blob(['voice'], { type: this.mimeType }) })
        }

        stop() {
          this.onstop?.()
        }
      }
      Object.defineProperty(window, 'MediaRecorder', {
        configurable: true,
        value: FakeMediaRecorder,
      })
    })
    await mockOnboardingApis(page)
    await page.route('**/api/onboarding/learned-review/actions', async (route) => {
      reviewActions.push(route.request().postDataJSON())
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true }),
      })
    })
    await page.route('**/api/auth/complete-onboarding', async (route) => {
      completePayload = route.request().postDataJSON()
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true }),
      })
    })

    await page.goto('/onboarding')

    await page.getByRole('button', { name: /@nafis_shop/ }).click()
    await page.getByRole('button', { name: /Sayt/ }).click()
    await page.getByLabel('Sayt').fill('https://nafis.example/shop')
    await page.getByLabel('Fayl').setInputFiles({
      name: 'catalog.pdf',
      mimeType: 'application/pdf',
      buffer: Buffer.from('%PDF-1.4 catalog'),
    })
    await page.getByRole('button', { name: 'Ovoz yozish' }).click()
    await expect(page.getByRole('button', { name: 'Yozishni to‘xtatish' })).toBeVisible()
    await page.getByRole('button', { name: 'Yozishni to‘xtatish' }).click()
    await expect(page.getByText(/oqim-ovoz-/)).toBeVisible()
    await page.getByLabel('Manbalar').fill('Telegram kanal: @nafis_shop, katalog PDF bor')

    await page.getByRole('button', { name: 'Tahrirlash' }).first().click()
    await page.getByLabel('Mahsulot nomi').fill('Binafsha klassik sumka')
    await page.getByRole('button', { name: 'Tuzatib tasdiqlash' }).click()
    await page.getByRole('button', { name: 'Birlashtirish' }).first().click()
    await expect.poll(() => reviewActions.length).toBe(2)

    await page.getByRole('button', { name: 'Davom etish' }).click()
    await expect(page.getByText('Agent manbalari')).toBeVisible()
    await page.getByLabel('Qoidalar').fill("Yetkazib berish so'ralsa, avval tuman va telefon so'ra.")
    await page.getByRole('button', { name: 'Sotuvchi ohangi' }).click()
    await page.getByLabel('Sotuvchi ohangi').fill('Chegirma faqat qaytgan mijozlarga.')
    await page.getByRole('button', { name: 'Davom etish' }).click()

    await page.getByLabel('Biznes nomi').fill('Nafis Liboslar')
    await page.getByLabel('Nima sotasiz?').fill('Ayollar kiyimi va aksessuarlar')
    await page.getByLabel('Qaysi hududda ishlaysiz?').fill('Toshkent')
    await page.getByLabel('Parol').fill('strongpass123')
    await page.getByRole('button', { name: 'Sozlashni yakunlash' }).click()

    await expect.poll(() => completePayload).not.toBeNull()
    expect(reviewActions[0]).toEqual({
      action: 'edit',
      target_type: 'fact',
      target_ref: 'catalog_product:binafsha-sumka',
      value_patch: { title: 'Binafsha klassik sumka' },
    })
    expect(reviewActions[1]).toEqual({
      action: 'merge',
      target_type: 'product',
      target_ref: 'catalog_product:binafsha-sumka',
      merge_into_ref: 'catalog_product:binafsha-sumka-copy',
    })
    expect(completePayload).toMatchObject({
      name: 'Nafis Liboslar',
      sources: {
        notes: 'Telegram kanal: @nafis_shop, katalog PDF bor',
        items: [
          {
            kind: 'website',
            url: 'https://nafis.example/shop',
          },
          {
            kind: 'telegram_channel',
            handle: '@nafis_shop',
          },
          {
            kind: 'file',
            file_name: 'catalog.pdf',
            content_type: 'application/pdf',
          },
          {
            kind: 'text',
            text: 'Chegirma faqat qaytgan mijozlarga.',
            purpose: 'agent_data',
          },
        ],
      },
      owner_rules: {
        notes: "Yetkazib berish so'ralsa, avval tuman va telefon so'ra.",
      },
    })
  })
})
