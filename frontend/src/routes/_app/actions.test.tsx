// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import type { CommercialActionProposal } from '@/lib/types'

let actionItems: CommercialActionProposal[]
const approveMock = vi.fn()
const editDraftMock = vi.fn()
const executeMock = vi.fn()
const rejectMock = vi.fn()
const requeueMock = vi.fn()

vi.mock('@/hooks/use-action-runtime', () => ({
  useActionRuntimeInbox: () => ({
    data: { schema_version: 'action_runtime_inbox.v1', workspace_id: 1, items: actionItems },
    isLoading: false,
    error: null,
  }),
  useActionProposalTimeline: (proposalId: string | null | undefined) => ({
    data: proposalId
      ? {
          schema_version: 'agent_run_timeline.v1',
          workspace_id: 1,
          run_id: 'seller-agent-run:test',
          run: {
            schema_version: 'agent_run.v1',
            run_id: 'seller-agent-run:test',
            workspace_id: 1,
            agent_id: 7,
            agent_kind: 'seller',
            trigger_ref: 'message:123',
            conversation_id: 10,
            customer_id: 20,
            state: 'waiting_approval',
            permission_mode: 'ask_always',
            cache_key: null,
            correlation_id: 'corr:test',
            idempotency_key: 'idem:test',
            source_refs: ['message:123'],
            started_at: '2026-05-18T00:00:00Z',
            completed_at: null,
          },
          events: [
            {
              schema_version: 'agent_run_event.v1',
              event_id: 'event-owner',
              run_id: 'seller-agent-run:test',
              workspace_id: 1,
              sequence: 1,
              event_type: 'owner_progress.created',
              visibility: 'owner',
              owner_label: 'Brain va katalog tekshirildi',
              owner_detail: '2 ta dalil topildi.',
              tool_name: null,
              tool_state: null,
              action_proposal_id: null,
              source_refs: ['fact:1'],
              payload: {},
              correlation_id: 'corr:test',
              idempotency_key: 'idem:test:owner',
              created_at: '2026-05-18T00:00:01Z',
            },
            {
              schema_version: 'agent_run_event.v1',
              event_id: 'event-internal',
              run_id: 'seller-agent-run:test',
              workspace_id: 1,
              sequence: 2,
              event_type: 'tool.call.started',
              visibility: 'internal',
              owner_label: '',
              owner_detail: '',
              tool_name: 'catalog.search',
              tool_state: 'called',
              action_proposal_id: null,
              source_refs: [],
              payload: {},
              correlation_id: 'corr:test',
              idempotency_key: 'idem:test:internal',
              created_at: '2026-05-18T00:00:02Z',
            },
            {
              schema_version: 'agent_run_event.v1',
              event_id: 'event-customer-action',
              run_id: 'seller-agent-run:test',
              workspace_id: 1,
              sequence: 3,
              event_type: 'customer_status.proposed',
              visibility: 'customer_action',
              owner_label: 'Mijozga holat xabari taklif qilindi',
              owner_detail: 'Bu yakuniy javob emas.',
              tool_name: null,
              tool_state: null,
              action_proposal_id: 'safe-reply',
              source_refs: ['proposal:safe-reply'],
              payload: {},
              correlation_id: 'corr:test',
              idempotency_key: 'idem:test:customer-action',
              created_at: '2026-05-18T00:00:03Z',
            },
          ],
        }
      : null,
    isLoading: false,
    error: null,
  }),
  useApproveActionProposal: () => ({ mutateAsync: approveMock, isPending: false }),
  useEditActionProposalDraft: () => ({ mutateAsync: editDraftMock, isPending: false }),
  useExecuteActionProposal: () => ({ mutateAsync: executeMock, isPending: false }),
  useRejectActionProposal: () => ({ mutateAsync: rejectMock, isPending: false }),
  useRequeueActionProposal: () => ({ mutateAsync: requeueMock, isPending: false }),
}))

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}))

vi.mock('@/components/ui/scroll-area', () => ({
  ScrollArea: ({ children, className }: { children: ReactNode; className?: string }) => (
    <div className={className}>{children}</div>
  ),
}))

import { ActionsPage } from './actions'

function proposal(overrides: Partial<CommercialActionProposal>): CommercialActionProposal {
  return {
    schema_version: 'commercial_action_proposal.v2',
    proposal_id: 'proposal-1',
    workspace_id: 1,
    conversation_id: 10,
    customer_id: 20,
    action_type: 'send_reply',
    lifecycle_state: 'waiting_approval',
    execution_mode: 'approval_required',
    risk_level: 'low',
    requires_approval: true,
    executor_runtime: 'telegram_tool_runtime',
    priority: 'medium',
    confidence: 0.9,
    reason_code: 'sales_followup',
    source_refs: ['message:123'],
    payload: {
      customer_name: 'Ali Valiyev',
      draft_text: 'Assalomu alaykum, buyurtmani davom ettiraymi?',
    },
    idempotency_key: 'idem-1',
    correlation_id: null,
    trace_id: null,
    ...overrides,
  }
}

describe('ActionsPage', () => {
  beforeEach(() => {
    approveMock.mockReset().mockResolvedValue({})
    editDraftMock.mockReset().mockResolvedValue({})
    executeMock.mockReset().mockResolvedValue({})
    rejectMock.mockReset().mockResolvedValue({})
    requeueMock.mockReset().mockResolvedValue({})
    actionItems = [
      proposal({
        proposal_id: 'safe-reply',
        action_type: 'send_reply',
        risk_level: 'low',
        payload: {
          customer_name: 'Ali Valiyev',
          draft_text: 'Assalomu alaykum, buyurtmani davom ettiraymi?',
        },
      }),
      proposal({
        proposal_id: 'status-message',
        action_type: 'send_status_message',
        risk_level: 'low',
        payload: {
          customer_name: 'Dilshod',
          draft_text: 'Katalogdan aynan shu modelni tekshiryapman.',
          kind: 'progress',
          not_final_answer: true,
        },
      }),
      proposal({
        proposal_id: 'risky-calendar',
        action_type: 'create_calendar_event',
        risk_level: 'high',
        payload: {
          customer_name: 'Madina',
          candidate_value: { title: 'Ertaga 11:00 uchrashuv' },
        },
      }),
      proposal({
        proposal_id: 'failed-payment',
        action_type: 'check_payment',
        lifecycle_state: 'failed',
        risk_level: 'medium',
        payload: {
          customer_name: 'Jasur',
          candidate_value: { title: 'To‘lov chekini tekshirish' },
        },
      }),
      proposal({
        proposal_id: 'custom-agent',
        conversation_id: 0,
        customer_id: 0,
        action_type: 'agent.create_custom_package',
        risk_level: 'high',
        reason_code: 'custom_agent_requires_owner_approval',
        source_refs: ['agent_package_request:abc123'],
        payload: {
          customer_name: 'Workspace sozlamasi',
          title: 'Uchrashuv agenti agentini yaratish',
        },
      }),
    ]
  })

  it('shows proposals with honest risk gating and curated evidence labels', () => {
    render(<ActionsPage />)

    expect(screen.getByText('Amallar')).toBeTruthy()
    expect(screen.getAllByText('Javob yuborish').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Holat xabari').length).toBeGreaterThan(0)
    expect(screen.queryByText('send_status_message')).toBeNull()
    expect(screen.getByText('Jarayon')).toBeTruthy()
    expect(screen.getByText('Brain va katalog tekshirildi')).toBeTruthy()
    expect(screen.getByText('Mijozga holat xabari taklif qilindi')).toBeTruthy()
    expect(screen.queryByText('tool.call.started')).toBeNull()
    expect(screen.queryByText('catalog.search')).toBeNull()
    expect(screen.getAllByText('Uchrashuv yaratish').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Agent yaratish').length).toBeGreaterThan(0)
    expect(screen.getByText('Yuqori xavfli amal bor')).toBeTruthy()
    expect(screen.queryByText('Xavfsizlarini tasdiqlab bajarish')).toBeNull()
    expect(screen.getByText('Telegram xabari #123')).toBeTruthy()
    expect(screen.queryByText('message:123')).toBeNull()

    fireEvent.click(screen.getByText('Uchrashuv agenti agentini yaratish'))
    expect(screen.getByText('Agent taklifi: abc123')).toBeTruthy()
  })

  it('approves and executes the selected safe action explicitly', async () => {
    render(<ActionsPage />)

    fireEvent.click(screen.getByRole('button', { name: /Tasdiqlab bajarish/i }))

    await waitFor(() => {
      expect(approveMock).toHaveBeenCalledWith('safe-reply')
      expect(executeMock).toHaveBeenCalledWith('safe-reply')
    })
  })

  it('edits a reply draft before approval', async () => {
    render(<ActionsPage />)

    fireEvent.click(screen.getByRole('button', { name: /Tahrirlash/i }))
    fireEvent.change(screen.getByLabelText('Javob matni'), {
      target: { value: 'Assalomu alaykum, ertaga 11:00 da javob beraman.' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Saqlash/i }))

    await waitFor(() => {
      expect(editDraftMock).toHaveBeenCalledWith({
        proposalId: 'safe-reply',
        draftText: 'Assalomu alaykum, ertaga 11:00 da javob beraman.',
      })
    })
  })

  it('requeues failed actions from the failed lifecycle tab', async () => {
    render(<ActionsPage />)

    fireEvent.click(screen.getByRole('button', { name: /Xato/i }))
    expect(screen.getAllByText('To‘lovni tekshirish').length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: /Qayta urinish/i }))

    await waitFor(() => {
      expect(requeueMock).toHaveBeenCalledWith('failed-payment')
    })
  })
})
