// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'

const navigateMock = vi.fn()

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children, ...props }: { to: string; children: ReactNode }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
  useNavigate: () => navigateMock,
}))

vi.mock('@/hooks/use-agent-workbench', () => ({
  useAgentWorkbenchAgents: () => ({
    data: {
      schema_version: 'intelligence_agents.v1',
      items: [
        {
          id: 1,
          name: 'Seller Agent',
          agent_type: 'seller',
          trust_mode: 'draft',
          is_active: true,
          package_key: 'seller',
          permission_mode: 'ask_always',
          skill_count: 1,
          document_section_count: 4,
          tool_grant_count: 2,
          trigger_count: 1,
        },
      ],
    },
    isLoading: false,
    error: null,
  }),
  useCreateCustomAgent: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
  }),
}))

vi.mock('@/components/ui/scroll-area', () => ({
  ScrollArea: ({ children, className }: { children: ReactNode; className?: string }) => (
    <div className={className}>{children}</div>
  ),
}))

import { AgentsPage } from './agents'

describe('AgentsPage', () => {
  afterEach(() => cleanup())

  beforeEach(() => {
    navigateMock.mockReset()
  })

  it('renders agents without raw runtime language', () => {
    render(<AgentsPage />)

    expect(screen.getByRole('heading', { name: 'Agentlar' })).toBeDefined()
    expect(screen.getByText('Seller Agent')).toBeDefined()
    expect(screen.getAllByText('Ruxsatlar').length).toBeGreaterThan(0)
    expect(screen.queryByText('CRM Intel')).toBeNull()
  })

  it('opens the custom agent wizard through the canonical route', () => {
    render(<AgentsPage />)

    expect(screen.getByRole('link', { name: 'Yangi agent' }).getAttribute('href')).toBe('/agents/new')
  })
})
