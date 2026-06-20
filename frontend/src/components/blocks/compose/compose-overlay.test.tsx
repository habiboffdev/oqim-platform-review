// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement } from 'react'
import { ComposeOverlay } from './compose-overlay'
import { uz } from '@/lib/uz'
import type { SellerAgentReply } from '@/lib/types'

// Mock framer-motion
vi.mock('framer-motion', () => ({
  motion: {
    div: ({ children, ...props }: React.PropsWithChildren<Record<string, unknown>>) => {
      const { initial, animate, exit, transition, whileHover, whileTap, ...domProps } = props as Record<string, unknown>
      void initial; void animate; void exit; void transition; void whileHover; void whileTap
      return createElement('div', domProps as React.HTMLAttributes<HTMLDivElement>, children)
    },
    button: ({ children, ...props }: React.PropsWithChildren<Record<string, unknown>>) => {
      const { initial, animate, exit, transition, whileHover, whileTap, ...domProps } = props as Record<string, unknown>
      void initial; void animate; void exit; void transition; void whileHover; void whileTap
      return createElement('button', domProps as React.ButtonHTMLAttributes<HTMLButtonElement>, children)
    },
  },
  AnimatePresence: ({ children }: React.PropsWithChildren) => createElement('div', null, children),
}))

// Mock hooks that make API calls
vi.mock('@/hooks/use-seller-agent-replies', () => ({
  useApproveSellerAgentReply: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useEditSellerAgentReply: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useDismissSellerAgentReply: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}))

// Mock ReplyBubble to keep it simple
vi.mock('./reply-bubble', () => ({
  ReplyBubble: ({ text }: { text: string }) => createElement('div', { 'data-testid': 'reply-bubble' }, text),
}))

// Mock TypedChipBar
vi.mock('./typed-chip-bar', () => ({
  TypedChipBar: () => createElement('div', { 'data-testid': 'typed-chip-bar' }),
}))

// Mock ConfidenceBadge
vi.mock('./confidence-badge', () => ({
  ConfidenceBadge: ({ score }: { score: number }) =>
    createElement('span', { 'data-testid': 'confidence-badge' }, String(score)),
}))

// Mock Spinner
vi.mock('@/components/primitives/spinner', () => ({
  Spinner: () => createElement('div', { 'data-testid': 'spinner' }),
}))

import { useApproveSellerAgentReply, useEditSellerAgentReply, useDismissSellerAgentReply } from '@/hooks/use-seller-agent-replies'

const mockUseApproveReply = useApproveSellerAgentReply as ReturnType<typeof vi.fn>
const mockUseDismissReply = useDismissSellerAgentReply as ReturnType<typeof vi.fn>

const mockReply: SellerAgentReply = {
  id: 1,
  conversation_id: 5,
  confidence_score: 0.85,
  status: 'draft',
  draft_content: 'Ha, mahsulot bor',
  chips: null,
  split_messages: null,
  is_auto_sent: false,
  created_at: '2026-01-01T00:00:00Z',
}

describe('ComposeOverlay', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockUseApproveReply.mockReturnValue({ mutate: vi.fn(), isPending: false })
    mockUseDismissReply.mockReturnValue({ mutate: vi.fn(), isPending: false })
    ;(useEditSellerAgentReply as ReturnType<typeof vi.fn>).mockReturnValue({ mutate: vi.fn(), isPending: false })
  })

  it('renders the reply content in a ReplyBubble', () => {
    render(
      createElement(QueryClientProvider, { client: new QueryClient() },
        createElement(ComposeOverlay, { reply: mockReply })
      )
    )

    expect(screen.getByTestId('reply-bubble')).toBeDefined()
    expect(screen.getByText('Ha, mahsulot bor')).toBeDefined()
  })

  it('renders confidence badge with correct score', () => {
    render(
      createElement(QueryClientProvider, { client: new QueryClient() },
        createElement(ComposeOverlay, { reply: mockReply })
      )
    )

    const badge = screen.getByTestId('confidence-badge')
    expect(badge.textContent).toBe('0.85')
  })

  it('renders the send button in idle state', () => {
    render(
      createElement(QueryClientProvider, { client: new QueryClient() },
        createElement(ComposeOverlay, { reply: mockReply })
      )
    )

    expect(screen.getByText(uz.compose.send)).toBeDefined()
  })

  it('calls approve.mutate when send button clicked', () => {
    const mockMutate = vi.fn()
    mockUseApproveReply.mockReturnValue({ mutate: mockMutate, isPending: false })

    render(
      createElement(QueryClientProvider, { client: new QueryClient() },
        createElement(ComposeOverlay, { reply: mockReply })
      )
    )

    fireEvent.click(screen.getByText(uz.compose.send))
    expect(mockMutate).toHaveBeenCalledWith(mockReply.id)
  })

  it('shows dismiss reason selector when dismiss button clicked', () => {
    render(
      createElement(QueryClientProvider, { client: new QueryClient() },
        createElement(ComposeOverlay, { reply: mockReply })
      )
    )

    const dismissBtn = screen.getByLabelText(uz.compose.dismiss)
    fireEvent.click(dismissBtn)

    // Dismiss reasons should now be visible
    expect(screen.getByText(uz.compose.dismissReasons['bad_tone'])).toBeDefined()
    expect(screen.getByText(uz.compose.dismissReasons['incorrect_fact'])).toBeDefined()
  })

  it('calls dismiss.mutate with reason when a reason is clicked', () => {
    const mockDismissMutate = vi.fn()
    mockUseDismissReply.mockReturnValue({ mutate: mockDismissMutate, isPending: false })

    render(
      createElement(QueryClientProvider, { client: new QueryClient() },
        createElement(ComposeOverlay, { reply: mockReply })
      )
    )

    // Open dismiss panel
    const dismissBtn = screen.getByLabelText(uz.compose.dismiss)
    fireEvent.click(dismissBtn)

    // Click a reason
    fireEvent.click(screen.getByText(uz.compose.dismissReasons['bad_tone']))
    expect(mockDismissMutate).toHaveBeenCalledWith({ replyId: mockReply.id, reason: 'bad_tone' })
  })

  it('shows stale warning banner when isStale=true', () => {
    render(
      createElement(QueryClientProvider, { client: new QueryClient() },
        createElement(ComposeOverlay, {
          reply: mockReply,
          isStale: true,
          staleMessageCount: 2,
        })
      )
    )

    expect(screen.getByText(/yangi xabar keldi/i)).toBeDefined()
  })

  it('shows "sent" state when reply.status is sent', () => {
    const sentReply: SellerAgentReply = { ...mockReply, status: 'sent' }
    render(
      createElement(QueryClientProvider, { client: new QueryClient() },
        createElement(ComposeOverlay, { reply: sentReply })
      )
    )

    expect(screen.getByText(uz.compose.sent)).toBeDefined()
  })

  it('shows sending spinner when approve is pending', () => {
    mockUseApproveReply.mockReturnValue({ mutate: vi.fn(), isPending: true })

    render(
      createElement(QueryClientProvider, { client: new QueryClient() },
        createElement(ComposeOverlay, { reply: mockReply })
      )
    )

    expect(screen.getByText(uz.compose.sending)).toBeDefined()
  })
})
