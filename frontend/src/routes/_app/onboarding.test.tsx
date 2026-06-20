// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { uz } from '@/lib/uz'

const {
  mockNavigate,
  mockRefreshUser,
  mockToastError,
  mockToastSuccess,
  mockUseAuth,
  mockUseOnboardingRuntime,
  mockUseOnboardingDocuments,
  mockUseGenerateOnboardingDocuments,
  mockUseProvisionWorkspaceOS,
  mockUseTelegramConnectionStatus,
  mockUseWorkspaceOS,
} = vi.hoisted(() => ({
  mockNavigate: vi.fn(),
  mockRefreshUser: vi.fn(),
  mockToastError: vi.fn(),
  mockToastSuccess: vi.fn(),
  mockUseAuth: vi.fn(),
  mockUseOnboardingRuntime: vi.fn(),
  mockUseOnboardingDocuments: vi.fn(),
  mockUseGenerateOnboardingDocuments: vi.fn(),
  mockUseProvisionWorkspaceOS: vi.fn(),
  mockUseTelegramConnectionStatus: vi.fn(),
  mockUseWorkspaceOS: vi.fn(),
}))

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
}))

vi.mock('sonner', () => ({
  toast: {
    error: mockToastError,
    success: mockToastSuccess,
  },
}))

vi.mock('@/lib/auth-context', () => ({
  useAuth: () => mockUseAuth(),
}))

vi.mock('@/hooks/use-telegram-connection-status', () => ({
  useTelegramConnectionStatus: () => mockUseTelegramConnectionStatus(),
}))

vi.mock('@/hooks/use-onboarding-runtime', () => ({
  useOnboardingRuntime: () => mockUseOnboardingRuntime(),
}))

vi.mock('@/hooks/use-onboarding-documents', () => ({
  useOnboardingDocuments: () => mockUseOnboardingDocuments(),
  useOnboardingDocumentsStream: () => undefined,
  useGenerateOnboardingDocuments: () => mockUseGenerateOnboardingDocuments(),
}))

vi.mock('@/hooks/use-workspace-os', () => ({
  useWorkspaceOS: () => mockUseWorkspaceOS(),
  useProvisionWorkspaceOS: () => mockUseProvisionWorkspaceOS(),
}))

vi.mock('@/components/blocks/onboarding/phone-auth', () => ({
  PhoneAuth: ({ onSuccess }: { onSuccess: (user: {
    userId: string
    phone: string
    firstName: string
    lastName: string
    authMethod?: 'phone' | 'qr'
  }) => void }) => (
    <button
      type="button"
      onClick={() => onSuccess({
        userId: '42',
        phone: '+998991234567',
        firstName: 'Ali',
        lastName: '',
        authMethod: 'qr',
      })}
    >
      mock telegram success
    </button>
  ),
}))

vi.mock('@/lib/api-client', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement } from 'react'
import { api } from '@/lib/api-client'
import { OnboardingPage, buildOnboardingSourceItems } from './onboarding'

const mockedApi = vi.mocked(api)

function renderOnboarding() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    createElement(QueryClientProvider, { client: queryClient }, createElement(OnboardingPage)),
  )
}

describe('OnboardingPage Telegram identity UX', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.history.pushState({}, '', '/onboarding')
    mockUseAuth.mockReturnValue({
      user: null,
      refreshUser: mockRefreshUser,
    })
    mockUseTelegramConnectionStatus.mockReturnValue({
      data: { connected: false, needsReconnect: false },
      isLoading: false,
    })
    mockUseOnboardingRuntime.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })
    mockUseOnboardingDocuments.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })
    mockUseGenerateOnboardingDocuments.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    })
    mockUseWorkspaceOS.mockReturnValue({
      data: undefined,
      refetch: vi.fn(),
    })
    mockUseProvisionWorkspaceOS.mockReturnValue({
      isPending: false,
      mutateAsync: vi.fn().mockResolvedValue({}),
    })
    mockedApi.get.mockResolvedValue({ phase: 'idle' })
  })

  it('routes uploaded screenshots to the screenshot source runtime', () => {
    const items = buildOnboardingSourceItems({
      websiteSource: '',
      telegramChannelSource: '',
      fileSource: {
        fileName: 'program-screenshot.png',
        contentType: 'image/png',
        contentBase64: 'aW1hZ2U=',
        byteSize: 5,
      },
      voiceSource: '',
      voiceFileSource: null,
    })

    expect(items).toEqual([
      {
        kind: 'screenshot',
        label: 'program-screenshot.png',
        file_name: 'program-screenshot.png',
        content_type: 'image/png',
        content_base64: 'aW1hZ2U=',
        byte_size: 5,
        purpose: 'brain_data',
      },
    ])
  })

  it('keeps business audio as editable manual source text before learning', () => {
    const items = buildOnboardingSourceItems({
      sourceNotes: 'Narx so‘ralsa, avval modelini aniqlang.',
      websiteSource: '',
      telegramChannelSource: '',
      fileSource: null,
      voiceSource: 'Qisqa va iliq ohangda yozing.',
      voiceFileSource: {
        fileName: 'agent-style.ogg',
        contentType: 'audio/ogg',
        contentBase64: 'dm9pY2U=',
        byteSize: 5,
      },
    })

    expect(items).toEqual([
      {
        kind: 'text',
        label: uz.onboarding.manualBrainSource,
        text: 'Narx so‘ralsa, avval modelini aniqlang.',
        purpose: 'brain_data',
      },
      {
        kind: 'text',
        label: 'Audio matni: agent-style.ogg',
        text: 'Qisqa va iliq ohangda yozing.',
        purpose: 'agent_data',
      },
    ])
  })

  it('shows connected sellers the sparse Business Brain source step without auto-starting ingestion', async () => {
    mockUseAuth.mockReturnValue({
      user: {
        id: 7,
        workspace_id: 7,
        name: 'Existing Shop',
        phone_number: '+998991234567',
        onboarding_completed: false,
      },
      refreshUser: mockRefreshUser,
    })
    mockUseTelegramConnectionStatus.mockReturnValue({
      data: { connected: true, needsReconnect: false },
      isLoading: false,
    })
    mockedApi.post.mockResolvedValue({})

    renderOnboarding()

    expect(mockedApi.post).not.toHaveBeenCalledWith('/api/telegram/start-ingestion')
    expect(screen.getByText('Manba qo‘shish')).toBeDefined()
    expect(screen.getByText('OQIM nimalarni o‘rganyapti')).toBeDefined()
    expect(screen.getByText('Telegram kanallari')).toBeDefined()
    expect(screen.queryByLabelText(uz.onboarding.businessName)).toBeNull()
  })

  it('reuses an existing completed workspace when new-account Telegram auth belongs to it', async () => {
    mockedApi.post.mockResolvedValueOnce({
      id: 7,
      name: 'Existing Shop',
      phone_number: '+998991234567',
      telegram_connected: true,
      onboarding_completed: true,
      is_new: false,
    })

    renderOnboarding()

    fireEvent.click(screen.getByRole('button', { name: new RegExp(uz.auth.newAccount) }))
    fireEvent.click(await screen.findByText('mock telegram success'))

    await waitFor(() => {
      expect(mockRefreshUser).toHaveBeenCalled()
      expect(mockToastSuccess).toHaveBeenCalledWith(uz.onboarding.existingAccountReconnected)
      expect(mockNavigate).toHaveBeenCalledWith({ to: '/conversations' })
    })
  })

  it('labels authenticated disconnected Telegram sessions as reconnect instead of revoked', () => {
    mockUseAuth.mockReturnValue({
      user: {
        id: 7,
        workspace_id: 7,
        name: 'Existing Shop',
        phone_number: '+998991234567',
        onboarding_completed: false,
      },
      refreshUser: mockRefreshUser,
    })
    mockUseTelegramConnectionStatus.mockReturnValue({
      data: { connected: false, needsReconnect: true, state: 'disconnected' },
      isLoading: false,
    })

    renderOnboarding()

    expect(screen.getByText(uz.onboarding.reconnectTitle)).toBeDefined()
    expect(screen.getByText(uz.onboarding.reconnectDesc)).toBeDefined()
    expect(screen.queryByText(uz.onboarding.reconnectRevokedDesc)).toBeNull()
  })

  it('uses revoked wording only when live Telegram status reports revoked', () => {
    mockUseAuth.mockReturnValue({
      user: {
        id: 7,
        workspace_id: 7,
        name: 'Existing Shop',
        phone_number: '+998991234567',
        onboarding_completed: false,
      },
      refreshUser: mockRefreshUser,
    })
    mockUseTelegramConnectionStatus.mockReturnValue({
      data: { connected: false, needsReconnect: true, state: 'revoked' },
      isLoading: false,
    })

    renderOnboarding()

    expect(screen.getByText(uz.onboarding.reconnectRevokedDesc)).toBeDefined()
  })

  it('uses identity mismatch wording when Telegram status belongs to another account', () => {
    mockUseAuth.mockReturnValue({
      user: {
        id: 7,
        workspace_id: 7,
        name: 'Existing Shop',
        phone_number: '+998991234567',
        onboarding_completed: false,
      },
      refreshUser: mockRefreshUser,
    })
    mockUseTelegramConnectionStatus.mockReturnValue({
      data: { connected: false, needsReconnect: true, state: 'stale', identityMismatch: true },
      isLoading: false,
    })

    renderOnboarding()

    expect(screen.getByText(uz.onboarding.reconnectIdentityMismatchDesc)).toBeDefined()
    expect(screen.queryByText(uz.onboarding.reconnectRevokedDesc)).toBeNull()
  })

  it('lets completed workspaces open Telegram reconnect without being bounced from onboarding', () => {
    window.history.pushState({}, '', '/onboarding?reconnect=telegram')
    mockUseAuth.mockReturnValue({
      user: {
        id: 7,
        workspace_id: 7,
        name: 'Existing Shop',
        phone_number: '+998991234567',
        onboarding_completed: true,
      },
      refreshUser: mockRefreshUser,
    })
    mockUseTelegramConnectionStatus.mockReturnValue({
      data: { connected: true, needsReconnect: false },
      isLoading: false,
    })

    renderOnboarding()

    expect(screen.getByText(uz.onboarding.reconnectAlreadyConnectedTitle)).toBeDefined()
    expect(screen.getByText(uz.onboarding.reconnectAlreadyConnectedDesc)).toBeDefined()
    expect(screen.getByRole('button', { name: uz.onboarding.continueSetup })).toBeDefined()
    expect(screen.queryByText('mock telegram success')).toBeNull()
  })

  it('continues onboarding when Telegram is degraded but identity is still linked', async () => {
    mockUseAuth.mockReturnValue({
      user: {
        id: 7,
        workspace_id: 7,
        name: 'Existing Shop',
        phone_number: '+998991234567',
        onboarding_completed: false,
      },
      refreshUser: mockRefreshUser,
    })
    mockUseTelegramConnectionStatus.mockReturnValue({
      data: {
        connected: false,
        state: 'degraded',
        workspaceId: 7,
        userId: '42',
        phone: '+998991234567',
        reconnectAttempts: 0,
        identityLinked: true,
        needsReconnect: false,
      },
      isLoading: false,
    })
    mockedApi.get.mockResolvedValue({
      workspace_id: 7,
      phase: 'reading_dialogs',
      percent: 55,
      contacts_found: 548,
      customers_identified: 0,
      products_extracted: 0,
      knowledge_items: 0,
      voice_profile_ready: false,
      voice_discoveries: [],
      completed: false,
      errors: [],
    })
    mockUseOnboardingRuntime.mockReturnValue({
      data: {
        workspace_id: 7,
        phase: 'reading_dialogs',
        percent: 55,
        stages: [],
        is_running: true,
        is_terminal: false,
        is_dlq: false,
        can_requeue: false,
        attempt_count: 1,
        max_attempts: 3,
        progress: {
          contacts_found: 548,
          customers_identified: 0,
          voice_profile_ready: false,
        },
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })

    renderOnboarding()

    expect(screen.getByText('Manba qo‘shish')).toBeDefined()
    expect(mockedApi.post).not.toHaveBeenCalledWith('/api/telegram/start-ingestion')
  })

  it('does not present old learned progress as ready while a new draft source is unprocessed', async () => {
    mockUseAuth.mockReturnValue({
      user: {
        id: 7,
        workspace_id: 7,
        name: 'Existing Shop',
        phone_number: '+998991234567',
        onboarding_completed: false,
      },
      refreshUser: mockRefreshUser,
    })
    mockUseTelegramConnectionStatus.mockReturnValue({
      data: { connected: true, needsReconnect: false },
      isLoading: false,
    })
    mockUseOnboardingRuntime.mockReturnValue({
      data: {
        schema_version: 'onboarding_runtime.v1',
        workspace_id: 7,
        state: 'completed',
        phase: 'completed',
        percent: 100,
        current_stage_id: 'done',
        stages: [],
        is_running: false,
        is_terminal: true,
        is_retryable: false,
        is_dlq: false,
        can_requeue: false,
        lease_expired: false,
        attempt_count: 1,
        max_attempts: 3,
        progress: {
          workspace_id: 7,
          phase: 'completed',
          percent: 100,
          contacts_found: 50,
          customers_identified: 12,
          products_extracted: 0,
          knowledge_items: 0,
          voice_profile_ready: true,
          voice_discoveries: [],
          completed: true,
          errors: [],
        },
        source_learning: {
          schema_version: 'onboarding_source_learning.v1',
          status: 'learned',
          percent: 100,
          summary: {
            total: 1,
            learning: 0,
            learned: 1,
            needs_review: 0,
            missing: 0,
            conflict: 0,
            retrying: 0,
            failed: 0,
          },
          sources: [{
            source_ref: 'onboarding:source:old',
            kind: 'website',
            label: 'old.example',
            status: 'learned',
            raw_state: 'learned',
            source_unit_count: 1,
            source_media_count: 0,
            degraded_reasons: [],
            retryable: false,
            fact_id: 'source:old',
            entity_ref: 'workspace:source:onboarding:source:old',
            source_refs: ['onboarding:source:old'],
          }],
          events: [],
        },
        learned_review: {
          schema_version: 'onboarding_learned_review.v1',
          status: 'empty',
          summary: {
            products: 0,
            knowledge: 0,
            rules: 0,
            voice: 0,
            integrations: 0,
            media: 0,
            offers: 0,
            total_review_items: 0,
          },
          products: [],
          knowledge: [],
          rules: [],
          voice: [],
          integrations: [],
        },
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })

    renderOnboarding()

    fireEvent.click(screen.getByRole('button', { name: /Sayt/ }))
    fireEvent.change(screen.getByLabelText(uz.onboarding.websiteSource), {
      target: { value: 'https://satstation.io' },
    })

    expect(await screen.findByText('1 ta dalil manbasi hali o‘qilmadi.')).toBeDefined()
    expect(screen.getAllByText('1 ta dalil navbatda').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Davom eting').length).toBeGreaterThan(0)
    expect(screen.getByText('OS tayyorligi')).toBeDefined()
    expect(screen.queryByText('100%')).toBeNull()
  })

  it('keeps accepted background source learning visible as an active stream', async () => {
    mockUseAuth.mockReturnValue({
      user: {
        id: 7,
        workspace_id: 7,
        name: 'Existing Shop',
        phone_number: '+998991234567',
        onboarding_completed: false,
      },
      refreshUser: mockRefreshUser,
    })
    mockUseTelegramConnectionStatus.mockReturnValue({
      data: { connected: true, needsReconnect: false },
      isLoading: false,
    })
    mockUseOnboardingRuntime.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })
    mockedApi.post.mockResolvedValue({})

    renderOnboarding()
    mockedApi.post.mockClear()

    fireEvent.click(screen.getByRole('button', { name: /Sayt/ }))
    fireEvent.change(screen.getByLabelText(uz.onboarding.websiteSource), {
      target: { value: 'https://satstation.io' },
    })
    fireEvent.click(screen.getByRole('button', { name: uz.onboarding.businessContinue }))

    await waitFor(() => {
      expect(mockedApi.post).toHaveBeenCalledWith('/api/business-brain/sources/learn', {
        limit: 1,
        max_attempts: 3,
        background: true,
      })
    })
    // Source learning runs in the background; the document workbench is the next
    // surface the owner sees. (The launch summary follows the workbench.)
    expect(await screen.findByRole('button', { name: uz.onboarding.documents.continue })).toBeDefined()
    expect(screen.getByRole('tab', { name: new RegExp(uz.onboarding.documents.tabBusiness) })).toBeDefined()
  })

  it('surfaces durable source-learning stream details in the status bar', async () => {
    mockUseAuth.mockReturnValue({
      user: {
        id: 7,
        workspace_id: 7,
        name: 'Existing Shop',
        phone_number: '+998991234567',
        onboarding_completed: false,
      },
      refreshUser: mockRefreshUser,
    })
    mockUseTelegramConnectionStatus.mockReturnValue({
      data: { connected: true, needsReconnect: false },
      isLoading: false,
    })
    mockUseOnboardingRuntime.mockReturnValue({
      data: {
        schema_version: 'onboarding_runtime.v1',
        workspace_id: 7,
        state: 'running',
        phase: 'learning_sources',
        percent: 48,
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
          percent: 48,
          contacts_found: 0,
          customers_identified: 0,
          products_extracted: 5,
          knowledge_items: 8,
          voice_profile_ready: false,
          voice_discoveries: [],
          completed: false,
          errors: [],
        },
        source_learning: {
          schema_version: 'onboarding_source_learning.v1',
          status: 'learning',
          percent: 52,
          summary: {
            total: 2,
            learning: 1,
            learned: 1,
            needs_review: 0,
            missing: 0,
            conflict: 0,
            retrying: 0,
            failed: 0,
          },
          sources: [],
          events: [
            {
              event_ref: 'source-learning:onboarding:agent:extracting',
              source_ref: 'onboarding:agent',
              kind: 'text',
              status: 'learning',
              stage: 'extracting',
              source_unit_count: 2,
              source_media_count: 0,
              memory_candidate_count: 1,
              attempt_count: 1,
              max_attempts: 3,
              title_uz: 'Agent sozlamasi ajratilmoqda: AGENT.md qoidalari',
              detail_uz: '2 ta dalil tayyor. Endi AGENT.md, SKILL.md, qoidalar va yozish uslubi ajratilmoqda.',
            },
            {
              event_ref: 'source-learning:onboarding:telegram:fetching',
              source_ref: 'onboarding:telegram',
              kind: 'telegram_channel',
              status: 'running',
              stage: 'fetching_telegram',
              source_unit_count: 12,
              source_media_count: 3,
              attempt_count: 1,
              max_attempts: 3,
              title_uz: 'Telegram o‘qilmoqda: @satstation',
              detail_uz: 'Postlar va rasmlar olinmoqda.',
            },
            {
              event_ref: 'source-learning:onboarding:telegram:extracting',
              source_ref: 'onboarding:telegram',
              kind: 'telegram_channel',
              status: 'running',
              stage: 'extracting',
              source_unit_count: 12,
              source_media_count: 3,
              catalog_candidate_count: 5,
              memory_candidate_count: 8,
              attempt_count: 1,
              max_attempts: 3,
              input_cache_reused: true,
              title_uz: 'Katalog va bilim ajratilmoqda: @satstation',
              detail_uz: '5 ta katalog va 8 ta bilim taklifi topildi.',
            },
          ],
        },
        learned_review: {
          schema_version: 'onboarding_learned_review.v1',
          status: 'empty',
          summary: {
            products: 0,
            knowledge: 0,
            rules: 0,
            voice: 0,
            integrations: 0,
            media: 0,
            offers: 0,
            total_review_items: 0,
          },
          products: [],
          knowledge: [],
          rules: [],
          voice: [],
          integrations: [],
        },
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })

    renderOnboarding()

    expect((await screen.findAllByText('Jonli oqim')).length).toBeGreaterThan(0)
    expect(screen.getAllByText('Katalog va bilim ajratilmoqda: @satstation').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Agent sozlamasi ajratilmoqda: AGENT.md qoidalari').length).toBeGreaterThan(0)
    expect(screen.getAllByText('2 ta dalil tayyor. Endi AGENT.md, SKILL.md, qoidalar va yozish uslubi ajratilmoqda.').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Postlar va rasmlar olinmoqda.').length).toBeGreaterThan(0)
    expect(screen.getByText('Katalog dalili topildi')).toBeDefined()
    expect(screen.getByText(/mahsulot kartasini hali yakuniy haqiqatga aylantirmadi/i)).toBeDefined()
    expect(screen.getAllByText((_, node) => {
      const text = node?.textContent ?? ''
      return text.includes('2 manba')
        && text.includes('cache')
        && text.includes('5 katalog')
        && text.includes('8 bilim')
        && text.includes('3 media')
        && text.includes('1/3')
    }).length).toBeGreaterThan(0)
  })

  it('shows degraded AI learning state and lets the seller retry ingestion', async () => {
    mockUseAuth.mockReturnValue({
      user: {
        id: 7,
        workspace_id: 7,
        name: 'Existing Shop',
        phone_number: '+998991234567',
        onboarding_completed: false,
      },
      refreshUser: mockRefreshUser,
    })
    mockUseTelegramConnectionStatus.mockReturnValue({
      data: { connected: true, needsReconnect: false },
      isLoading: false,
    })
    mockedApi.get.mockResolvedValue({
      workspace_id: 7,
      phase: 'reading_dialogs',
      percent: 55,
      contacts_found: 548,
      customers_identified: 0,
      products_extracted: 0,
      knowledge_items: 0,
      voice_profile_ready: false,
      voice_profile_degraded: true,
      voice_profile_error: 'voice_profile_degraded',
      contact_classification_degraded: false,
      ai_learning_degraded: true,
      ai_learning_error: 'voice_profile_degraded',
      voice_discoveries: [],
      completed: false,
      errors: ['voice_profile_degraded'],
    })
    mockUseOnboardingRuntime.mockReturnValue({
      data: {
        workspace_id: 7,
        phase: 'reading_dialogs',
        percent: 55,
        stages: [],
        is_running: true,
        is_terminal: false,
        is_dlq: false,
        can_requeue: false,
        attempt_count: 1,
        max_attempts: 3,
        progress: {
          workspace_id: 7,
          phase: 'reading_dialogs',
          percent: 55,
          contacts_found: 548,
          customers_identified: 0,
          products_extracted: 0,
          knowledge_items: 0,
          voice_profile_ready: false,
          voice_profile_degraded: true,
          ai_learning_degraded: true,
          contact_classification_degraded: false,
          voice_discoveries: [],
          completed: false,
          errors: ['voice_profile_degraded'],
        },
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })

    renderOnboarding()

    expect((await screen.findAllByText(uz.onboarding.learningDegraded)).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Yozish uslubi uchun suhbat yetarli emas/).length).toBeGreaterThan(0)

    mockedApi.post.mockClear()
    fireEvent.click(screen.getByRole('button', { name: uz.onboarding.retryLearning }))

    await waitFor(() => {
      expect(mockedApi.post).toHaveBeenCalledWith('/api/telegram/start-ingestion')
    })
  })

  it('shows the latest-50 conversation learning window and voice discoveries', async () => {
    mockUseAuth.mockReturnValue({
      user: {
        id: 7,
        workspace_id: 7,
        name: 'Existing Shop',
        phone_number: '+998991234567',
        onboarding_completed: false,
      },
      refreshUser: mockRefreshUser,
    })
    mockUseTelegramConnectionStatus.mockReturnValue({
      data: { connected: true, needsReconnect: false },
      isLoading: false,
    })
    mockUseOnboardingRuntime.mockReturnValue({
      data: {
        workspace_id: 7,
        phase: 'generating_voice_profile',
        percent: 62,
        stages: [],
        is_running: true,
        is_terminal: false,
        is_dlq: false,
        can_requeue: false,
        attempt_count: 1,
        max_attempts: 3,
        progress: {
          workspace_id: 7,
          phase: 'generating_voice_profile',
          percent: 62,
          contacts_found: 50,
          customers_identified: 38,
          visible_dialog_limit: 50,
          history_learning_conversation_limit: 50,
          history_learning_message_limit: 12,
          history_prefetched_conversations: 50,
          history_replayed_conversations: 50,
          history_replayed_messages: 324,
          products_extracted: 0,
          knowledge_items: 0,
          voice_profile_ready: true,
          voice_discoveries: [
            { icon: 'chat', label: 'Yozish usuli: qisqa', subtitle: 'burst=4' },
          ],
          completed: false,
          errors: [],
        },
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })
    mockedApi.post.mockResolvedValue({})

    renderOnboarding()

    expect(screen.getByText(uz.onboarding.learningCustomers(38))).toBeDefined()
    expect(screen.queryByText(uz.onboarding.learningContacts(151))).toBeNull()
    expect(screen.queryByText('151 ta kontakt topildi')).toBeNull()
    expect(screen.getByText('Suhbat tarixi')).toBeDefined()
    expect(screen.getAllByText('Yozish uslubi o‘rganildi').length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Yozish usuli: qisqa/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/324 xabar/).length).toBeGreaterThan(0)
  })

  it('separates imported Telegram contacts from the latest-50 learning window', async () => {
    mockUseAuth.mockReturnValue({
      user: {
        id: 7,
        workspace_id: 7,
        name: 'Existing Shop',
        phone_number: '+998991234567',
        onboarding_completed: false,
      },
      refreshUser: mockRefreshUser,
    })
    mockUseTelegramConnectionStatus.mockReturnValue({
      data: { connected: true, needsReconnect: false },
      isLoading: false,
    })
    mockUseOnboardingRuntime.mockReturnValue({
      data: {
        workspace_id: 7,
        phase: 'classifying_contacts',
        percent: 72,
        stages: [
          {
            id: 'voice',
            label: 'Sotuvchi uslubi tayyor',
            status: 'degraded',
            percent: 80,
            detail: 'voice_profile_degraded',
            error: null,
          },
        ],
        is_running: true,
        is_terminal: false,
        is_dlq: false,
        can_requeue: false,
        attempt_count: 1,
        max_attempts: 3,
        progress: {
          workspace_id: 7,
          phase: 'classifying_contacts',
          percent: 72,
          contacts_found: 152,
          customers_identified: 152,
          visible_dialog_limit: 200,
          history_learning_conversation_limit: 50,
          history_learning_message_limit: 12,
          history_prefetched_conversations: 152,
          history_replayed_conversations: 50,
          history_replayed_messages: 600,
          products_extracted: 0,
          knowledge_items: 0,
          voice_profile_ready: false,
          voice_profile_degraded: true,
          voice_discoveries: [],
          completed: false,
          errors: [],
        },
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })
    mockedApi.post.mockResolvedValue({})

    renderOnboarding()

    expect(await screen.findByText(uz.onboarding.learningCustomers(50))).toBeDefined()
    expect(screen.getByText('Suhbat tarixi')).toBeDefined()
    expect(screen.queryByText(uz.onboarding.learningCustomers(152))).toBeNull()
    expect(screen.queryByText('voice_profile_degraded')).toBeNull()
    expect(screen.getByText(/Yozish uslubi uchun suhbat yetarli emas/)).toBeDefined()
  })

  it('does not show retryable learning state before explicit learning starts', async () => {
    mockUseAuth.mockReturnValue({
      user: {
        id: 7,
        workspace_id: 7,
        name: 'Existing Shop',
        phone_number: '+998991234567',
        onboarding_completed: false,
      },
      refreshUser: mockRefreshUser,
    })
    mockUseTelegramConnectionStatus.mockReturnValue({
      data: { connected: true, needsReconnect: false },
      isLoading: false,
    })
    mockUseOnboardingRuntime.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })
    mockedApi.post.mockRejectedValueOnce(new Error('sidecar down'))

    renderOnboarding()

    expect(await screen.findByText(/OQIM boshlamaguncha/)).toBeDefined()
    expect(screen.queryByText(uz.onboarding.learningDegraded)).toBeNull()
    expect(screen.queryByRole('button', { name: uz.onboarding.retryLearning })).toBeNull()
    expect(mockToastError).not.toHaveBeenCalledWith(uz.onboarding.learningRetryFailed)
  })

  it('shows failed source-learning reasons and lets the seller retry learning', async () => {
    mockUseAuth.mockReturnValue({
      user: {
        id: 7,
        workspace_id: 7,
        name: 'Existing Shop',
        phone_number: '+998991234567',
        onboarding_completed: false,
      },
      refreshUser: mockRefreshUser,
    })
    mockUseTelegramConnectionStatus.mockReturnValue({
      data: { connected: true, needsReconnect: false },
      isLoading: false,
    })
    mockUseOnboardingRuntime.mockReturnValue({
      data: {
        schema_version: 'onboarding_runtime.v1',
        workspace_id: 7,
        state: 'running',
        phase: 'learning_sources',
        percent: 60,
        current_stage_id: 'source_learning',
        stages: [],
        is_running: true,
        is_terminal: false,
        is_retryable: true,
        is_dlq: false,
        can_requeue: true,
        lease_expired: false,
        attempt_count: 1,
        max_attempts: 3,
        progress: {
          workspace_id: 7,
          phase: 'learning_sources',
          percent: 60,
          contacts_found: 32,
          customers_identified: 12,
          products_extracted: 0,
          knowledge_items: 0,
          voice_profile_ready: false,
          voice_discoveries: [],
          completed: false,
          errors: [],
        },
        source_learning: {
          schema_version: 'onboarding_source_learning.v1',
          status: 'failed',
          percent: 100,
          summary: {
            total: 1,
            learning: 0,
            learned: 0,
            needs_review: 0,
            missing: 0,
            conflict: 0,
            retrying: 0,
            failed: 1,
          },
          sources: [{
            source_ref: 'onboarding:source:0',
            kind: 'screenshot',
            label: 'program-screenshot.png',
            status: 'failed',
            raw_state: 'failed',
            source_unit_count: 0,
            source_media_count: 1,
            degraded_reasons: ['missing_file_content'],
            retryable: true,
            fact_id: 'source:program-screenshot',
            entity_ref: 'workspace:source:onboarding:source:0',
            source_refs: ['onboarding:source:0'],
          }],
          events: [{
            event_ref: 'source-learning:onboarding:source:0:failed',
            source_ref: 'onboarding:source:0',
            kind: 'screenshot',
            status: 'failed',
            title_uz: 'Qayta tekshirish kerak: program-screenshot.png',
            detail_uz: 'Fayl topilmadi yoki o‘qilmadi.',
          }],
        },
        learned_review: {
          schema_version: 'onboarding_learned_review.v1',
          status: 'empty',
          summary: {
            products: 0,
            knowledge: 0,
            rules: 0,
            voice: 0,
            integrations: 0,
            media: 0,
            offers: 0,
            total_review_items: 0,
          },
          products: [],
          knowledge: [],
          rules: [],
          voice: [],
          integrations: [],
        },
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })
    mockedApi.post.mockResolvedValue({})

    renderOnboarding()
    mockedApi.post.mockClear()

    expect(await screen.findByText('Fayldan ma’lumot o‘qilmadi')).toBeDefined()
    const liveStream = screen.getByLabelText('Jonli onboarding jarayoni')
    expect(within(liveStream).getByText('Qayta tekshirish kerak: program-screenshot.png')).toBeDefined()
    expect(screen.getAllByText('Fayl topilmadi yoki o‘qilmadi.').length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: 'Qayta urinish' }))

    await waitFor(() => {
      expect(mockedApi.post).toHaveBeenCalledWith('/api/business-brain/sources/retry', {
        limit: 10,
        max_attempts: 3,
        background: true,
      })
    })

    mockedApi.post.mockClear()
    fireEvent.click(screen.getByRole('button', { name: uz.onboarding.retryLearning }))

    await waitFor(() => {
      expect(mockedApi.post).toHaveBeenCalledWith('/api/business-brain/sources/retry', {
        limit: 10,
        max_attempts: 3,
        background: true,
      })
    })
  })

  it('keeps source retry UI stable when a runtime source has no degraded reason array', async () => {
    mockUseAuth.mockReturnValue({
      user: {
        id: 7,
        workspace_id: 7,
        name: 'Existing Shop',
        phone_number: '+998991234567',
        onboarding_completed: false,
      },
      refreshUser: mockRefreshUser,
    })
    mockUseTelegramConnectionStatus.mockReturnValue({
      data: { connected: true, needsReconnect: false },
      isLoading: false,
    })
    mockUseOnboardingRuntime.mockReturnValue({
      data: {
        schema_version: 'onboarding_runtime.v1',
        workspace_id: 7,
        state: 'running',
        phase: 'learning_sources',
        percent: 50,
        current_stage_id: 'source_learning',
        stages: [],
        is_running: true,
        is_terminal: false,
        is_retryable: true,
        is_dlq: false,
        can_requeue: true,
        lease_expired: false,
        attempt_count: 1,
        max_attempts: 3,
        progress: {
          workspace_id: 7,
          phase: 'learning_sources',
          percent: 50,
          contacts_found: 12,
          customers_identified: 6,
          products_extracted: 0,
          knowledge_items: 0,
          voice_profile_ready: false,
          voice_discoveries: [],
          completed: false,
          errors: [],
        },
        source_learning: {
          schema_version: 'onboarding_source_learning.v1',
          status: 'retrying',
          percent: 50,
          summary: {
            total: 1,
            learning: 0,
            learned: 0,
            needs_review: 0,
            missing: 0,
            conflict: 0,
            retrying: 1,
            failed: 0,
          },
          sources: [{
            source_ref: 'onboarding:source:1',
            kind: 'website',
            label: 'https://example.com',
            status: 'retrying',
            raw_state: 'retrying',
            source_unit_count: 0,
            source_media_count: 0,
            retryable: true,
            fact_id: 'source:example',
            entity_ref: 'workspace:source:onboarding:source:1',
            source_refs: ['onboarding:source:1'],
          }],
        },
        learned_review: {
          schema_version: 'onboarding_learned_review.v1',
          status: 'empty',
          summary: {
            products: 0,
            knowledge: 0,
            rules: 0,
            voice: 0,
            integrations: 0,
            media: 0,
            offers: 0,
            total_review_items: 0,
          },
          products: [],
          knowledge: [],
          rules: [],
          voice: [],
          integrations: [],
        },
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })
    mockedApi.post.mockResolvedValue({})

    renderOnboarding()

    expect(await screen.findByText('Manbani qayta tekshirish kerak')).toBeDefined()
    expect(screen.getByRole('button', { name: uz.onboarding.retryLearning })).toBeDefined()
  })

  it('lets the seller approve learned product proposals from onboarding review', async () => {
    const refetch = vi.fn()
    mockUseAuth.mockReturnValue({
      user: {
        id: 7,
        workspace_id: 7,
        name: 'Nafis Liboslar',
        phone_number: '+998991234567',
        onboarding_completed: false,
      },
      refreshUser: mockRefreshUser,
    })
    mockUseTelegramConnectionStatus.mockReturnValue({
      data: { connected: true, needsReconnect: false },
      isLoading: false,
    })
    mockUseOnboardingRuntime.mockReturnValue({
      data: {
        schema_version: 'onboarding_runtime.v1',
        workspace_id: 7,
        state: 'running',
        phase: 'learning_sources',
        percent: 70,
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
          percent: 70,
          contacts_found: 548,
          customers_identified: 180,
          products_extracted: 1,
          knowledge_items: 0,
          voice_profile_ready: false,
          voice_discoveries: [],
          completed: false,
          errors: [],
        },
        source_learning: {
          schema_version: 'onboarding_source_learning.v1',
          status: 'needs_review',
          percent: 80,
          summary: {
            total: 1,
            learning: 0,
            learned: 1,
            needs_review: 1,
            missing: 0,
            conflict: 0,
            retrying: 0,
            failed: 0,
          },
          sources: [],
        },
        learned_review: {
          schema_version: 'onboarding_learned_review.v1',
          status: 'needs_review',
          summary: {
            products: 1,
            knowledge: 1,
            rules: 0,
            voice: 0,
            integrations: 0,
            media: 1,
            offers: 1,
            total_review_items: 2,
          },
          products: [{
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
          }],
          knowledge: [{
            fact_id: 'knowledge:mentor-sla',
            fact_type: 'knowledge_fact',
            entity_ref: 'business:support',
            topic: 'Mentor javobi',
            question: 'Mentor qachon javob beradi?',
            answer: 'Mentorlar 24 soat ichida javob beradi.',
            summary: null,
            requirement: null,
            rule: null,
            details: {},
            observations: [],
            confidence: 0.84,
            risk_tier: 'low',
            source_refs: ['source_unit:onboarding:support'],
            source_evidence: [{
              ref: 'source_unit:business_source:onboarding:support:ingested:000',
              kind: 'text',
              label: 'Manba',
              unit_label: 'bo‘lak 000',
            }],
          }],
          rules: [],
          voice: [],
          integrations: [],
        },
      },
      isLoading: false,
      error: null,
      refetch,
    })
    mockedApi.post.mockResolvedValue({})

    renderOnboarding()
    mockedApi.post.mockClear()

    expect(screen.getByText('Mentor javobi')).toBeDefined()
    expect(screen.getByText('Mentorlar 24 soat ichida javob beradi.')).toBeDefined()
    expect(screen.getByText('Qo‘lda yozilgan ma’lumot · matn bo‘lagi')).toBeDefined()
    expect(screen.queryByText('Manba · bo‘lak 000')).toBeNull()

    fireEvent.click(screen.getAllByRole('button', { name: 'Tasdiqlash' })[0])

    await waitFor(() => {
      expect(mockedApi.post).toHaveBeenCalledWith('/api/onboarding/learned-review/actions', {
        action: 'approve',
        target_type: 'product',
        target_ref: 'catalog_product:binafsha-sumka',
      })
      expect(refetch).toHaveBeenCalled()
      expect(mockToastSuccess).toHaveBeenCalledWith('Tasdiqlandi')
    })

    mockedApi.post.mockClear()
    const approveButtons = screen.getAllByRole('button', { name: 'Tasdiqlash' })
    fireEvent.click(approveButtons[approveButtons.length - 1])

    await waitFor(() => {
      expect(mockedApi.post).toHaveBeenCalledWith('/api/onboarding/learned-review/actions', {
        action: 'approve',
        target_type: 'fact',
        target_ref: 'knowledge:mentor-sla',
      })
    })
  })

  it('lets the seller edit and merge learned product proposals from onboarding review', async () => {
    const refetch = vi.fn()
    mockUseAuth.mockReturnValue({
      user: {
        id: 7,
        workspace_id: 7,
        name: 'Nafis Liboslar',
        phone_number: '+998991234567',
        onboarding_completed: false,
      },
      refreshUser: mockRefreshUser,
    })
    mockUseTelegramConnectionStatus.mockReturnValue({
      data: { connected: true, needsReconnect: false },
      isLoading: false,
    })
    mockUseOnboardingRuntime.mockReturnValue({
      data: {
        schema_version: 'onboarding_runtime.v1',
        workspace_id: 7,
        state: 'running',
        phase: 'learning_sources',
        percent: 70,
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
          percent: 70,
          contacts_found: 548,
          customers_identified: 180,
          products_extracted: 2,
          knowledge_items: 0,
          voice_profile_ready: false,
          voice_discoveries: [],
          completed: false,
          errors: [],
        },
        source_learning: {
          schema_version: 'onboarding_source_learning.v1',
          status: 'needs_review',
          percent: 80,
          summary: {
            total: 1,
            learning: 0,
            learned: 1,
            needs_review: 1,
            missing: 0,
            conflict: 0,
            retrying: 0,
            failed: 0,
          },
          sources: [],
        },
        learned_review: {
          schema_version: 'onboarding_learned_review.v1',
          status: 'needs_review',
          summary: {
            products: 2,
            knowledge: 0,
            rules: 0,
            voice: 0,
            integrations: 0,
            media: 1,
            offers: 1,
            total_review_items: 2,
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
          knowledge: [],
          rules: [],
          voice: [],
          integrations: [],
        },
      },
      isLoading: false,
      error: null,
      refetch,
    })
    mockedApi.post.mockResolvedValue({})

    renderOnboarding()
    mockedApi.post.mockClear()

    fireEvent.click(screen.getAllByRole('button', { name: 'Tahrirlash' })[0])
    fireEvent.change(screen.getByLabelText('Mahsulot nomi'), {
      target: { value: 'Binafsha klassik sumka' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Tuzatib tasdiqlash' }))

    await waitFor(() => {
      expect(mockedApi.post).toHaveBeenCalledWith('/api/onboarding/learned-review/actions', {
        action: 'edit',
        target_type: 'fact',
        target_ref: 'catalog_product:binafsha-sumka',
        value_patch: { title: 'Binafsha klassik sumka' },
      })
    })

    mockedApi.post.mockClear()
    fireEvent.click(screen.getAllByRole('button', { name: 'Birlashtirish' })[0])

    await waitFor(() => {
      expect(mockedApi.post).toHaveBeenCalledWith('/api/onboarding/learned-review/actions', {
        action: 'merge',
        target_type: 'product',
        target_ref: 'catalog_product:binafsha-sumka',
        merge_into_ref: 'catalog_product:binafsha-sumka-copy',
      })
    })
  })

  it('keeps business basics, preferences, and source rules on separate onboarding screens', async () => {
    mockUseAuth.mockReturnValue({
      user: {
        id: 7,
        workspace_id: 7,
        name: '',
        phone_number: '+998991234567',
        onboarding_completed: false,
      },
      refreshUser: mockRefreshUser,
    })
    mockUseTelegramConnectionStatus.mockReturnValue({
      data: { connected: true, needsReconnect: false },
      isLoading: false,
    })
    mockUseOnboardingRuntime.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })
    mockedApi.post.mockResolvedValue({})

    renderOnboarding()
    mockedApi.post.mockClear()

    expect(screen.getByText('Manba qo‘shish')).toBeDefined()
    expect(screen.getByText('Telegram kanallari')).toBeDefined()
    expect(screen.queryByText(uz.onboarding.messageVolume)).toBeNull()
    expect(await screen.findByLabelText(uz.onboarding.telegramChannelSource)).toBeDefined()
    expect(screen.queryByLabelText(uz.onboarding.websiteSource)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Sayt/ }))
    expect(screen.getByLabelText(uz.onboarding.websiteSource)).toBeDefined()
    expect(screen.getByLabelText(uz.onboarding.fileSource)).toBeDefined()
    fireEvent.click(screen.getByRole('button', { name: /Ovozdan matn/ }))
    expect(screen.getByLabelText(uz.onboarding.sources)).toBeDefined()
    fireEvent.change(screen.getByLabelText(uz.onboarding.sources), {
      target: { value: 'Narx va yetkazish qoidalari shu yerda.' },
    })
    expect(screen.getAllByText('1 ta dalil navbatda').length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Hali o‘qilmadi/).length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: uz.onboarding.businessContinue }))

    // Document workbench sits between sources and the launch summary.
    expect(screen.getByRole('tab', { name: new RegExp(uz.onboarding.documents.tabBusiness) })).toBeDefined()
    fireEvent.click(screen.getByRole('button', { name: uz.onboarding.documents.continue }))

    // Launch summary is the final onboarding surface after the workbench.
    expect(screen.getByText(uz.onboarding.launch.title)).toBeDefined()
    expect(screen.getByText(uz.onboarding.launch.learnedTitle)).toBeDefined()
    expect(screen.getByText(uz.onboarding.launch.firstWorkTitle)).toBeDefined()
    expect(screen.getByRole('button', { name: new RegExp(uz.onboarding.launch.start) })).toBeDefined()
    expect(screen.getByRole('button', { name: uz.onboarding.launch.later })).toBeDefined()
  })

  it('summarizes learned documents and the 5 default agents on the launch screen', async () => {
    mockUseAuth.mockReturnValue({
      user: {
        id: 7,
        workspace_id: 7,
        name: '',
        phone_number: '+998991234567',
        onboarding_completed: false,
      },
      refreshUser: mockRefreshUser,
    })
    mockUseTelegramConnectionStatus.mockReturnValue({
      data: { connected: true, needsReconnect: false },
      isLoading: false,
    })
    mockUseOnboardingDocuments.mockReturnValue({
      data: {
        schema_version: 'onboarding_documents.v1',
        workspace_id: 7,
        running: false,
        current_doc: null,
        error: null,
        percent: 100,
        documents: {
          business: { total: 6, approved: 4, proposed: 1, generating: null, sections: [] },
          agent: { total: 4, approved: 3, proposed: 0, generating: null, sections: [] },
          skill: { status: 'proposed', candidates: 2 },
        },
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })
    mockedApi.post.mockResolvedValue({})

    renderOnboarding()

    fireEvent.click(screen.getByRole('button', { name: uz.onboarding.businessContinue }))
    // Document workbench → launch summary (the final onboarding surface).
    fireEvent.click(screen.getByRole('button', { name: uz.onboarding.documents.continue }))

    expect(screen.getByText(uz.onboarding.launch.title)).toBeDefined()
    expect(screen.getByText(uz.onboarding.launch.learnedReady(5, 6))).toBeDefined()
    expect(screen.getByText(uz.onboarding.launch.learnedReady(3, 4))).toBeDefined()
    expect(screen.getByText(uz.onboarding.launch.learnedCount(2))).toBeDefined()

    // All 5 default agents are listed.
    expect(screen.getByText(uz.onboarding.launch.agentSeller)).toBeDefined()
    expect(screen.getByText(uz.onboarding.launch.agentSupport)).toBeDefined()
    expect(screen.getByText(uz.onboarding.launch.agentCatalogUpdate)).toBeDefined()
    expect(screen.getByText(uz.onboarding.launch.agentFollowUp)).toBeDefined()
    expect(screen.getByText(uz.onboarding.launch.agentBi)).toBeDefined()
  })

  it('renders an honest empty launch summary when the documents projection is still empty', async () => {
    mockUseAuth.mockReturnValue({
      user: {
        id: 7,
        workspace_id: 7,
        name: '',
        phone_number: '+998991234567',
        onboarding_completed: false,
      },
      refreshUser: mockRefreshUser,
    })
    mockUseTelegramConnectionStatus.mockReturnValue({
      data: { connected: true, needsReconnect: false },
      isLoading: false,
    })
    mockUseOnboardingDocuments.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })
    mockedApi.post.mockResolvedValue({})

    renderOnboarding()

    fireEvent.click(screen.getByRole('button', { name: uz.onboarding.businessContinue }))
    // Document workbench → launch summary (the final onboarding surface).
    fireEvent.click(screen.getByRole('button', { name: uz.onboarding.documents.continue }))

    // With no projection yet, counts fall back to zero but the screen and its
    // launch actions stay usable.
    expect(screen.getByText(uz.onboarding.launch.title)).toBeDefined()
    expect(screen.getAllByText(uz.onboarding.launch.learnedReady(0, 0)).length).toBeGreaterThan(0)
    expect(screen.getByText(uz.onboarding.launch.learnedCount(0))).toBeDefined()
    expect(screen.getByRole('button', { name: new RegExp(uz.onboarding.launch.start) })).toBeDefined()
  })

  it('completes onboarding from the launch summary with launch_mode "start" and brain sources', async () => {
    mockUseAuth.mockReturnValue({
      user: {
        id: 7,
        workspace_id: 7,
        name: '',
        phone_number: '+998991234567',
        onboarding_completed: false,
      },
      refreshUser: mockRefreshUser,
    })
    mockUseTelegramConnectionStatus.mockReturnValue({
      data: { connected: true, needsReconnect: false },
      isLoading: false,
    })
    mockUseOnboardingRuntime.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    })
    mockedApi.post.mockResolvedValue({})

    renderOnboarding()
    mockedApi.post.mockClear()

    expect(screen.getByText('Manba qo‘shish')).toBeDefined()

    fireEvent.click(screen.getByRole('button', { name: /Ovozdan matn/ }))
    fireEvent.change(screen.getByLabelText(uz.onboarding.sources), {
      target: { value: 'Telegram kanal: @nafis_shop, katalog PDF bor' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Sayt/ }))
    fireEvent.change(screen.getByLabelText(uz.onboarding.websiteSource), {
      target: { value: 'https://nafis.example/shop' },
    })
    fireEvent.change(await screen.findByLabelText(uz.onboarding.telegramChannelSource), {
      target: { value: '@nafis_shop' },
    })
    fireEvent.change(screen.getByLabelText(uz.onboarding.telegramDateFrom), {
      target: { value: '2026-05-01' },
    })
    fireEvent.change(screen.getByLabelText(uz.onboarding.telegramDateTo), {
      target: { value: '2026-05-18' },
    })
    fireEvent.change(screen.getByLabelText(uz.onboarding.fileSource), {
      target: {
        files: [new File(['catalog'], 'catalog.pdf', { type: 'application/pdf' })],
      },
    })
    await waitFor(() => {
      expect(screen.getAllByText(/catalog.pdf/).length).toBeGreaterThan(0)
    })
    fireEvent.click(screen.getByRole('button', { name: uz.onboarding.businessContinue }))
    // Document workbench → launch summary. The owner is already authenticated via
    // Telegram, so the launch action finishes onboarding without a password step.
    fireEvent.click(await screen.findByRole('button', { name: uz.onboarding.documents.continue }))

    fireEvent.click(await screen.findByRole('button', { name: new RegExp(uz.onboarding.launch.start) }))

    await waitFor(() => {
      expect(mockedApi.post).toHaveBeenCalledWith('/api/auth/complete-onboarding', {
        name: undefined,
        category: 'retail',
        monthly_revenue_band: 'from_10m_to_50m',
        business_profile: {
          offer_summary: '',
          message_volume: '10_50',
          reply_team_size: 'owner_only',
          region: '',
          preferred_language: 'uzbek_latin',
          tone: 'short_warm',
        },
        preferences: {
          reply_mode: 'draft',
          permission_mode: 'ask_always',
          safe_autopilot: false,
          default_agents: ['seller', 'support', 'follow_up', 'catalog_update', 'bi'],
          escalation_destination: 'in_app',
          quiet_hours: {
            enabled: false,
            start: '22:00',
            end: '09:00',
          },
          add_phone_later: true,
          invite_team_later: true,
        },
        sources: {
          notes: 'Telegram kanal: @nafis_shop, katalog PDF bor',
          items: [
            {
              kind: 'text',
              label: uz.onboarding.manualBrainSource,
              text: 'Telegram kanal: @nafis_shop, katalog PDF bor',
              purpose: 'brain_data',
            },
            {
              kind: 'website',
              label: 'https://nafis.example/shop',
              url: 'https://nafis.example/shop',
              purpose: 'brain_data',
            },
            {
              kind: 'telegram_channel',
              label: '@nafis_shop',
              handle: '@nafis_shop',
              purpose: 'brain_data',
              date_from: '2026-05-01',
              date_to: '2026-05-18',
            },
            {
              kind: 'file',
              label: 'catalog.pdf',
              file_name: 'catalog.pdf',
              content_type: 'application/pdf',
              content_base64: 'Y2F0YWxvZw==',
              byte_size: 7,
              purpose: 'brain_data',
            },
          ],
        },
        owner_rules: {
          notes: '',
        },
        launch_mode: 'start',
      })
    })
    expect(mockRefreshUser).toHaveBeenCalled()
    expect(mockNavigate).toHaveBeenCalledWith({ to: '/conversations' })
  })

  it('does not trap the owner on launch when the OS rail refetch is slow', async () => {
    const slowWorkspaceRefetch = vi.fn(() => new Promise(() => {}))
    mockUseAuth.mockReturnValue({
      user: {
        id: 7,
        workspace_id: 7,
        name: 'SATStation',
        phone_number: '+998991234567',
        onboarding_completed: false,
      },
      refreshUser: mockRefreshUser,
    })
    mockUseTelegramConnectionStatus.mockReturnValue({
      data: { connected: true, needsReconnect: false },
      isLoading: false,
    })
    mockUseWorkspaceOS.mockReturnValue({
      data: undefined,
      refetch: slowWorkspaceRefetch,
    })
    mockedApi.post.mockResolvedValue({})

    renderOnboarding()

    fireEvent.click(screen.getByRole('button', { name: uz.onboarding.businessContinue }))
    // Document workbench → launch summary; the launch action must not await the
    // (here never-resolving) OS rail refetch before navigating.
    fireEvent.click(screen.getByRole('button', { name: uz.onboarding.documents.continue }))
    fireEvent.click(await screen.findByRole('button', { name: new RegExp(uz.onboarding.launch.start) }))

    await waitFor(() => {
      expect(mockRefreshUser).toHaveBeenCalled()
      expect(mockNavigate).toHaveBeenCalledWith({ to: '/conversations' })
    })
    expect(slowWorkspaceRefetch).toHaveBeenCalled()
  })
})
