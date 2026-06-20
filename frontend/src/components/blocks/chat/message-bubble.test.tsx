// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'

import { MessageBubble } from './message-bubble'
import { uz } from '@/lib/uz'
import type { Message } from '@/lib/types'

function sellerMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: 11,
    conversation_id: 38,
    sender_type: 'seller',
    content: 'Salom',
    channel: 'telegram_dm',
    is_read: false,
    created_at: '2026-05-04T10:00:00Z',
    ...overrides,
  }
}

function renderBubble(message: Message) {
  render(
    <MessageBubble
      message={message}
      position="single"
      messageMap={new Map()}
    />,
  )
}

describe('MessageBubble delivery status', () => {
  it('shows uncertain delivery instead of a sent tick while echo reconciliation is pending', () => {
    renderBubble(sellerMessage({
      delivery_state: 'unknown',
      delivery_runtime: {
        schema_version: 'delivery_runtime.v1',
        state: 'unknown',
        customer_status: 'uncertain',
        next_action: 'reconcile',
        is_terminal: false,
        requires_reconciliation: true,
        can_retry: false,
        attempt_count: 1,
        max_attempts: 3,
        retry_budget_remaining: 2,
      },
    }))

    expect(screen.getByRole('img', { name: uz.conversations.deliveryUncertain })).toBeTruthy()
    expect(screen.getByText(uz.conversations.deliveryUncertain)).toBeTruthy()
  })

  it('shows failed delivery only for terminal failed runtime state', () => {
    renderBubble(sellerMessage({
      delivery_state: 'failed',
      delivery_runtime: {
        schema_version: 'delivery_runtime.v1',
        state: 'failed',
        customer_status: 'failed',
        next_action: 'retry',
        is_terminal: true,
        requires_reconciliation: false,
        can_retry: true,
        attempt_count: 3,
        max_attempts: 3,
        retry_budget_remaining: 0,
      },
    }))

    expect(screen.getByRole('img', { name: uz.conversations.failed })).toBeTruthy()
    expect(screen.getByText(uz.conversations.failed)).toBeTruthy()
  })

  it('labels confirmed and read states explicitly for assistive and testable status', () => {
    renderBubble(sellerMessage({
      delivery_state: 'confirmed',
      is_read: false,
    }))

    expect(screen.getByRole('img', { name: uz.conversations.sent })).toBeTruthy()
  })

  it('labels read messages separately from Telegram send confirmation', () => {
    renderBubble(sellerMessage({
      delivery_state: 'confirmed',
      is_read: true,
    }))

    expect(screen.getByRole('img', { name: uz.conversations.read })).toBeTruthy()
  })

  it('shows pending delivery text while the send request is still active', () => {
    renderBubble(sellerMessage({
      delivery_state: 'pending',
      delivery_runtime: {
        schema_version: 'delivery_runtime.v1',
        state: 'sending',
        customer_status: 'sending',
        next_action: 'wait',
        is_terminal: false,
        requires_reconciliation: false,
        can_retry: false,
        attempt_count: 1,
        max_attempts: 3,
        retry_budget_remaining: 2,
      },
    }))

    expect(screen.getByRole('img', { name: uz.conversations.sending })).toBeTruthy()
    expect(screen.getByText(uz.conversations.sending)).toBeTruthy()
  })
})
