// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { createElement } from 'react'
import { TypedChipBar } from './typed-chip-bar'
import { uz } from '@/lib/uz'

vi.mock('framer-motion', () => ({
  motion: {
    div: ({ children, ...props }: React.PropsWithChildren<Record<string, unknown>>) => {
      const { initial, animate, exit, transition, ...domProps } = props as Record<string, unknown>
      void initial; void animate; void exit; void transition
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

const mockRegenerate = vi.fn()

vi.mock('@/hooks/use-seller-agent-replies', () => ({
  useApproveSellerAgentReply: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useApproveAndStage: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useRegenerateSellerAgentReply: vi.fn(() => ({ mutate: mockRegenerate, isPending: false })),
  useSendQuickReply: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useDismissSellerAgentReply: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}))

describe('TypedChipBar custom regenerate', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('keeps custom instruction visible when regenerate does not succeed', () => {
    render(
      <TypedChipBar
        chips={[]}
        replyId={10}
        conversationId={20}
        isRegenerating={false}
      />,
    )

    fireEvent.click(screen.getByText(uz.compose.customInstruction))
    const input = screen.getByLabelText(uz.compose.customInstruction) as HTMLInputElement
    fireEvent.change(input, { target: { value: 'Qisqaroq yoz' } })
    fireEvent.click(screen.getByLabelText(uz.compose.send))

    expect(mockRegenerate).toHaveBeenCalled()
    expect(screen.getByLabelText(uz.compose.customInstruction)).toBeDefined()
    expect((screen.getByLabelText(uz.compose.customInstruction) as HTMLInputElement).value).toBe('Qisqaroq yoz')
  })

  it('clears custom instruction only after regenerate succeeds', () => {
    mockRegenerate.mockImplementation(
      (
        _vars: unknown,
        options?: { onSuccess?: () => void },
      ) => {
        options?.onSuccess?.()
      },
    )

    render(
      <TypedChipBar
        chips={[]}
        replyId={10}
        conversationId={20}
        isRegenerating={false}
      />,
    )

    fireEvent.click(screen.getByText(uz.compose.customInstruction))
    fireEvent.change(screen.getByLabelText(uz.compose.customInstruction), {
      target: { value: "Narxni qo'sh" },
    })
    fireEvent.click(screen.getByLabelText(uz.compose.send))

    expect(screen.queryByLabelText(uz.compose.customInstruction)).toBeNull()
  })
})
