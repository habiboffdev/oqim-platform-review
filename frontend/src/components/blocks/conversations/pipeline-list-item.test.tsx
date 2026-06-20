// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { ComponentProps } from 'react'

import { PipelineListItem } from './pipeline-list-item'

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, ...props }: ComponentProps<'a'>) => <a {...props}>{children}</a>,
}))

describe('PipelineListItem', () => {
  it('shows a pending reply indicator from canonical conversation data', () => {
    render(
      <PipelineListItem
        conversation={{
          id: 38,
          customer_id: 12,
          customer_name: 'Husnida',
          channel: 'telegram_dm',
          telegram_chat_id: 44,
          pipeline_stage: 'new',
          needs_attention: false,
          last_message_at: '2026-04-22T10:00:00Z',
          unread_count: 0,
          created_at: '2026-04-22T09:00:00Z',
          has_pending_reply: true,
        }}
      />,
    )

    expect(screen.getByTestId('pending-reply-indicator')).toBeTruthy()
  })
})
