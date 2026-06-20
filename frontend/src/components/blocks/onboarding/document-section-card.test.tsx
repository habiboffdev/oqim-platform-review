import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { DocumentSectionCard, type SectionDisplayStatus } from './document-section-card'
import type { OnboardingDocumentSection } from '@/lib/types'

const BODY = 'Biz Toshkentda kofe sotamiz'
const base: OnboardingDocumentSection = {
  key: 'overview',
  title: 'Biznes haqida',
  status: 'proposed',
  body: BODY,
  evidence_count: 2,
}
const noop = () => {}

function renderCard(displayStatus: SectionDisplayStatus, section: OnboardingDocumentSection = base) {
  return render(
    <DocumentSectionCard
      section={section}
      displayStatus={displayStatus}
      selected={false}
      onSelect={noop}
      onAccept={noop}
      onReject={noop}
      onEdit={noop}
    />,
  )
}

describe('DocumentSectionCard', () => {
  it('renders the prose body for proposed sections', () => {
    renderCard('proposed')
    expect(screen.getByText(BODY)).toBeDefined()
  })

  it('renders the prose body for approved sections', () => {
    renderCard('approved')
    expect(screen.getByText(BODY)).toBeDefined()
  })

  it('hides the body while a section is still generating', () => {
    renderCard('generating')
    expect(screen.queryByText(BODY)).toBeNull()
    expect(screen.getByText('Biznes haqida')).toBeDefined()
  })

  it('shows only the title (no body) for a queued/pending section', () => {
    renderCard('pending', { ...base, status: 'pending', body: '' })
    expect(screen.queryByText(BODY)).toBeNull()
    expect(screen.getByText('Biznes haqida')).toBeDefined()
  })
})
