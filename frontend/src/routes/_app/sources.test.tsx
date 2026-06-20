// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import type { SourceIntakeProjection } from '@/lib/types'

const mockNavigate = vi.fn()
const retryMutate = vi.fn()
const controlMutate = vi.fn()
const createMutateAsync = vi.fn()
const learnMutateAsync = vi.fn()
let searchState: { lifecycle?: string; kind?: string }
let sourceQuery: {
  data?: SourceIntakeProjection
  isLoading: boolean
  error: unknown
}

vi.mock('@tanstack/react-router', () => ({
  useSearch: () => searchState,
  useNavigate: () => mockNavigate,
}))

vi.mock('@/hooks/use-business-brain', () => ({
  useBusinessBrainSourceIntake: () => sourceQuery,
  useBusinessBrainFacts: () => ({ data: { items: [] }, isLoading: false, error: null }),
  useCreateBusinessBrainSource: () => ({
    mutateAsync: createMutateAsync,
    isPending: false,
  }),
  useRunBusinessBrainSourceLearning: () => ({
    mutateAsync: learnMutateAsync,
    isPending: false,
  }),
  useRetryBusinessBrainSourceLearning: () => ({
    mutate: retryMutate,
    isPending: false,
  }),
  useBusinessBrainSourceControl: () => ({
    mutate: controlMutate,
    isPending: false,
  }),
}))

vi.mock('@/components/ui/scroll-area', () => ({
  ScrollArea: ({ children, className }: { children: ReactNode; className?: string }) => (
    <div className={className}>{children}</div>
  ),
}))

import { SourcesPage } from './sources'

describe('SourcesPage', () => {
  afterEach(() => cleanup())

  beforeEach(() => {
    mockNavigate.mockClear()
    retryMutate.mockClear()
    controlMutate.mockClear()
    createMutateAsync.mockReset()
    learnMutateAsync.mockReset()
    createMutateAsync.mockResolvedValue({ source_ref: 'brain:source:new' })
    learnMutateAsync.mockResolvedValue({})
    searchState = {}
    sourceQuery = {
      data: projection(),
      isLoading: false,
      error: null,
    }
  })

  it('renders owner-facing source state without raw refs or provider errors', () => {
    render(<SourcesPage />)

    expect(screen.getByRole('heading', { name: 'Manbalar' })).toBeDefined()
    expect(screen.getAllByText('@catalog').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Narxlar PDF').length).toBeGreaterThan(0)
    expect(screen.queryByText(/brain:source/i)).toBeNull()
    expect(screen.queryByText(/fetch_failed/i)).toBeNull()
    expect(screen.queryByText(/no_source_evidence/i)).toBeNull()
  })

  it('filters by lifecycle and kind through route search params', () => {
    render(<SourcesPage />)

    const lifecycleGroup = screen.getByRole('group', { name: 'Manba holatlari' })
    fireEvent.click(within(lifecycleGroup).getByRole('button', { name: /Yordam/i }))

    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/sources',
      search: { lifecycle: 'failed' },
      replace: true,
    })

    mockNavigate.mockClear()
    fireEvent.click(screen.getByRole('button', { name: /Fayl1/i }))
    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/sources',
      search: { kind: 'file' },
      replace: true,
    })
  })

  it('searches visible source results and can retry a failed source', () => {
    render(<SourcesPage />)

    fireEvent.change(screen.getByLabelText('Manbalarni qidirish'), {
      target: { value: 'pdf' },
    })

    expect(screen.getAllByText('Narxlar PDF').length).toBeGreaterThan(0)
    expect(screen.queryByText('@catalog')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /Qayta o‘qish/i }))
    expect(retryMutate).toHaveBeenCalledWith({
      source_ref: 'brain:source:file:bad-pdf',
      limit: 1,
      max_attempts: 1,
    })
  })

  it('adds a Telegram source and starts learning', async () => {
    render(<SourcesPage />)

    fireEvent.click(screen.getByRole('button', { name: /^Manba$/i }))
    fireEvent.change(screen.getByLabelText('Kanal nomi'), {
      target: { value: '@new_channel' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Qo‘shish' }))

    await waitFor(() => {
      expect(createMutateAsync).toHaveBeenCalledWith({
        kind: 'telegram_channel',
        handle: '@new_channel',
        label: '@new_channel',
      })
    })
    expect(learnMutateAsync).toHaveBeenCalledWith({ limit: 1, max_attempts: 2 })
  })

  it('can pause and archive a live source through source controls', () => {
    render(<SourcesPage />)

    fireEvent.click(screen.getByRole('button', { name: /Kuzatishni to‘xtatish/i }))
    expect(controlMutate).toHaveBeenCalledWith({
      source_ref: 'telegram:channel:catalog',
      action: 'pause',
      idempotency_key: 'source-control:pause:telegram:channel:catalog',
    })

    fireEvent.click(screen.getByRole('button', { name: /Arxivlash/i }))
    expect(controlMutate).toHaveBeenCalledWith({
      source_ref: 'telegram:channel:catalog',
      action: 'archive',
      idempotency_key: 'source-control:archive:telegram:channel:catalog',
    })
  })

  it('shows an honest empty state', () => {
    sourceQuery = {
      data: projection([]),
      isLoading: false,
      error: null,
    }

    render(<SourcesPage />)

    expect(screen.getByText('Hali manba yo‘q')).toBeDefined()
    expect(screen.getAllByText(/Fayl, sayt, Telegram/).length).toBeGreaterThan(0)
  })
})

function projection(sources = sourceItems()): SourceIntakeProjection {
  return {
    schema_version: 'source_intake_projection.v1',
    workspace_id: 1,
    sources,
    counts: {
      live: sources.filter((item) => item.lifecycle === 'live').length,
      snapshot: sources.filter((item) => item.lifecycle === 'snapshot').length,
      learning: sources.filter((item) => item.lifecycle === 'learning').length,
      needs_review: sources.filter((item) => item.lifecycle === 'needs_review').length,
      retrying: sources.filter((item) => item.lifecycle === 'retrying').length,
      failed: sources.filter((item) => item.lifecycle === 'failed').length,
      conflicting: sources.filter((item) => item.lifecycle === 'conflicting').length,
      archived: sources.filter((item) => item.lifecycle === 'archived').length,
    },
    kind_counts: sources.reduce<Record<string, number>>((acc, item) => {
      acc[item.kind] = (acc[item.kind] ?? 0) + 1
      return acc
    }, {}),
    ready_count: sources.filter((item) => item.lifecycle === 'live' || item.lifecycle === 'snapshot').length,
    review_count: sources.filter((item) => item.lifecycle === 'needs_review' || item.lifecycle === 'conflicting').length,
    failed_count: sources.filter((item) => item.lifecycle === 'failed').length,
    live_count: sources.filter((item) => item.lifecycle === 'live').length,
  }
}

function sourceItems(): SourceIntakeProjection['sources'] {
  return [
    {
      schema_version: 'source_intake_item.v1',
      source_ref: 'telegram:channel:catalog',
      title: '@catalog',
      kind: 'telegram_channel',
      kind_label: 'Telegram kanal',
      purpose: 'brain_data',
      purpose_label: "Javob ma'lumoti",
      lifecycle: 'live',
      status_label: "Jonli o'qiladi",
      summary: '4 matn bo‘lagi, 2 media dalil topildi.',
      preview: '@catalog',
      learned_object_count: 1,
      learned_object_labels: ['Katalog'],
      source_unit_count: 4,
      media_count: 2,
      issue_label: null,
      retryable: false,
      can_retry: false,
      can_archive: true,
      can_pause: true,
      can_resume: false,
      fact_id: 'source:catalog-channel',
      updated_at: '2026-05-17T08:00:00Z',
    },
    {
      schema_version: 'source_intake_item.v1',
      source_ref: 'brain:source:file:bad-pdf',
      title: 'Narxlar PDF',
      kind: 'file',
      kind_label: 'Fayl',
      purpose: 'brain_data',
      purpose_label: "Javob ma'lumoti",
      lifecycle: 'failed',
      status_label: 'Yordam kerak',
      summary: "Manbaga ulanishda muammo bo'ldi.",
      preview: 'narxlar.pdf',
      learned_object_count: 0,
      learned_object_labels: [],
      source_unit_count: 0,
      media_count: 0,
      issue_label: "Manbaga ulanishda muammo bo'ldi.",
      retryable: true,
      can_retry: true,
      can_archive: true,
      can_pause: false,
      can_resume: false,
      fact_id: 'source:bad-pdf',
      updated_at: '2026-05-17T08:01:00Z',
    },
  ]
}
