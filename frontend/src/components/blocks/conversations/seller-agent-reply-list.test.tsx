// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ReactNode } from 'react'
import { SellerAgentReplyList } from './seller-agent-reply-list'
import { uz } from '@/lib/uz'
import { GREEN_CONFIDENCE_THRESHOLD } from '@/lib/constants'
import type { SellerAgentReply } from '@/lib/types'

// Mock ChatViewer
vi.mock('@/components/blocks/chat/chat-viewer', () => ({
  ChatViewer: () => createElement('div', { 'data-testid': 'chat-viewer' }),
}))

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, ...props }: React.PropsWithChildren<Record<string, unknown>>) =>
    createElement('a', props as React.AnchorHTMLAttributes<HTMLAnchorElement>, children),
}))

// Mock hooks
vi.mock('@/hooks/use-seller-agent-reply-inbox', () => ({
  useSellerAgentReplyInbox: vi.fn(),
}))

vi.mock('@/hooks/use-seller-agent-replies', () => ({
  useApproveSellerAgentReply: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useDismissSellerAgentReply: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useEditSellerAgentReply: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useRegenerateSellerAgentReply: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}))

import { useSellerAgentReplyInbox } from '@/hooks/use-seller-agent-reply-inbox'

const mockUseSellerAgentReplyInbox = useSellerAgentReplyInbox as ReturnType<typeof vi.fn>

const makeReply = (id: number, confidence: number): SellerAgentReply => ({
  id,
  conversation_id: id * 10,
  confidence_score: confidence,
  status: 'draft',
  draft_content: `Reply text ${id}`,
  chips: null,
  split_messages: null,
  is_auto_sent: false,
  customer_name: `Customer ${id}`,
  learning_runtime: {
    schema_version: 'seller_agent_learning_runtime.v1',
    state: 'not_applicable',
    next_action: 'none',
  },
  created_at: '2026-01-01T00:00:00Z',
})

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: queryClient }, children)
}

describe('SellerAgentReplyList', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows loading skeletons while data is loading', () => {
    mockUseSellerAgentReplyInbox.mockReturnValue({ data: undefined, isLoading: true })

    const Wrapper = createWrapper()
    const { container } = render(
      createElement(Wrapper, null,
        createElement(SellerAgentReplyList, { search: '' })
      )
    )

    const skeletons = container.querySelectorAll('[data-slot="skeleton"]')
    expect(skeletons.length).toBeGreaterThan(0)
  })

  it('shows empty state when no replies', () => {
    mockUseSellerAgentReplyInbox.mockReturnValue({ data: [], isLoading: false })

    const Wrapper = createWrapper()
    render(
      createElement(Wrapper, null,
        createElement(SellerAgentReplyList, { search: '' })
      )
    )

    expect(screen.getByText(uz.replies.empty)).toBeTruthy()
  })

  it('renders reply rows when replies exist', () => {
    const replies = [makeReply(1, 0.9), makeReply(2, 0.5)]
    mockUseSellerAgentReplyInbox.mockReturnValue({ data: replies, isLoading: false })

    const Wrapper = createWrapper()
    render(
      createElement(Wrapper, null,
        createElement(SellerAgentReplyList, { search: '' })
      )
    )

    expect(screen.getAllByText('Customer 1').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Customer 2').length).toBeGreaterThan(0)
  })

  it('shows learned only from seller agent learning runtime learned state', () => {
    const replies = [
      {
        ...makeReply(1, 0.9),
        learning_runtime: {
          schema_version: 'seller_agent_learning_runtime.v1' as const,
          state: 'learned' as const,
          signal_id: 7,
          next_action: 'none' as const,
        },
      },
      makeReply(2, 0.3),
    ]
    mockUseSellerAgentReplyInbox.mockReturnValue({ data: replies, isLoading: false })

    const Wrapper = createWrapper()
    render(
      createElement(Wrapper, null,
        createElement(SellerAgentReplyList, { search: '' })
      )
    )

    expect(screen.getAllByText("O'rganildi").length).toBeGreaterThan(0)
    expect(screen.getByText('dalil saqlandi')).toBeTruthy()
  })

  it('shows failed learning as degraded without claiming learned', () => {
    const replies = [
      {
        ...makeReply(1, 0.8),
        learning_runtime: {
          schema_version: 'seller_agent_learning_runtime.v1' as const,
          state: 'failed' as const,
          next_action: 'retry' as const,
          last_error: 'index unavailable',
        },
      },
    ]
    mockUseSellerAgentReplyInbox.mockReturnValue({ data: replies, isLoading: false })

    const Wrapper = createWrapper()
    render(
      createElement(Wrapper, null,
        createElement(SellerAgentReplyList, { search: '' })
      )
    )

    expect(screen.queryByText("O'rganildi")).toBeNull()
    expect(screen.getAllByText("O'rganish xatosi").length).toBeGreaterThan(0)
    expect(screen.getByText('O‘rganish to‘xtadi. Tahrirni yana yuborsangiz, AI qayta urinadi.')).toBeTruthy()
  })

  it('filters replies by search text', () => {
    const replies = [makeReply(1, 0.9), makeReply(2, 0.8)]
    mockUseSellerAgentReplyInbox.mockReturnValue({ data: replies, isLoading: false })

    const Wrapper = createWrapper()
    render(
      createElement(Wrapper, null,
        createElement(SellerAgentReplyList, { search: 'Customer 1' })
      )
    )

    expect(screen.getAllByText('Customer 1').length).toBeGreaterThan(0)
    // Customer 2 should not be visible
    expect(screen.queryByText('Customer 2')).toBeNull()
  })

  it('sorts replies by confidence_score descending', () => {
    const replies = [makeReply(1, 0.4), makeReply(2, 0.95), makeReply(3, 0.7)]
    mockUseSellerAgentReplyInbox.mockReturnValue({ data: replies, isLoading: false })

    const Wrapper = createWrapper()
    const { container } = render(
      createElement(Wrapper, null,
        createElement(SellerAgentReplyList, { search: '' })
      )
    )

    const customerNames = Array.from(container.querySelectorAll('aside button'))
      .map((el) => el.textContent)
      .filter((text): text is string => Boolean(text))

    // Highest confidence first: Customer 2 (0.95) → Customer 3 (0.7) → Customer 1 (0.4)
    expect(customerNames[0]).toContain('Customer 2')
    expect(customerNames[1]).toContain('Customer 3')
    expect(customerNames[2]).toContain('Customer 1')
  })

  it('GREEN_CONFIDENCE_THRESHOLD constant is 0.7', () => {
    expect(GREEN_CONFIDENCE_THRESHOLD).toBe(0.7)
  })
})
