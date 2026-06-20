// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { uz } from '@/lib/uz'

const { mockUseOnboardingDocuments } = vi.hoisted(() => ({
  mockUseOnboardingDocuments: vi.fn(),
}))

vi.mock('@/hooks/use-onboarding-documents', () => ({
  useOnboardingDocuments: () => mockUseOnboardingDocuments(),
}))

import type { DefaultAgentKey } from './types'
import { PhaseLaunch } from './phase-launch'

const ALL_AGENTS: DefaultAgentKey[] = ['seller', 'support', 'catalog_update', 'follow_up', 'bi']

function projection() {
  return {
    data: {
      schema_version: 'onboarding_documents.v1',
      workspace_id: 1,
      running: false,
      current_doc: null,
      error: null,
      percent: 100,
      documents: {
        business: { total: 6, approved: 4, proposed: 1, generating: null, sections: [] },
        agent: { total: 4, approved: 2, proposed: 0, generating: null, sections: [] },
        skill: { status: 'proposed', candidates: 3 },
      },
    },
  }
}

describe('PhaseLaunch', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockUseOnboardingDocuments.mockReturnValue(projection())
  })

  it('renders the 5 default agents and the two launch actions', () => {
    render(
      <PhaseLaunch
        permissionMode="ask_always"
        enabledDefaultAgents={ALL_AGENTS}
        isSubmitting={false}
        onLaunch={vi.fn()}
      />,
    )

    expect(screen.getByText(uz.onboarding.launch.agentSeller)).toBeDefined()
    expect(screen.getByText(uz.onboarding.launch.agentSupport)).toBeDefined()
    expect(screen.getByText(uz.onboarding.launch.agentCatalogUpdate)).toBeDefined()
    expect(screen.getByText(uz.onboarding.launch.agentFollowUp)).toBeDefined()
    expect(screen.getByText(uz.onboarding.launch.agentBi)).toBeDefined()

    expect(screen.getByRole('button', { name: new RegExp(uz.onboarding.launch.start) })).toBeDefined()
    expect(screen.getByRole('button', { name: uz.onboarding.launch.later })).toBeDefined()
  })

  it('shows learned counts from the documents projection', () => {
    render(
      <PhaseLaunch
        permissionMode="ask_always"
        enabledDefaultAgents={ALL_AGENTS}
        isSubmitting={false}
        onLaunch={vi.fn()}
      />,
    )

    // business approved+proposed / total = 5/6, agent = 2/4, skill candidates = 3
    expect(screen.getByText(uz.onboarding.launch.learnedReady(5, 6))).toBeDefined()
    expect(screen.getByText(uz.onboarding.launch.learnedReady(2, 4))).toBeDefined()
    expect(screen.getByText(uz.onboarding.launch.learnedCount(3))).toBeDefined()
  })

  it('calls onLaunch with "start" and "later" for each action', () => {
    const onLaunch = vi.fn()
    render(
      <PhaseLaunch
        permissionMode="ask_always"
        enabledDefaultAgents={ALL_AGENTS}
        isSubmitting={false}
        onLaunch={onLaunch}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: new RegExp(uz.onboarding.launch.start) }))
    expect(onLaunch).toHaveBeenCalledWith('start')

    fireEvent.click(screen.getByRole('button', { name: uz.onboarding.launch.later }))
    expect(onLaunch).toHaveBeenCalledWith('later')
  })

  it('disables both actions while submitting', () => {
    render(
      <PhaseLaunch
        permissionMode="ask_always"
        enabledDefaultAgents={ALL_AGENTS}
        isSubmitting
        onLaunch={vi.fn()}
      />,
    )

    expect(
      screen.getByRole('button', { name: new RegExp(uz.onboarding.launch.start) }).hasAttribute('disabled'),
    ).toBe(true)
    expect(
      screen.getByRole('button', { name: uz.onboarding.launch.later }).hasAttribute('disabled'),
    ).toBe(true)
  })
})
