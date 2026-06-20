// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import type { AgentDetailResponse } from '@/hooks/use-agent-workbench'

const updateAgentMock = vi.fn()
const upsertSectionMock = vi.fn()
const proposeToolGrantMock = vi.fn()
const proposeTriggerMock = vi.fn()
let detailData: AgentDetailResponse

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children, ...props }: { to: string; children: ReactNode }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
  useParams: () => ({ agentId: '1' }),
}))

vi.mock('@/hooks/use-agent-workbench', () => ({
  useAgentDetail: () => ({ data: detailData, isLoading: false, error: null }),
  useToolCatalog: () => ({
    data: {
      schema_version: 'intelligence_tool_catalog.v1',
      items: [
        {
          scope: 'telegram.read_messages',
          connector: 'telegram',
          verb: 'read_messages',
          label_uz: 'Suhbatni o‘qish',
          description_uz: 'Agent suhbat tarixini o‘qib javob tuzadi.',
          short_label: 'read',
          operation_kind: 'read',
          risk_level: 'medium',
          mutates_external_state: false,
          requires_action_proposal: false,
          default_permission_mode: 'ask_always',
          owner_visible: true,
          runtime_boundary: 'telegram_tool_runtime',
        },
        {
          scope: 'telegram.send_message',
          connector: 'telegram',
          verb: 'send_message',
          label_uz: 'Javob yuborish',
          description_uz: 'Agent faqat tasdiqlangan javobni yuboradi.',
          short_label: 'send',
          operation_kind: 'write',
          risk_level: 'high',
          mutates_external_state: true,
          requires_action_proposal: true,
          default_permission_mode: 'ask_always',
          owner_visible: true,
          runtime_boundary: 'telegram_tool_runtime',
        },
        {
          scope: 'telegram.fetch_media',
          connector: 'telegram',
          verb: 'fetch_media',
          label_uz: 'Media ochish',
          description_uz: 'Agent rasm, chek va faylni ochib tekshiradi.',
          short_label: 'media',
          operation_kind: 'media',
          risk_level: 'medium',
          mutates_external_state: false,
          requires_action_proposal: false,
          default_permission_mode: 'ask_always',
          owner_visible: true,
          runtime_boundary: 'telegram_tool_runtime',
        },
      ],
    },
    isLoading: false,
    error: null,
  }),
  useUpdateAgentState: () => ({ mutate: updateAgentMock, isPending: false }),
  useUpsertAgentSection: () => ({ mutate: upsertSectionMock, isPending: false }),
  useProposeAgentToolGrant: () => ({ mutate: proposeToolGrantMock, isPending: false }),
  useProposeAgentTrigger: () => ({ mutate: proposeTriggerMock, isPending: false }),
}))

vi.mock('@/components/ui/scroll-area', () => ({
  ScrollArea: ({ children, className }: { children: ReactNode; className?: string }) => (
    <div className={className}>{children}</div>
  ),
}))

import { AgentDetailPage } from './agent-detail'

function buildDetail(): AgentDetailResponse {
  return {
    schema_version: 'intelligence_agent_detail.v1',
    agent: {
      id: 1,
      name: 'Seller Agent',
      agent_type: 'seller',
      trust_mode: 'draft',
      is_active: true,
      package_key: 'seller',
      permission_mode: 'ask_always',
      skill_count: 1,
      document_section_count: 2,
      tool_grant_count: 2,
      trigger_count: 1,
      contact_scope: 'business',
    },
    enforced_config: {
      permission_mode: 'ask_always',
      trust_mode: 'draft',
      is_active: true,
      contact_scope: 'business',
      brain_scopes: ['catalog', 'knowledge'],
      tool_scopes: ['telegram.read_messages', 'telegram.send_message'],
      channel_mode: 'workspace_events',
    },
    drift_warnings: [
      {
        code: 'permission_mode_drift',
        title_uz: 'Hujjat va haqiqiy ruxsat mos emas',
        detail_uz: 'Hozir ishlayotgan sozlama ishlatiladi.',
        document_value: 'full_access',
        enforced_value: 'ask_always',
      },
    ],
    sections: [
      {
        id: 10,
        section_key: 'role',
        title: 'Rol',
        body: 'Sotuvchi agent mijozlarga aniq javob beradi.',
        order_index: 10,
        generated_by: 'workspace_os_provisioner',
      },
      {
        id: 11,
        section_key: 'never_guess',
        title: 'Nimani taxmin qilmaydi',
        body: 'Narx, mavjudlik va to‘lov holatini taxmin qilmaydi.',
        order_index: 20,
        generated_by: 'owner',
      },
    ],
    skills: [
      {
        id: 20,
        slug: 'seller-grounded-reply',
        name: 'Grounded seller reply',
        description: 'Brain va suhbatdan javob yozadi.',
        instructions: '',
        when_to_use: '',
        when_not_to_use: '',
        tools: ['brain.search'],
        enabled: true,
      },
    ],
    tool_grants: [
      {
        id: 30,
        agent_id: 1,
        scope: 'telegram.read_messages',
        grant_reason: 'Read customer context.',
        granted_by: 'workspace_os_provisioner',
        active: true,
        use_count: 0,
      },
      {
        id: 31,
        agent_id: 1,
        scope: 'telegram.send_message',
        grant_reason: 'Send approved replies.',
        granted_by: 'workspace_os_provisioner',
        active: true,
        use_count: 0,
      },
    ],
    triggers: [
      {
        id: 40,
        owner_agent_id: 1,
        event_source: 'channel_message_received',
        action_proposal_type: 'conversation.propose_reply',
        permission_mode: 'ask_always',
        last_run_status: null,
        last_run_at: null,
        run_count: 0,
        notes: 'Yangi mijoz xabarida javob taklif qiladi.',
        active: true,
      },
    ],
    recent_actions: [
      {
        proposal_id: 'proposal-1',
        action_type: 'send_reply',
        lifecycle_state: 'waiting_approval',
        risk_level: 'low',
        reason_code: 'sales_followup',
        summary_uz: 'Javob yuborish taklifi',
        created_at: '2026-05-17T10:00:00Z',
      },
    ],
    rendered: {
      kind: 'agent',
      title: 'Seller Agent',
      markdown: '## Rol\nSotuvchi agent mijozlarga aniq javob beradi.',
      sections_used: 2,
    },
  }
}

describe('AgentDetailPage', () => {
  beforeEach(() => {
    updateAgentMock.mockReset()
    upsertSectionMock.mockReset()
    proposeToolGrantMock.mockReset()
    proposeTriggerMock.mockReset()
    detailData = buildDetail()
  })

  it('renders AGENT.md with skills, permissions, triggers and drift warning', () => {
    render(<AgentDetailPage />)

    expect(screen.getByText('Seller Agent')).toBeTruthy()
    expect(screen.getAllByText('AGENT.md').length).toBeGreaterThan(0)
    expect(screen.getByText('Rol')).toBeTruthy()
    expect(screen.getByText('Sotuvchi agent mijozlarga aniq javob beradi.')).toBeTruthy()
    expect(screen.getByText('Grounded seller reply')).toBeTruthy()

    fireEvent.click(screen.getByRole('tab', { name: 'Ruxsatlar' }))
    expect(screen.getByText('Suhbatni o‘qish')).toBeTruthy()
    expect(screen.queryByText('telegram.read_messages')).toBeNull()

    fireEvent.click(screen.getByRole('tab', { name: 'Triggerlar' }))
    expect(screen.getByText('Yangi Telegram xabar')).toBeTruthy()
    expect(screen.getByText('Hujjat va haqiqiy ruxsat mos emas')).toBeTruthy()
  })

  it('updates active state through the agent runtime endpoint', () => {
    render(<AgentDetailPage />)

    fireEvent.click(screen.getByRole('switch', { name: /Agent holatini/i }))

    expect(updateAgentMock).toHaveBeenCalledWith({ is_active: false })
  })

  it('proposes Telegram permission changes instead of mutating grants directly', () => {
    render(<AgentDetailPage />)

    fireEvent.click(screen.getByRole('tab', { name: 'Ruxsatlar' }))
    fireEvent.click(screen.getByRole('button', { name: /Media ochish/i }))

    expect(proposeToolGrantMock).toHaveBeenCalledWith({
      action: 'grant',
      scope: 'telegram.fetch_media',
      reason: 'Seller Agent agenti uchun media ochish ruxsati',
      correlation_id: 'ui:agent:1:tool-grant',
    })
  })

  it('proposes trigger changes instead of mutating triggers directly', () => {
    render(<AgentDetailPage />)

    fireEvent.click(screen.getByRole('tab', { name: 'Triggerlar' }))
    fireEvent.click(screen.getByRole('button', { name: /BI buyrug‘idan ish boshlash/i }))

    expect(proposeTriggerMock).toHaveBeenCalledWith({
      operation: 'create',
      event_source: 'owner_bi_command',
      action_proposal_type: 'agent.handle_owner_command',
      matching_scope: {},
      permission_mode: 'ask_always',
      retry_policy: { max_attempts: 2 },
      notes: 'Ega BI agentga topshiriq berganda bu agent ishga tushadi.',
      correlation_id: 'ui:agent:1:trigger',
    })
  })
})
