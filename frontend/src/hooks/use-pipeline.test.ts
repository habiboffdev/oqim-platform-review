// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ReactNode } from 'react'
import { usePipeline, useUpdatePipelineStage } from './use-pipeline'

vi.mock('@/lib/api-client', () => ({
  api: {
    get: vi.fn(),
    patch: vi.fn(),
  },
}))

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}))

import { api } from '@/lib/api-client'

const mockApi = api as unknown as { get: ReturnType<typeof vi.fn>; patch: ReturnType<typeof vi.fn> }

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: queryClient }, children)
}

describe('usePipeline', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('uses backend crm_pipeline projection without client-side stage aliases', async () => {
    mockApi.get.mockResolvedValue({
      schema_version: 'crm_pipeline.v1',
      total: 1,
      stages: [
        {
          stage: 'qualified',
          count: 1,
          cards: [
            {
              conversation_id: 38,
              customer_id: 38,
              customer_name: 'Husnida Akhrorkulova',
              channel: 'telegram_dm',
              stage: {
                schema_version: 'crm_stage.v1',
                stage: 'qualified',
                source: 'crm_state',
                raw_stage: 'talking',
                normalized_from: 'talking',
                confidence: 0.82,
                products_interested: [],
                needs_attention: false,
                field_provenance: {},
              },
              unread_count: 0,
              has_pending_reply: false,
              needs_attention: false,
            },
          ],
        },
      ],
    })

    const { result } = renderHook(() => usePipeline(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockApi.get).toHaveBeenCalledWith('/api/conversations/pipeline')
    expect(result.current.data?.schema_version).toBe('crm_pipeline.v1')
    expect(result.current.data?.stages[0].cards[0].stage.normalized_from).toBe('talking')
  })

  it('sends backend crm_stage stage names when dragging kanban cards', async () => {
    mockApi.patch.mockResolvedValue({})

    const { result } = renderHook(() => useUpdatePipelineStage(), { wrapper: createWrapper() })

    result.current.mutate({ conversationId: 42, stage: 'negotiation' })

    await waitFor(() => {
      expect(mockApi.patch).toHaveBeenCalledWith('/api/conversations/42', {
        pipeline_stage: 'negotiation',
      })
    })
  })
})
