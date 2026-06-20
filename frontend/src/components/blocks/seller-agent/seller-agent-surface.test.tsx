// @vitest-environment jsdom
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { Conversation } from '@/lib/types'
import { uz } from '@/lib/uz'

const latestReplyMock = vi.fn()
const actionInboxMock = vi.fn()
const catalogMock = vi.fn()
const shimmerMock = vi.fn()

vi.mock('@/hooks/use-seller-agent-replies', () => ({
  useLatestSellerAgentReply: (conversationId: number | undefined) => latestReplyMock(conversationId),
}))

vi.mock('@/hooks/use-action-runtime', () => ({
  useActionRuntimeInbox: () => actionInboxMock(),
  useProcessActionProposal: () => ({ isPending: false, mutate: vi.fn() }),
  useApproveActionProposal: () => ({ isPending: false, mutate: vi.fn() }),
  useRejectActionProposal: () => ({ isPending: false, mutate: vi.fn() }),
  useExecuteActionProposal: () => ({ isPending: false, mutate: vi.fn() }),
}))

vi.mock('@/hooks/use-business-brain', () => ({
  useBrainCatalog: () => catalogMock(),
}))

vi.mock('@/hooks/use-websocket', () => ({
  useShimmerState: () => shimmerMock(),
}))

vi.mock('@/components/blocks/seller-agent/seller-agent-reply-review-card', () => ({
  SellerAgentReplyReviewCard: ({ reply }: { reply: { id: number; draft_content: string } }) => (
    <div data-testid="reply-card">{reply.draft_content}</div>
  ),
}))

import { SellerAgentSurface } from './seller-agent-surface'

function conversation(overrides: Partial<Conversation> = {}): Conversation {
  return {
    id: 42,
    customer_id: 7,
    customer_name: 'Aris',
    channel: 'telegram_dm',
    telegram_chat_id: 777001,
    pipeline_stage: 'qualified',
    needs_attention: true,
    last_message_at: '2026-05-08T10:00:00Z',
    unread_count: 1,
    created_at: '2026-05-08T09:00:00Z',
    crm_stage: {
      schema_version: 'crm_stage.v1',
      stage: 'qualified',
      source: 'crm_state',
      products_interested: ['Ring'],
      needs_attention: true,
      field_provenance: {},
    },
    next_best_action: {
      action: 'seller_reply',
      ready: true,
      reason: 'customer_asked_price',
    },
    ...overrides,
  }
}

describe('SellerAgentSurface', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    latestReplyMock.mockReturnValue(null)
    actionInboxMock.mockReturnValue({ data: { items: [] }, isError: false })
    catalogMock.mockReturnValue({
      data: {
        products: [
          {
            schema_version: 'catalog_workspace_product.v1',
            product_ref: 'catalog_product:ring',
            product: { title: 'Ring' },
            variants: [],
            offers: [],
            media: [{ source_ref: 'source:photo:1' }],
            source_refs: ['source:pdf:1'],
            conflict_refs: [],
            index_state: 'ready',
            extraction_state: 'available',
          },
        ],
      },
    })
    shimmerMock.mockReturnValue(new Set())
  })

  it('renders seller-facing context labels instead of internal architecture names', () => {
    render(<SellerAgentSurface conversation={conversation()} />)

    expect(screen.getByText('Aris')).toBeTruthy()
    expect(screen.getByText(uz.workspaceUi.conversations.sellerAgent)).toBeTruthy()
    expect(screen.getByText('Suhbat')).toBeTruthy()
    expect(screen.getByText('Mijoz holati')).toBeTruthy()
    expect(screen.getByText('Biznes ma’lumoti')).toBeTruthy()
    expect(screen.getByText('1 mahsulot · 1 manba')).toBeTruthy()
    expect(screen.getByText('1 rasmli mahsulot')).toBeTruthy()
    expect(screen.queryByText('Seller Agent')).toBeNull()
    expect(screen.queryByText('Business Brain')).toBeNull()
  })

  it('renders the current reply as the reply decision', () => {
    latestReplyMock.mockReturnValue({
      id: 12,
      conversation_id: 42,
      confidence_score: 0.91,
      status: 'draft',
      draft_content: 'Ha, bor. Qaysi o‘lcham kerak?',
      chips: null,
      split_messages: null,
      is_auto_sent: false,
      created_at: '2026-05-08T10:00:00Z',
    })

    render(<SellerAgentSurface conversation={conversation()} />)

    expect(screen.getByTestId('reply-card').textContent).toContain('Ha, bor')
  })

  it('shows media learning copy only while a media-grounded reply is blocked', () => {
    const { unmount } = render(
      <SellerAgentSurface
        conversation={conversation({
          next_best_action: {
            action: 'seller_reply',
            ready: false,
            reason: 'waiting_on_media_hydration',
          },
        })}
      />,
    )

    expect(screen.getByText(/Media o‘rganilmoqda/)).toBeTruthy()
    unmount()

    latestReplyMock.mockReturnValue({
      id: 12,
      conversation_id: 42,
      confidence_score: 0.91,
      status: 'draft',
      draft_content: 'Ha, shu model bor.',
      chips: null,
      split_messages: null,
      is_auto_sent: false,
      created_at: '2026-05-08T10:00:00Z',
    })

    render(
      <SellerAgentSurface
        conversation={conversation({
          next_best_action: {
            action: 'seller_reply',
            ready: false,
            reason: 'waiting_on_media_hydration',
          },
        })}
      />,
    )

    expect(screen.getByText(/Javob tayyor/)).toBeTruthy()
    expect(screen.queryByText(/Media o‘rganilmoqda/)).toBeNull()
  })

  it('surfaces incomplete chat history without route-time repair', () => {
    render(
      <SellerAgentSurface
        conversation={conversation({
          hydration: {
            schema_version: 'conversation_hydration_runtime.v1',
            needed: true,
            state: 'pending',
            reason: 'history_gap',
            can_retry: true,
            attempt_count: 0,
            max_attempts: 3,
            requested_count: 0,
            persisted_count: 0,
            duplicate_count: 0,
          },
        })}
      />,
    )

    expect(screen.getByText('Suhbat tarixi to‘liq emas')).toBeTruthy()
    expect(screen.getByText(/kanonik xabarlarga/)).toBeTruthy()
  })

  it('filters action proposals to the active conversation', () => {
    actionInboxMock.mockReturnValue({
      data: {
        items: [
          {
            schema_version: 'commercial_action_proposal.v2',
            proposal_id: 'proposal-visible',
            workspace_id: 1,
            conversation_id: 42,
            customer_id: 7,
            action_type: 'follow_up_schedule',
            lifecycle_state: 'waiting_approval',
            execution_mode: 'manual',
            risk_level: 'low',
            requires_approval: true,
            priority: 'normal',
            confidence: 0.82,
            reason_code: 'customer_went_cold_after_price',
            source_refs: ['message:42:9001'],
            payload: { message_goal: 'Ertaga muloyim qayta aloqa yuborish' },
            idempotency_key: 'proposal-visible',
          },
          {
            schema_version: 'commercial_action_proposal.v2',
            proposal_id: 'proposal-hidden',
            workspace_id: 1,
            conversation_id: 99,
            customer_id: 9,
            action_type: 'seller_reply',
            lifecycle_state: 'waiting_approval',
            execution_mode: 'manual',
            risk_level: 'low',
            requires_approval: true,
            priority: 'normal',
            confidence: 0.8,
            reason_code: 'needs_seller_confirmation',
            source_refs: [],
            payload: { message_goal: 'Hidden proposal' },
            idempotency_key: 'proposal-hidden',
          },
        ],
      },
      isError: false,
    })

    render(<SellerAgentSurface conversation={conversation()} />)

    expect(screen.getByText('Ertaga muloyim qayta aloqa yuborish')).toBeTruthy()
    expect(screen.getByText(uz.workspaceUi.conversations.conversationEvidence)).toBeTruthy()
    expect(screen.queryByText('42:9001')).toBeNull()
    expect(screen.getByText(uz.workspaceUi.conversations.actionProposals)).toBeTruthy()
    expect(screen.queryByText('Action takliflari')).toBeNull()
    expect(screen.queryByText('Hidden proposal')).toBeNull()
  })
})
