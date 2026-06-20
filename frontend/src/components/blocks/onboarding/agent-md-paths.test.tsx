// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { uz } from '@/lib/uz'

const { mockMutate } = vi.hoisted(() => ({ mockMutate: vi.fn() }))

vi.mock('@/hooks/use-onboarding-documents', () => ({
  useGenerateAgentMd: () => ({ mutate: mockMutate, isPending: false }),
}))

// The speak path imports the transcript helper at module load; stub it so the
// suite never reaches network or MediaRecorder.
vi.mock('./audio-transcription', () => ({
  transcribeOnboardingAudio: vi.fn(),
}))

import { AgentMdPaths } from './agent-md-paths'

const t = uz.onboarding.documents.agentPaths

describe('AgentMdPaths', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the three path options when an agent exists', () => {
    render(<AgentMdPaths agentId={7} />)
    expect(screen.getByRole('tab', { name: t.tabDefaults })).toBeDefined()
    expect(screen.getByRole('tab', { name: t.tabSpeak })).toBeDefined()
    expect(screen.getByRole('tab', { name: t.tabUpload })).toBeDefined()
  })

  it('renders a calm empty state and no tabs when no agent exists yet', () => {
    render(<AgentMdPaths agentId={undefined} />)
    expect(screen.getByText(t.emptyTitle)).toBeDefined()
    expect(screen.queryByRole('tab', { name: t.tabDefaults })).toBeNull()
  })

  it('Defaults Yaratish generates with an empty owner_input', () => {
    render(<AgentMdPaths agentId={7} />)
    fireEvent.click(screen.getByRole('button', { name: t.generate }))
    expect(mockMutate).toHaveBeenCalledTimes(1)
    expect(mockMutate).toHaveBeenCalledWith('')
  })

  it('Upload textarea feeds owner_input into the generate mutation', () => {
    render(<AgentMdPaths agentId={7} />)
    fireEvent.click(screen.getByRole('tab', { name: t.tabUpload }))

    const textarea = screen.getByLabelText(t.ownerInputLabel)
    fireEvent.change(textarea, { target: { value: 'Doim hurmat bilan javob ber' } })

    fireEvent.click(screen.getByRole('button', { name: t.generate }))
    expect(mockMutate).toHaveBeenCalledWith('Doim hurmat bilan javob ber')
  })
})
