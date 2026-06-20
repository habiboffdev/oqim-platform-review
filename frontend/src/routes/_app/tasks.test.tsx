// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import type { OwnerTaskItem } from '@/lib/types'

let taskItems: OwnerTaskItem[]
let proposedItems: OwnerTaskItem[]
const acceptMock = vi.fn()
const completeMock = vi.fn()
const dismissMock = vi.fn()
const snoozeMock = vi.fn()
const biCommandMock = vi.fn()

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: ReactNode }) => <a href="/conversations/1">{children}</a>,
}))

vi.mock('@/hooks/use-owner-tasks', () => ({
  useOwnerTasks: () => ({
    data: {
      schema_version: 'owner_task_projection.v1',
      workspace_id: 1,
      items: taskItems,
      proposed: proposedItems,
      counts: {
        today: taskItems.filter((item) => item.due_bucket === 'today').length,
        overdue: taskItems.filter((item) => item.due_bucket === 'overdue').length,
        upcoming: taskItems.filter((item) => item.due_bucket === 'upcoming').length,
        completed: taskItems.filter((item) => item.due_bucket === 'completed').length,
        proposed: proposedItems.length,
      },
    },
    isLoading: false,
    error: null,
  }),
  useAcceptOwnerTask: () => ({ mutateAsync: acceptMock, isPending: false }),
  useCompleteOwnerTask: () => ({ mutateAsync: completeMock, isPending: false }),
  useDismissOwnerTask: () => ({ mutateAsync: dismissMock, isPending: false }),
  useSnoozeOwnerTask: () => ({ mutateAsync: snoozeMock, isPending: false }),
}))

vi.mock('@/hooks/use-bi-promoter', () => ({
  useBICommandMutation: () => ({ mutateAsync: biCommandMock, isPending: false }),
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

import { TasksPage } from './tasks'

function task(overrides: Partial<OwnerTaskItem>): OwnerTaskItem {
  return {
    schema_version: 'owner_task_item.v1',
    task_id: 'task:proposal-task-1',
    workspace_id: 1,
    proposal_id: 'proposal-task-1',
    action_type: 'check_payment',
    kind: 'payment',
    state: 'accepted',
    due_bucket: 'today',
    title: "To'lov chekini tekshirish",
    detail: 'Mijoz chek yuborganini aytdi.',
    customer_label: 'Jasur',
    conversation_id: 10,
    customer_id: 20,
    due_at: null,
    status_label: 'Bajarish kerak',
    source_label: 'Telegram suhbat',
    evidence_labels: ['Suhbat dalili'],
    priority: 'medium',
    risk_level: 'low',
    confidence: 0.9,
    can_accept: false,
    can_complete: true,
    can_snooze: true,
    can_message: true,
    proposal: {
      schema_version: 'commercial_action_proposal.v2',
      proposal_id: 'proposal-task-1',
      workspace_id: 1,
      conversation_id: 10,
      customer_id: 20,
      action_type: 'check_payment',
      lifecycle_state: 'approved',
      execution_mode: 'suggest_only',
      risk_level: 'low',
      requires_approval: false,
      priority: 'medium',
      confidence: 0.9,
      reason_code: 'payment_check',
      source_refs: ['message:123'],
      payload: {},
      idempotency_key: 'idem-task-1',
    },
    ...overrides,
  }
}

describe('TasksPage', () => {
  beforeEach(() => {
    acceptMock.mockReset().mockResolvedValue({})
    completeMock.mockReset().mockResolvedValue({})
    dismissMock.mockReset().mockResolvedValue({})
    snoozeMock.mockReset().mockResolvedValue({})
    biCommandMock.mockReset().mockResolvedValue({})
    taskItems = [
      task({ proposal_id: 'payment-task', task_id: 'task:payment-task' }),
      task({
        proposal_id: 'delivery-task',
        task_id: 'task:delivery-task',
        kind: 'delivery',
        due_bucket: 'overdue',
        title: 'Yetkazishni tekshirish',
        status_label: 'Yordam kerak',
        state: 'blocked',
      }),
      task({
        proposal_id: 'done-task',
        task_id: 'task:done-task',
        due_bucket: 'completed',
        state: 'completed',
        can_complete: false,
        can_snooze: false,
        status_label: 'Tugatilgan',
      }),
    ]
    proposedItems = [
      task({
        proposal_id: 'proposed-meeting',
        task_id: 'task:proposed-meeting',
        kind: 'meeting',
        state: 'proposed',
        due_bucket: 'proposed',
        title: 'Madina bilan uchrashuv vaqtini tasdiqlash',
        status_label: 'Qabul qilish kerak',
        can_accept: true,
        can_complete: false,
      }),
    ]
  })

  it('renders owner tasks separately from proposed tasks with business labels', () => {
    render(<TasksPage />)

    expect(screen.getByText('Vazifalar')).toBeTruthy()
    expect(screen.getAllByText("To'lov chekini tekshirish").length).toBeGreaterThan(0)
    expect(screen.getByText('Taklif qilingan vazifalar')).toBeTruthy()
    expect(screen.getByText('Madina bilan uchrashuv vaqtini tasdiqlash')).toBeTruthy()
    expect(screen.queryByText('message:123')).toBeNull()
    expect(screen.getAllByText('Suhbat dalili').length).toBeGreaterThan(0)
  })

  it('completes accepted tasks through the owner task endpoint', async () => {
    render(<TasksPage />)

    fireEvent.click(screen.getAllByRole('button', { name: /Bajarildi/i })[0])

    await waitFor(() => {
      expect(completeMock).toHaveBeenCalledWith('payment-task')
    })
  })

  it('accepts proposed tasks from the right rail', async () => {
    render(<TasksPage />)

    fireEvent.click(screen.getByRole('button', { name: /^Qabul$/i }))

    await waitFor(() => {
      expect(acceptMock).toHaveBeenCalledWith('proposed-meeting')
    })
  })

  it('creates owner task proposals through the BI command surface', async () => {
    render(<TasksPage />)

    fireEvent.change(screen.getByLabelText('Nima qilish kerak?'), {
      target: { value: 'Uchrashuv vaqtini tasdiqlash' },
    })
    fireEvent.change(screen.getByLabelText('Tafsilot'), {
      target: { value: 'Ertaga 11:00 vaqt mijozga mosligini egasi tekshiradi.' },
    })
    fireEvent.change(screen.getByLabelText('Kimga oid?'), {
      target: { value: 'Madina' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Taklif yaratish/i }))

    await waitFor(() => {
      expect(biCommandMock).toHaveBeenCalledWith(expect.objectContaining({
        command_kind: 'create_owner_task',
        task_kind: 'business',
        task_title: 'Uchrashuv vaqtini tasdiqlash',
        task_detail: 'Ertaga 11:00 vaqt mijozga mosligini egasi tekshiradi.',
        customer_label: 'Madina',
      }))
    })
  })

  it('turns a conversation task message into an Action Proposal', async () => {
    render(<TasksPage />)

    fireEvent.change(screen.getByLabelText('Javob matni'), {
      target: { value: 'Salom, ertaga 11:00 uchrashuvga vaqtim bor.' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Javob taklif qilish/i }))

    await waitFor(() => {
      expect(biCommandMock).toHaveBeenCalledWith(expect.objectContaining({
        command_kind: 'create_reply_action',
        conversation_id: 10,
        customer_id: 20,
        customer_label: 'Jasur',
        reply_text: 'Salom, ertaga 11:00 uchrashuvga vaqtim bor.',
        source_proposal_id: 'payment-task',
      }))
    })
  })

  it('does not show chat or reply actions for workspace-scoped tasks', () => {
    taskItems = [
      task({
        proposal_id: 'workspace-task',
        task_id: 'task:workspace-task',
        action_type: 'create_business_task',
        title: 'Katalog faylini yangilash',
        detail: 'Yangi ro‘yxatni Brain ichida tartibga solish kerak.',
        customer_label: 'Biznes',
        conversation_id: 0,
        customer_id: 0,
        can_message: false,
      }),
    ]
    proposedItems = []

    render(<TasksPage />)

    expect(screen.queryByText('Suhbatni ochish')).toBeNull()
    expect(screen.queryByLabelText('Javob matni')).toBeNull()
  })
})
