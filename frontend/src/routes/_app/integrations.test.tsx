// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

let toolGrantsData: unknown

vi.mock('@tanstack/react-router', () => ({
  useSearch: () => ({}),
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({
    data: toolGrantsData,
    isLoading: false,
    error: null,
  }),
  useMutation: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
  useQueryClient: () => ({
    invalidateQueries: vi.fn(),
  }),
}))

vi.mock('@/lib/auth-context', () => ({
  useAuth: () => ({
    session: {
      integrations: [
        {
          provider: 'telegram_personal',
          durable_connected: true,
          needs_reconnect: false,
        },
      ],
    },
  }),
}))

import { IntegrationsPage } from './integrations'

describe('IntegrationsPage', () => {
  beforeEach(() => {
    toolGrantsData = {
      schema_version: 'intelligence_tool_grants.v1',
      items: [
        {
          id: 1,
          workspace_id: 1,
          agent_id: 3,
          scope: 'telegram.send_message',
          connector: 'telegram',
          scope_label: 'Javob yuborish',
          scope_description: 'Agent faqat tasdiqlangan javobni Telegramga yuboradi.',
          active: true,
          use_count: 4,
          granted_at: '2026-05-17T10:00:00Z',
          last_used_at: null,
        },
        {
          id: 2,
          workspace_id: 1,
          agent_id: 3,
          scope: 'telegram.fetch_media',
          connector: 'telegram',
          scope_label: 'Media ochish',
          scope_description: 'Agent rasm, chek va fayllarni dalil sifatida tekshiradi.',
          active: false,
          use_count: 0,
          granted_at: '2026-05-17T10:00:00Z',
          last_used_at: null,
        },
      ],
    }
  })

  it('shows Telegram tools as owner-facing capabilities, not raw scopes', () => {
    render(<IntegrationsPage />)

    expect(screen.getByText('Telegram')).toBeTruthy()
    expect(screen.getByText('Ulangan')).toBeTruthy()
    expect(screen.getByText('Javob yuborish')).toBeTruthy()
    expect(screen.getByText('Media ochish')).toBeTruthy()
    expect(screen.getByText('4 marta ishlatilgan')).toBeTruthy()
    expect(screen.queryByText('telegram.send_message')).toBeNull()
    expect(screen.queryByText('telegram.fetch_media')).toBeNull()
    expect(screen.queryByText(/scope granti/i)).toBeNull()
  })
})
