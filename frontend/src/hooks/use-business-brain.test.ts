// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useBrainCatalog } from './use-business-brain'

vi.mock('@/lib/api-client', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

import { api } from '@/lib/api-client'

const mockApi = api as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
}

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: queryClient }, children)
}

describe('useBrainCatalog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('loads catalog products from the current Business Brain object projection', async () => {
    mockApi.get.mockResolvedValue({
      schema_version: 'brain_object_projection.v1',
      workspace_id: 7,
      objects: [
        {
          schema_version: 'brain_object_item.v1',
          object_id: 'catalog_product:sat-guide',
          domain: 'catalog',
          title: 'SAT English Guide',
          summary: 'Three-page guide.',
          status: 'ready',
          status_label: 'Ready',
          confidence: 0.94,
          risk_tier: 'low',
          source_lifecycle: 'live',
          evidence: [
            {
              schema_version: 'brain_object_evidence.v1',
              label: 'Telegram channel',
              kind: 'telegram',
              freshness_label: 'fresh',
              source_ref: 'telegram_channel:7:42',
            },
          ],
          evidence_count: 1,
          updated_at: '2026-06-08T00:00:00Z',
          can_edit: true,
          can_archive: true,
          needs_review: false,
          fact_ids: ['fact:catalog:sat-guide'],
          proposal_refs: [],
        },
      ],
      counts: { catalog: 1 },
      issues_count: 0,
      ready_count: 1,
      review_count: 0,
    })

    const wrapper = createWrapper()
    const { result } = renderHook(() => useBrainCatalog(), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockApi.get).toHaveBeenCalledWith('/api/business-brain/objects?domain=catalog&limit=250')
    expect(result.current.data).toMatchObject({
      schema_version: 'catalog_workspace_projection.v1',
      workspace_id: 7,
      products: [
        {
          product_ref: 'catalog_product:sat-guide',
          product: { title: 'SAT English Guide' },
          source_refs: ['telegram_channel:7:42'],
          index_state: 'ready',
          extraction_state: 'available',
        },
      ],
    })
  })
})
