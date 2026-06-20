// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import type { BrainObjectProjection } from '@/lib/types'

const mockNavigate = vi.fn()
let searchState: { tab?: string }
let objectQuery: {
  data?: BrainObjectProjection
  isLoading: boolean
  error: unknown
}
let businessMdQuery: {
  data?: {
    sections: Array<{
      id: number
      section_key: string
      title: string
      body: string
      order_index: number
      generated_by: string
      created_at: string
      updated_at: string
    }>
  }
  isLoading: boolean
  error: unknown
}
let agentsQuery: {
  data?: { items: Array<Record<string, unknown>> }
  isLoading: boolean
}
let agentDetailQuery: {
  data?: Record<string, unknown>
  isLoading: boolean
  error: unknown
}

vi.mock('@tanstack/react-router', () => ({
  useSearch: () => searchState,
  useNavigate: () => mockNavigate,
}))

vi.mock('@/hooks/use-business-brain', () => ({
  useBusinessBrainObjects: () => objectQuery,
  useBusinessMdDocument: () => businessMdQuery,
  useUpsertBusinessMdSection: () => ({ mutate: vi.fn(), isPending: false }),
}))

vi.mock('@/hooks/use-agent-workbench', () => ({
  useAgentWorkbenchAgents: () => agentsQuery,
  useAgentDetail: () => agentDetailQuery,
  useUpsertAgentSection: () => ({ mutate: vi.fn(), isPending: false }),
}))

import { BrainPage } from './brain'

describe('BrainPage object workbench', () => {
  afterEach(() => cleanup())

  beforeEach(() => {
    mockNavigate.mockClear()
    searchState = {}
    objectQuery = {
      data: projection(),
      isLoading: false,
      error: null,
    }
    businessMdQuery = {
      data: {
        sections: [
          {
            id: 1,
            section_key: 'business_context',
            title: 'Biznes konteksti',
            body: 'SATStation kurslar va texnika bo‘yicha mijozlarga yordam beradi.',
            order_index: 10,
            generated_by: 'oqim',
            created_at: '2026-05-17T08:00:00Z',
            updated_at: '2026-05-17T08:00:00Z',
          },
        ],
      },
      isLoading: false,
      error: null,
    }
    agentsQuery = {
      data: {
        items: [
          {
            id: 7,
            name: 'Sotuvchi',
            agent_type: 'seller',
            trust_mode: 'copilot',
            is_active: true,
            package_key: 'seller.default',
            permission_mode: 'ask_always',
            skill_count: 2,
            document_section_count: 2,
            tool_grant_count: 1,
            trigger_count: 1,
          },
        ],
      },
      isLoading: false,
    }
    agentDetailQuery = {
      data: agentDetail(),
      isLoading: false,
      error: null,
    }
  })

  it('renders Brain objects without raw source refs or provider states', () => {
    render(<BrainPage />)

    expect(screen.getByText('Biznes haqiqati')).toBeDefined()
    expect(screen.getAllByText('Yetkazish hududi').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Toshkent bo‘yicha yetkazish 1 kun ichida.').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Telegram: @satstation · matn bo‘lagi').length).toBeGreaterThan(0)
    expect(screen.getByText(/Kurs postidan olingan yetkazish qoidasi/)).toBeDefined()
    expect(screen.queryByText(/telegram:channel/i)).toBeNull()
    expect(screen.queryByText(/source_unit/i)).toBeNull()
    expect(screen.queryByText(/no_source_evidence/i)).toBeNull()
    expect(screen.queryByText(/^Manba$/)).toBeNull()
  })

  it('routes between object domains with canonical tab values', () => {
    const firstRender = render(<BrainPage />)

    const domainGroup = screen.getByRole('group', { name: 'Brain bo‘limlari' })
    fireEvent.click(within(domainGroup).getByRole('button', { name: /dalillar/i }))

    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/brain',
      search: { tab: 'sources' },
      replace: true,
    })

    mockNavigate.mockClear()
    firstRender.unmount()
    searchState = { tab: 'sources' }
    render(<BrainPage />)
    expect(screen.getAllByText('Narxlar PDF').length).toBeGreaterThan(0)
    expect(screen.queryByText('Yetkazish hududi')).toBeNull()
  })

  it('does not route old Brain tab aliases into current views', () => {
    searchState = { tab: 'business_md' }

    render(<BrainPage />)

    expect(screen.getByRole('button', { name: /hammasi/i }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getAllByText('Yetkazish hududi').length).toBeGreaterThan(0)
    expect(screen.queryByText('Biznes konteksti')).toBeNull()
  })

  it('filters rows and selects review issues from the right rail', () => {
    render(<BrainPage />)

    fireEvent.change(screen.getByLabelText('Brain qidirish'), {
      target: { value: 'chek' },
    })

    expect(screen.getAllByText('To‘lov cheki').length).toBeGreaterThan(0)
    expect(screen.queryByText('Yetkazish hududi')).toBeNull()

    fireEvent.change(screen.getByLabelText('Brain qidirish'), {
      target: { value: '' },
    })
    fireEvent.click(screen.getByRole('button', { name: /narxlar pdf/i }))

    expect(screen.getAllByText('O‘qilmadi').length).toBeGreaterThan(0)
  })

  it('opens selected evidence in the adaptive Brain rail', () => {
    render(<BrainPage />)

    fireEvent.click(screen.getByRole('button', { name: 'Brain dalillarini ochish' }))

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Tanlangan yozuv')).toBeDefined()
    expect(within(dialog).getAllByText('Dalillar').length).toBeGreaterThan(0)
    expect(within(dialog).getByText('Telegram: @satstation · matn bo‘lagi')).toBeDefined()
    expect(within(dialog).getByText(/Kurs postidan olingan yetkazish qoidasi/)).toBeDefined()
  })

  it('shows an honest empty state', () => {
    objectQuery = {
      data: projection([]),
      isLoading: false,
      error: null,
    }

    render(<BrainPage />)

    expect(screen.getByText('Brain hali bo‘sh')).toBeDefined()
    expect(screen.getByText(/Fayl, sayt, Telegram kanal/)).toBeDefined()
  })

  it('does not keep a stale selected object when a section is empty', () => {
    searchState = { tab: 'sources' }
    objectQuery = {
      data: projection(brainObjects().slice(0, 2)),
      isLoading: false,
      error: null,
    }

    render(<BrainPage />)

    expect(screen.getByText('Mos ma’lumot topilmadi')).toBeDefined()
    expect(screen.getByText('Jadvaldan obyekt tanlang.')).toBeDefined()
    expect(screen.queryByText('Yetkazish hududi')).toBeNull()
  })

  it('shows formatted generated documents instead of vague learned labels', () => {
    searchState = { tab: 'documents' }

    render(<BrainPage />)

    expect(screen.getAllByText('Hujjatlar').length).toBeGreaterThan(0)
    expect(screen.getAllByText('BUSINESS.md').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Biznes konteksti').length).toBeGreaterThan(0)
    expect(screen.getByText(/SATStation kurslar/)).toBeDefined()

    fireEvent.click(screen.getByRole('button', { name: /sotuvchi/i }))

    expect(screen.getAllByText('AGENT.md').length).toBeGreaterThan(0)
    expect(screen.getByText('Rol')).toBeDefined()
    expect(screen.getByText(/Mijozlarga aniq/)).toBeDefined()
    expect(screen.getAllByText('SKILL.md').length).toBeGreaterThan(0)
    expect(screen.getByText('Narxni tekshirish')).toBeDefined()
  })
})

function projection(objects = brainObjects()): BrainObjectProjection {
  return {
    schema_version: 'brain_object_projection.v1',
    workspace_id: 1,
    objects,
    counts: {
      catalog: objects.filter((item) => item.domain === 'catalog').length,
      knowledge: objects.filter((item) => item.domain === 'knowledge').length,
      rules: objects.filter((item) => item.domain === 'rules').length,
      voice: objects.filter((item) => item.domain === 'voice').length,
      examples: objects.filter((item) => item.domain === 'examples').length,
      issues: objects.filter((item) => item.domain === 'issues').length,
      sources: objects.filter((item) => item.domain === 'sources').length,
    },
    issues_count: objects.filter((item) => item.needs_review).length,
    ready_count: objects.filter((item) => item.status === 'ready').length,
    review_count: objects.filter((item) => item.needs_review).length,
  }
}

function brainObjects(): BrainObjectProjection['objects'] {
  return [
    {
      schema_version: 'brain_object_item.v1',
      object_id: 'knowledge:delivery',
      domain: 'knowledge',
      title: 'Yetkazish hududi',
      summary: 'Toshkent bo‘yicha yetkazish 1 kun ichida.',
      status: 'ready',
      status_label: 'Agentga tayyor',
      confidence: 0.88,
      risk_tier: 'low',
      source_lifecycle: 'live',
      evidence: [
        {
          schema_version: 'brain_object_evidence.v1',
          label: 'Telegram: @satstation',
          kind: 'telegram',
          freshness_label: 'Bugun',
          detail: 'Kurs postidan olingan yetkazish qoidasi.',
          unit_label: 'matn bo‘lagi',
          source_ref: 'source_unit:business_source:telegram:channel:satstation:ingested:000',
        },
      ],
      evidence_count: 1,
      updated_at: '2026-05-17T08:00:00Z',
      can_edit: true,
      can_archive: true,
      needs_review: false,
      fact_ids: ['knowledge:delivery'],
      proposal_refs: [],
    },
    {
      schema_version: 'brain_object_item.v1',
      object_id: 'rule:payment',
      domain: 'rules',
      title: 'To‘lov cheki',
      summary: 'Chek ko‘rinmasa, mijozdan qayta yuborishni so‘raydi.',
      status: 'ready',
      status_label: 'Agentga tayyor',
      confidence: 0.9,
      risk_tier: 'low',
      source_lifecycle: 'live',
      evidence: [
        {
          schema_version: 'brain_object_evidence.v1',
          label: 'Qo‘lda kiritilgan',
          kind: 'manual',
          freshness_label: 'Bugun',
          source_ref: 'owner:manual:payment-rule',
        },
      ],
      evidence_count: 1,
      updated_at: '2026-05-17T08:01:00Z',
      can_edit: true,
      can_archive: true,
      needs_review: false,
      fact_ids: ['rule:payment'],
      proposal_refs: [],
    },
    {
      schema_version: 'brain_object_item.v1',
      object_id: 'source:price-pdf',
      domain: 'sources',
      title: 'Narxlar PDF',
      summary: 'Bu manbani o‘qishda muammo bo‘ldi. Qayta urinib ko‘rish mumkin.',
      status: 'degraded',
      status_label: 'Yordam kerak',
      confidence: 0.72,
      risk_tier: 'low',
      source_lifecycle: 'failed',
      evidence: [
        {
          schema_version: 'brain_object_evidence.v1',
          label: 'Fayl: Narxlar PDF',
          kind: 'file',
          freshness_label: 'Bugun',
          source_ref: 'brain:source:file:price-pdf',
        },
      ],
      evidence_count: 1,
      updated_at: '2026-05-17T08:02:00Z',
      can_edit: false,
      can_archive: true,
      needs_review: true,
      fact_ids: ['source:price-pdf'],
      proposal_refs: [],
    },
  ]
}

function agentDetail() {
  return {
    schema_version: 'intelligence_agent_detail.v1',
    agent: {
      id: 7,
      name: 'Sotuvchi',
      agent_type: 'seller',
      trust_mode: 'copilot',
      is_active: true,
      package_key: 'seller.default',
      permission_mode: 'ask_always',
      skill_count: 1,
      document_section_count: 2,
      tool_grant_count: 1,
      trigger_count: 1,
      contact_scope: 'all',
    },
    enforced_config: {
      permission_mode: 'ask_always',
      trust_mode: 'copilot',
      is_active: true,
      contact_scope: 'all',
      brain_scopes: ['knowledge', 'rules', 'catalog'],
      tool_scopes: ['telegram.read_messages'],
      channel_mode: 'workspace_events',
    },
    drift_warnings: [],
    sections: [
      {
        id: 1,
        section_key: 'role',
        title: 'Rol',
        body: 'Mijozlarga aniq, qisqa va dalil bilan javob beradi.',
        order_index: 10,
        generated_by: 'oqim',
        source_evidence: [],
      },
      {
        id: 2,
        section_key: 'permission',
        title: 'Ruxsat',
        body: 'Xavfli ishni egadan tasdiq olmasdan bajarmaydi.',
        order_index: 20,
        generated_by: 'owner',
        source_evidence: [],
      },
    ],
    skills: [
      {
        id: 1,
        slug: 'price-check',
        name: 'Narxni tekshirish',
        description: 'Narx va mavjudlikni faqat Brain dalili bilan aytadi.',
        instructions: 'Katalogdan qidir.',
        when_to_use: 'Mijoz narx, mavjudlik yoki chegirma haqida so‘raganda.',
        when_not_to_use: '',
        tools: ['catalog.search'],
        enabled: true,
      },
    ],
    tool_grants: [],
    triggers: [],
    recent_actions: [],
    rendered: {
      kind: 'agent',
      title: 'Sotuvchi',
      markdown: '# Sotuvchi',
      sections_used: 2,
    },
  }
}
