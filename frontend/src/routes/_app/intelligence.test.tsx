// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import type { CommercialActionProposal } from '@/lib/types'

let actionItems: CommercialActionProposal[]

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, to, params, className }: {
    children: ReactNode
    to: string
    params?: Record<string, string>
    className?: string
  }) => (
    <a href={params?.conversationId ? `${to}/${params.conversationId}` : to} className={className}>
      {children}
    </a>
  ),
  useSearch: () => ({ tab: 'customer' }),
  useNavigate: () => () => {},
}))

vi.mock('@/hooks/use-business-brain', () => ({
  useBusinessBrainFacts: () => ({ data: { items: [] }, isLoading: false }),
}))

vi.mock('@/hooks/use-bi-promoter', () => ({
  useBIAnalyticsDashboard: () => ({
    data: {
      schema_version: 'bi_analytics_dashboard.v1',
      workspace_id: 1,
      freshness: 'projection_current',
      summary: {},
      insights: [],
      breakdowns: { products: [], channels: [] },
      source_refs: [],
    },
    error: null,
    isLoading: false,
  }),
  usePromoterPolicy: () => ({
    data: { enabled: true, approved: false },
  }),
  useBIInvestigationMutation: () => ({
    mutate: vi.fn(),
    isPending: false,
    data: null,
  }),
  usePromoterPlanMutation: () => ({
    mutate: vi.fn(),
    isPending: false,
    data: null,
  }),
}))

vi.mock('@/hooks/use-action-runtime', () => ({
  useActionRuntimeInbox: () => ({
    data: { schema_version: 'action_runtime_inbox.v1', workspace_id: 1, items: actionItems },
    isLoading: false,
  }),
}))

vi.mock('@/hooks/use-customers', () => ({
  useCustomers: () => ({
    data: { customers: [] },
    isLoading: false,
  }),
}))

vi.mock('@/hooks/use-pipeline', () => ({
  usePipeline: () => ({
    data: { total: 0, stages: [] },
    isLoading: false,
  }),
}))

import { OQIMIntelligencePage } from './intelligence'

function proposal(overrides: Partial<CommercialActionProposal>): CommercialActionProposal {
  return {
    schema_version: 'commercial_action_proposal.v2',
    proposal_id: 'proposal-1',
    workspace_id: 1,
    conversation_id: 101,
    customer_id: 201,
    action_type: 'schedule_sales_follow_up',
    lifecycle_state: 'waiting_approval',
    execution_mode: 'approval_required',
    risk_level: 'medium',
    requires_approval: true,
    executor_runtime: null,
    priority: 'medium',
    confidence: 0.82,
    reason_code: 'sales_followup',
    source_refs: ['conversation:101'],
    payload: {},
    idempotency_key: 'proposal-1',
    correlation_id: null,
    trace_id: null,
    ...overrides,
  }
}

describe('OQIMIntelligencePage', () => {
  beforeEach(() => {
    actionItems = [
      proposal({
        proposal_id: 'order-review',
        action_type: 'check_payment',
        reason_code: 'payment_needs_review',
        payload: {
          customer_name: 'Ali Valiyev',
          candidate_value: { title: 'To‘lovni tekshirish' },
        },
      }),
      proposal({
        proposal_id: 'business-task',
        conversation_id: 102,
        customer_id: 202,
        action_type: 'create_business_task',
        reason_code: 'seller_promised_invoice',
        payload: {
          customer_display_name: 'Madina',
          candidate_value: { task_title: 'Hisob-fakturani yuborish' },
        },
      }),
      proposal({
        proposal_id: 'follow-up',
        conversation_id: 103,
        customer_id: 203,
        action_type: 'schedule_sales_follow_up',
        reason_code: 'customer_went_cold_after_price',
        payload: {
          customer_name: 'Jasur',
          candidate_value: { title: 'Narxdan keyin qayta yozish' },
        },
      }),
    ]
  })

  it('renders orders and business tasks from Action Runtime proposals', () => {
    render(<OQIMIntelligencePage />)

    expect(screen.getByText('Kompaniya ma’lumoti, mijoz holati va savdo ishlari shu yerda alohida ko‘rinadi.')).toBeTruthy()
    expect(screen.queryByText(/Brain’da/)).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Buyurtmalar' }))
    expect(screen.getByText('To‘lovni tekshirish')).toBeTruthy()
    expect(screen.getByText(/Ali Valiyev/)).toBeTruthy()
    expect(screen.queryByText('Hisob-fakturani yuborish')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Vazifalar' }))
    expect(screen.getByText('Hisob-fakturani yuborish')).toBeTruthy()
    expect(screen.getByText(/Madina/)).toBeTruthy()
    expect(screen.queryByText('To‘lovni tekshirish')).toBeNull()
  })
})
