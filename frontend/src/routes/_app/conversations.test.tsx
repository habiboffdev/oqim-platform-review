// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const infiniteConversationsMock = vi.fn()
const conversationDetailMock = vi.fn()
const pipelineMock = vi.fn()
const routeMock = vi.hoisted(() => ({
  params: { conversationId: '38' } as { conversationId?: string },
  location: { pathname: '/conversations/38' },
  search: {} as { mode?: string },
}))

vi.mock('@tanstack/react-router', () => ({
  useParams: () => routeMock.params,
  useLocation: () => routeMock.location,
  useSearch: () => routeMock.search,
  useNavigate: () => vi.fn(),
}))

vi.mock('@/hooks/use-conversations', () => ({
  useInfiniteConversations: () => infiniteConversationsMock(),
  useConversation: () => conversationDetailMock(),
}))

vi.mock('@/hooks/use-seller-agent-reply-inbox', () => ({
  useSellerAgentReplyInbox: () => ({ data: [] }),
}))

vi.mock('@/hooks/use-pipeline', () => ({
  usePipeline: () => pipelineMock(),
  useUpdatePipelineStage: () => ({ mutate: vi.fn() }),
}))

vi.mock('@/components/blocks/pipeline/kanban-board', () => ({
  KanbanBoard: () => <div data-testid="kanban-board" />,
}))

vi.mock('@/components/blocks/conversation-list', () => ({
  ConversationList: () => <div data-testid="conversation-list" />,
}))

vi.mock('@/components/blocks/chat/chat-viewer', () => ({
  ChatViewer: ({ conversationId }: { conversationId?: number }) => (
    <div data-testid="chat-viewer">{conversationId ?? 'none'}</div>
  ),
}))

vi.mock('@/components/blocks/chat/compose-box', () => ({
  ComposeBox: ({ conversationId }: { conversationId: number }) => (
    <div data-testid="compose-box">{conversationId}</div>
  ),
}))

vi.mock('@/components/blocks/seller-agent/seller-agent-surface', () => ({
  SellerAgentSurface: ({ conversation }: { conversation?: { id: number } | null }) => (
    <div data-testid="seller-agent-surface">{conversation?.id ?? 'none'}</div>
  ),
}))

vi.mock('@/components/primitives/search-input', () => ({
  SearchInput: () => <input aria-label="search" />,
}))

import { ConversationsPage } from './conversations'
import { uz } from '@/lib/uz'

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ConversationsPage />
    </QueryClientProvider>,
  )
}

describe('ConversationsPage route authority', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    routeMock.params = { conversationId: '38' }
    routeMock.location = { pathname: '/conversations/38' }
    routeMock.search = {}
    const conversation = {
      id: 38,
      customer_id: 38,
      customer_name: 'Husnida Akhrorkulova',
      channel: 'telegram_dm',
      telegram_chat_id: 6723503799,
      pipeline_stage: 'new',
      crm_snapshot: {
        pipeline_stage: 'manual_override_should_not_render',
        lead_score: 92,
        last_intent: 'purchase',
        products_interested: ['Atlas'],
        urgency: true,
        needs_attention: false,
        last_updated: '2026-04-15T14:34:21Z',
      },
      crm_stage: {
        schema_version: 'crm_stage.v1',
        stage: 'won',
        source: 'crm_state',
        products_interested: ['Atlas'],
        needs_attention: false,
        field_provenance: {},
      },
      needs_attention: false,
      last_message_at: '2026-04-15T14:34:21Z',
      unread_count: 0,
      created_at: '2026-04-15T01:32:54Z',
      last_message_text: 'in instagram?',
      contact_type: 'unknown',
      has_pending_reply: false,
    }

    infiniteConversationsMock.mockReturnValue({
      data: {
        pages: [{ items: [conversation] }],
      },
      isLoading: false,
      error: null,
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage: vi.fn(),
    })
    conversationDetailMock.mockReturnValue({
      data: conversation,
      error: null,
    })
    pipelineMock.mockReturnValue({ data: undefined, isLoading: false, isError: false })
  })

  it('opens the route conversation as the active chat on first render', () => {
    renderPage()

    expect(screen.getAllByText('Husnida Akhrorkulova').length).toBeGreaterThan(0)
    expect(screen.getByTestId('chat-viewer').textContent).toBe('38')
    expect(screen.getByTestId('compose-box').textContent).toBe('38')
  })

  it('uses canonical crm_stage.v1 for the active header', () => {
    renderPage()

    expect(screen.getByText('Buyurtma')).toBeTruthy()
    expect(screen.queryByText('manual_override_should_not_render')).toBeNull()
  })

  it('uses seller-facing Uzbek copy instead of internal operator labels', () => {
    renderPage()

    expect(screen.getByText(uz.workspaceUi.conversations.conversationsDescription)).toBeTruthy()
    expect(screen.getByText(uz.workspaceUi.conversations.activeConversation)).toBeTruthy()
    expect(screen.getByText(uz.workspaceUi.conversations.loadedCount(1))).toBeTruthy()
    expect(screen.getByText(uz.workspaceUi.conversations.sellerAgent)).toBeTruthy()
    expect(screen.queryByText('Canonical chat tail')).toBeNull()
    expect(screen.queryByText('Open conversation')).toBeNull()
    expect(screen.queryByText('1 loaded')).toBeNull()
    expect(screen.queryByText('Seller Agent')).toBeNull()
  })

  it('does not render a stale active thread when the authoritative list no longer contains it', () => {
    infiniteConversationsMock.mockReturnValue({
      data: { pages: [{ items: [] }] },
      isLoading: false,
      error: null,
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage: vi.fn(),
    })
    conversationDetailMock.mockReturnValue({
      data: undefined,
      error: new Error('Not found'),
    })

    renderPage()

    expect(screen.queryByText('Husnida Akhrorkulova')).toBeNull()
    expect(screen.queryByTestId('chat-viewer')).toBeNull()
    expect(screen.queryByTestId('compose-box')).toBeNull()
  })

  it('renders the active route from authoritative detail even when it is not in the loaded list page', () => {
    infiniteConversationsMock.mockReturnValue({
      data: { pages: [{ items: [] }] },
      isLoading: false,
      error: null,
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage: vi.fn(),
    })

    renderPage()

    expect(screen.getAllByText('Husnida Akhrorkulova').length).toBeGreaterThan(0)
    expect(screen.getByTestId('chat-viewer').textContent).toBe('38')
    expect(screen.getByTestId('compose-box').textContent).toBe('38')
  })

  it('uses the browser path over stale router params for the active thread', () => {
    routeMock.params = { conversationId: '12' }
    routeMock.location = { pathname: '/conversations/38' }

    renderPage()

    expect(screen.getByTestId('chat-viewer').textContent).toBe('38')
    expect(screen.getByTestId('compose-box').textContent).toBe('38')
  })

  it('explains customer learning state in pipeline mode', () => {
    routeMock.search = { mode: 'pipeline' }
    pipelineMock.mockReturnValue({
      data: {
        schema_version: 'crm_pipeline.v1',
        total: 151,
        stages: [
          {
            stage: 'new',
            count: 150,
            cards: [
              {
                conversation_id: 1,
                customer_id: 1,
                customer_name: 'Madina',
                channel: 'telegram_dm',
                stage: {
                  schema_version: 'crm_stage.v1',
                  stage: 'new',
                  source: 'defaulted',
                  products_interested: [],
                  needs_attention: false,
                  field_provenance: {},
                },
                unread_count: 0,
                has_pending_reply: false,
                needs_attention: false,
              },
            ],
          },
          {
            stage: 'payment',
            count: 1,
            cards: [
              {
                conversation_id: 2,
                customer_id: 2,
                customer_name: 'Bekzod',
                channel: 'telegram_dm',
                stage: {
                  schema_version: 'crm_stage.v1',
                  stage: 'payment',
                  source: 'crm_state',
                  confidence: 0.84,
                  products_interested: [],
                  needs_attention: true,
                  field_provenance: { pipeline_stage: 'ai' },
                },
                unread_count: 0,
                has_pending_reply: false,
                needs_attention: true,
              },
            ],
          },
        ],
      },
      isLoading: false,
      isError: false,
    })

    renderPage()

    expect(screen.getByText('Mijozlar holati')).toBeTruthy()
    expect(screen.getByText('1/151 suhbat joyiga qo‘yildi')).toBeTruthy()
    expect(screen.getByText('1 ta holatni ko‘rish kerak')).toBeTruthy()
    expect(screen.getByText('50 mijoz')).toBeTruthy()
    expect(screen.getByTestId('kanban-board')).toBeTruthy()
  })
})
