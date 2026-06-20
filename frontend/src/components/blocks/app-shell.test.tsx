// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

let mockLocation = { pathname: '/conversations', search: {} as Record<string, string | undefined> }
const biCommandMock = vi.hoisted(() => vi.fn())
const recentRunsRefetchMock = vi.hoisted(() => vi.fn())
const recentRunsMock = vi.hoisted(() => vi.fn())

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, search, children, ...props }: { to: string; search?: Record<string, string>; children: ReactNode }) => (
    <a href={to} data-search={JSON.stringify(search ?? {})} {...props}>
      {children}
    </a>
  ),
  useLocation: () => mockLocation,
}))

vi.mock('@/lib/auth-context', () => ({
  useAuth: () => ({
    session: {
      workspace: { name: 'SATStation' },
      integrations: [{ provider: 'telegram_personal', durable_connected: true, needs_reconnect: false }],
    },
    user: { name: 'Mirzo', is_founder: false },
  }),
}))

vi.mock('@/hooks/use-activity-stream', () => ({
  useActivityStream: () => ({ latestEvent: null, events: [], eventCount: 0 }),
}))

vi.mock('@/hooks/use-action-runtime', () => ({
  useActionRuntimeInbox: () => ({
    data: { schema_version: 'action_runtime_inbox.v1', workspace_id: 1, items: [] },
    isFetching: false,
    refetch: vi.fn(),
  }),
  useRecentAgentRuns: () => recentRunsMock(),
}))

vi.mock('@/hooks/use-bi-promoter', () => ({
  useBICommandMutation: () => ({
    mutateAsync: biCommandMock,
    isPending: false,
  }),
}))

import { AppShell } from './app-shell'

function renderShell(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <AppShell>{children}</AppShell>
    </QueryClientProvider>,
  )
}

describe('AppShell', () => {
  beforeEach(() => {
    biCommandMock.mockReset()
    biCommandMock.mockResolvedValue({})
    recentRunsRefetchMock.mockReset()
    recentRunsMock.mockReset()
    recentRunsMock.mockReturnValue({
      data: { schema_version: 'agent_run_feed.v1', workspace_id: 1, timelines: [] },
      isLoading: false,
      isFetching: false,
      error: null,
      refetch: recentRunsRefetchMock,
    })
  })

  it('renders the locked top-level nav items', () => {
    mockLocation = { pathname: '/conversations', search: {} }

    renderShell(<div>Workspace content</div>)

    expect(screen.getByText('Workspace content')).toBeDefined()
    for (const label of ['Suhbatlar', 'Bilim', 'Manbalar', 'Agentlar', 'Aql', 'Amallar', 'Vazifalar', 'Integratsiyalar', 'Sozlamalar']) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0)
    }
  })

  it('does not link to retired top-level surfaces', () => {
    mockLocation = { pathname: '/conversations', search: {} }
    renderShell(<div>X</div>)
    expect(screen.queryByRole('link', { name: /Ruxsat navbati/i })).toBeNull()
    expect(screen.queryByRole('link', { name: /Founder runtime/i })).toBeNull()
  })

  it('renders the BI rail inline on a page without its own context rail', () => {
    mockLocation = { pathname: '/intelligence', search: {} }
    renderShell(<div>X</div>)
    expect(screen.getByText('Jarayon')).toBeDefined()
    expect(screen.getByText('Ruxsat kerak')).toBeDefined()
    expect(screen.getByText('Bajarildi')).toBeDefined()
    expect(screen.getByPlaceholderText(/Instagramdan kelgan savollar/i)).toBeDefined()
  })

  it('opens the same BI rail in an adaptive sheet', () => {
    mockLocation = { pathname: '/conversations', search: {} }
    renderShell(<div>X</div>)

    fireEvent.click(screen.getByRole('button', { name: 'BI panelni ochish' }))

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getAllByText('Faoliyat').length).toBeGreaterThan(0)
    expect(within(dialog).getByText('Jarayon')).toBeDefined()
    expect(within(dialog).getByPlaceholderText(/Instagramdan kelgan savollar/i)).toBeDefined()
  })

  it('renders owner-visible agent progress without leaking internal tool state', () => {
    mockLocation = { pathname: '/intelligence', search: {} }
    recentRunsMock.mockReturnValue({
      data: {
        schema_version: 'agent_run_feed.v1',
        workspace_id: 1,
        timelines: [
          {
            schema_version: 'agent_run_timeline.v1',
            workspace_id: 1,
            run_id: 'run:progress:2',
            run: {
              schema_version: 'agent_run.v1',
              run_id: 'run:progress:2',
              workspace_id: 1,
              agent_id: 7,
              agent_kind: 'seller',
              trigger_ref: 'message:200',
              conversation_id: 11,
              customer_id: 21,
              state: 'running',
              permission_mode: 'ask_always',
              cache_key: null,
              correlation_id: 'corr:run:2',
              idempotency_key: 'idem:run:2',
              source_refs: [],
              started_at: '2026-05-18T09:00:00Z',
              completed_at: null,
            },
            events: [
              {
                schema_version: 'agent_run_event.v1',
                event_id: 'run-progress-2-owner-1',
                run_id: 'run:progress:2',
                workspace_id: 1,
                sequence: 1,
                event_type: 'owner_progress.created',
                visibility: 'owner',
                owner_label: 'Katalogdan model qidirilyapti',
                owner_detail: 'Agent katalogdan mos mahsulotni tekshirmoqda.',
                tool_name: null,
                tool_state: null,
                action_proposal_id: null,
                source_refs: [],
                payload: {},
                correlation_id: 'corr:run:2',
                idempotency_key: 'idem:run:2:owner:1',
                created_at: '2026-05-18T09:00:01Z',
              },
              {
                schema_version: 'agent_run_event.v1',
                event_id: 'run-progress-2-tool-1',
                run_id: 'run:progress:2',
                workspace_id: 1,
                sequence: 2,
                event_type: 'tool.call.started',
                visibility: 'internal',
                owner_label: '',
                owner_detail: '',
                tool_name: 'catalog.search',
                tool_state: 'called',
                action_proposal_id: null,
                source_refs: ['message:200'],
                payload: {},
                correlation_id: 'corr:run:2',
                idempotency_key: 'idem:run:2:tool:1',
                created_at: '2026-05-18T09:00:02Z',
              },
              {
                schema_version: 'agent_run_event.v1',
                event_id: 'run-progress-2-action-1',
                run_id: 'run:progress:2',
                workspace_id: 1,
                sequence: 3,
                event_type: 'customer_status.proposed',
                visibility: 'customer_action',
                owner_label: 'Mijozga holat xabari taklif qilindi',
                owner_detail: 'Aniqlashtirish xabari ruxsat kutmoqda.',
                tool_name: null,
                tool_state: null,
                action_proposal_id: 'proposal-status-message',
                source_refs: [],
                payload: {},
                correlation_id: 'corr:run:2',
                idempotency_key: 'idem:run:2:action:1',
                created_at: '2026-05-18T09:00:03Z',
              },
            ],
          },
        ],
      },
      isLoading: false,
      isFetching: false,
      error: null,
      refetch: recentRunsRefetchMock,
    })

    renderShell(<div>X</div>)

    expect(screen.getByText('Sotuvchi agent')).toBeDefined()
    expect(screen.getByText('Ishlayapti')).toBeDefined()
    expect(screen.getByText('Katalogdan model qidirilyapti')).toBeDefined()
    expect(screen.getAllByText('Mijozga holat xabari taklif qilindi').length).toBeGreaterThan(0)
    expect(screen.queryByText('catalog.search')).toBeNull()
    expect(screen.queryByText('message:200')).toBeNull()
  })

  it('turns a right-rail BI request into a structured agent proposal command', async () => {
    mockLocation = { pathname: '/intelligence', search: {} }
    renderShell(<div>X</div>)

    fireEvent.change(screen.getByLabelText('Agent nomi'), {
      target: { value: 'Uchrashuv agenti' },
    })
    fireEvent.change(screen.getByLabelText('BI topshiriq'), {
      target: {
        value: 'Mijoz uchrashuv so‘rasa vaqt taklif qiladigan agent yarat',
      },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Yuborish' }))

    await waitFor(() => {
      expect(biCommandMock).toHaveBeenCalledWith(
        expect.objectContaining({
          command_kind: 'create_agent',
          agent_name: 'Uchrashuv agenti',
          command_text: 'Mijoz uchrashuv so‘rasa vaqt taklif qiladigan agent yarat',
          permission_mode: 'ask_always',
          tool_scopes: ['telegram.read_messages'],
        }),
      )
    })
  })

  it('turns a right-rail BI task request into an owner task proposal command', async () => {
    mockLocation = { pathname: '/intelligence', search: {} }
    renderShell(<div>X</div>)

    fireEvent.click(screen.getByRole('button', { name: 'Vazifa' }))
    fireEvent.change(screen.getByLabelText('BI topshiriq'), {
      target: {
        value: 'Ertaga soat 11:00 da mijoz bilan uchrashuvni eslat',
      },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Yuborish' }))

    await waitFor(() => {
      expect(biCommandMock).toHaveBeenCalledWith(
        expect.objectContaining({
          command_kind: 'create_owner_task',
          command_text: 'Ertaga soat 11:00 da mijoz bilan uchrashuvni eslat',
          task_title: 'Ertaga soat 11:00 da mijoz bilan uchrashuvni eslat',
          task_detail: 'Ertaga soat 11:00 da mijoz bilan uchrashuvni eslat',
          task_kind: 'meeting',
        }),
      )
    })
  })

  it('exposes the persistent BI launcher on every route (rail across pages)', () => {
    const routes = [
      '/conversations', '/brain', '/sources', '/agents', '/agents/7',
      '/intelligence', '/actions', '/tasks', '/integrations', '/settings',
    ]
    for (const pathname of routes) {
      mockLocation = { pathname, search: {} }
      const { unmount } = renderShell(<div>X</div>)
      expect(
        screen.queryByRole('button', { name: 'BI panelni ochish' }),
        `BI rail unreachable on ${pathname}`,
      ).not.toBeNull()
      unmount()
    }
  })

  it('a page with its own context rail uses the launcher, not an inline column', () => {
    mockLocation = { pathname: '/brain', search: {} }
    renderShell(<div>X</div>)
    // Launcher page: no inline BI column until the sheet is opened.
    expect(screen.queryByPlaceholderText(/Instagramdan kelgan savollar/i)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'BI panelni ochish' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByPlaceholderText(/Instagramdan kelgan savollar/i)).toBeDefined()
  })
})
