// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { DocumentToggle } from './document-toggle'
import type { OnboardingDocumentsProjection } from '@/lib/types'

function makeProjection(
  overrides: Partial<OnboardingDocumentsProjection['documents']> = {},
): OnboardingDocumentsProjection {
  return {
    schema_version: 'onboarding_documents.v1',
    workspace_id: 1,
    running: true,
    current_doc: 'business',
    error: null,
    percent: 40,
    documents: {
      business: { total: 10, approved: 4, proposed: 3, generating: null, sections: [] },
      agent: { total: 6, approved: 6, proposed: 0, generating: null, sections: [] },
      skill: { status: 'proposed', candidates: 3 },
      ...overrides,
    },
  }
}

describe('DocumentToggle', () => {
  it('renders counts as approved+proposed over total, and skill candidates', () => {
    render(
      <DocumentToggle active="business" onChange={() => {}} projection={makeProjection()} />,
    )
    // business: (4 + 3)/10
    expect(screen.getByText('7/10')).toBeTruthy()
    // agent: (6 + 0)/6
    expect(screen.getByText('6/6')).toBeTruthy()
    // skill: candidates only
    expect(screen.getByText('3')).toBeTruthy()
  })

  it('renders zero counts when projection is undefined', () => {
    render(<DocumentToggle active="business" onChange={() => {}} projection={undefined} />)
    expect(screen.getAllByText('0/0')).toHaveLength(2)
    expect(screen.getByText('0')).toBeTruthy()
  })

  it('marks the active tab and fires onChange for another tab', () => {
    const onChange = vi.fn()
    render(
      <DocumentToggle active="business" onChange={onChange} projection={makeProjection()} />,
    )
    const tabs = screen.getAllByRole('tab')
    const active = tabs.find((tab) => tab.getAttribute('aria-selected') === 'true')
    expect(active?.textContent).toContain('7/10')

    const agentTab = tabs.find((tab) => tab.textContent?.includes('6/6'))
    fireEvent.click(agentTab!)
    expect(onChange).toHaveBeenCalledWith('agent')
  })
})
