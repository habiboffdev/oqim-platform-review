// @vitest-environment jsdom
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'

import { KanbanCard } from './kanban-card'
import { uz } from '@/lib/uz'

describe('KanbanCard', () => {
  it('uses backend crm pipeline attention state for its follow-up affordance', () => {
    render(
      <KanbanCard
        card={{
          conversation_id: 7,
          customer_id: 11,
          customer_name: 'Aziza',
          channel: 'telegram_dm',
          stage: {
            schema_version: 'crm_stage.v1',
            stage: 'qualified',
            source: 'crm_state',
            products_interested: [],
            needs_attention: true,
            field_provenance: {},
          },
          needs_attention: true,
          last_message_at: '2026-04-22T10:00:00Z',
          unread_count: 0,
          has_pending_reply: false,
        }}
      />,
    )

    expect(screen.getByTestId('follow-up-indicator')).toBeTruthy()
    expect(screen.getByText(uz.pipeline.needsFollowup)).toBeTruthy()
  })

  it('explains who set the stage instead of showing only an opaque column', () => {
    render(
      <KanbanCard
        card={{
          conversation_id: 8,
          customer_id: 12,
          customer_name: 'Bekzod',
          channel: 'telegram_dm',
          stage: {
            schema_version: 'crm_stage.v1',
            stage: 'payment',
            source: 'crm_state',
            confidence: 0.87,
            last_intent: 'payment_proof',
            products_interested: ['Premium kurs'],
            needs_attention: false,
            field_provenance: { pipeline_stage: 'ai' },
          },
          needs_attention: false,
          last_message_text: "To'lov qildim",
          last_message_at: '2026-04-22T10:00:00Z',
          unread_count: 0,
          has_pending_reply: true,
        }}
      />,
    )

    expect(screen.getByText(new RegExp(uz.pipeline.stageBy.ai))).toBeTruthy()
    expect(screen.getByText(/87% ishonch/)).toBeTruthy()
    expect(screen.getByText('Niyat: payment proof')).toBeTruthy()
    expect(screen.getByText('Premium kurs')).toBeTruthy()
    expect(screen.getByText(uz.pipeline.hasReply)).toBeTruthy()
  })

  it('shows when OQIM has not learned a stage yet instead of exposing backend defaults', () => {
    render(
      <KanbanCard
        card={{
          conversation_id: 10,
          customer_id: 14,
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
          needs_attention: false,
          last_message_text: 'Assalomu alaykum',
          last_message_at: '2026-04-22T10:00:00Z',
          unread_count: 0,
          has_pending_reply: false,
        }}
      />,
    )

    expect(screen.getByText(uz.pipeline.defaultedStage)).toBeTruthy()
    expect(screen.queryByText('Dalil kam')).toBeNull()
  })

  it('uses a readable fallback initial for symbol-only names', () => {
    render(
      <KanbanCard
        card={{
          conversation_id: 9,
          customer_id: 13,
          customer_name: '🍀',
          channel: 'telegram_dm',
          stage: {
            schema_version: 'crm_stage.v1',
            stage: 'new',
            source: 'crm_state',
            products_interested: [],
            needs_attention: false,
            field_provenance: {},
          },
          needs_attention: false,
          last_message_text: 'Xabar yo‘q',
          last_message_at: '2026-04-22T10:00:00Z',
          unread_count: 0,
          has_pending_reply: false,
        }}
      />,
    )

    expect(screen.getByText('M')).toBeTruthy()
    expect(screen.queryByText('Holatdan olindi')).toBeNull()
  })
})
