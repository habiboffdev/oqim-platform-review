import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api-client'
import { queryKeys } from '@/lib/query-keys'
import type {
  BusinessBrainFactsResponse,
  BrainObjectDomain,
  BrainObjectProjection,
  BusinessBrainFactReviewActionInput,
  BusinessBrainFactReviewActionResult,
  BusinessBrainManualFactUpdateInput,
  BusinessBrainManualFactUpdateResult,
  BusinessBrainSourceCreateInput,
  BusinessBrainSourceCreateResponse,
  BusinessBrainSourceControlInput,
  BusinessBrainSourceControlResponse,
  BusinessBrainSourceLearningResult,
  BrainObjectItem,
  CatalogWorkspaceProduct,
  CatalogWorkspaceProjection,
  OnboardingSourceLearningProjection,
  SourceIntakeProjection,
} from '@/lib/types'

function catalogIndexState(status: BrainObjectItem['status']): CatalogWorkspaceProduct['index_state'] {
  if (status === 'ready') return 'ready'
  if (status === 'archived') return 'unavailable'
  return 'degraded'
}

function catalogExtractionState(status: BrainObjectItem['status']): CatalogWorkspaceProduct['extraction_state'] {
  return status === 'archived' ? 'unavailable' : 'available'
}

function catalogProductFromBrainObject(item: BrainObjectItem): CatalogWorkspaceProduct {
  const sourceRefs = item.evidence
    .map((evidence) => evidence.source_ref)
    .filter((sourceRef): sourceRef is string => Boolean(sourceRef))
  return {
    schema_version: 'catalog_workspace_product.v1',
    product_ref: item.object_id,
    product: {
      title: item.title,
      description: item.summary,
      status: item.status,
      status_label: item.status_label,
      confidence: item.confidence,
    },
    variants: [],
    offers: [],
    media: [],
    source_refs: sourceRefs.length ? sourceRefs : item.fact_ids,
    conflict_refs: item.status === 'conflict' ? item.fact_ids : [],
    index_state: catalogIndexState(item.status),
    extraction_state: catalogExtractionState(item.status),
  }
}

function catalogProjectionFromBrainObjects(projection: BrainObjectProjection): CatalogWorkspaceProjection {
  return {
    schema_version: 'catalog_workspace_projection.v1',
    workspace_id: projection.workspace_id,
    products: projection.objects.map(catalogProductFromBrainObject),
  }
}

export function useBrainCatalog() {
  return useQuery({
    queryKey: queryKeys.businessBrain.catalog,
    queryFn: async () => catalogProjectionFromBrainObjects(
      await api.get<BrainObjectProjection>('/api/business-brain/objects?domain=catalog&limit=250'),
    ),
    staleTime: 20_000,
  })
}

export function useBusinessBrainFacts() {
  return useQuery({
    queryKey: queryKeys.businessBrain.facts,
    queryFn: () => api.get<BusinessBrainFactsResponse>('/api/business-brain/facts'),
    staleTime: 20_000,
  })
}

export function useBusinessBrainObjects(domain?: BrainObjectDomain) {
  return useQuery({
    queryKey: queryKeys.businessBrain.objects(domain),
    queryFn: () => {
      const params = new URLSearchParams()
      if (domain) params.set('domain', domain)
      params.set('limit', '250')
      const suffix = params.toString()
      return api.get<BrainObjectProjection>(`/api/business-brain/objects${suffix ? `?${suffix}` : ''}`)
    },
    staleTime: 20_000,
  })
}

export function useBusinessBrainSources() {
  return useQuery({
    queryKey: queryKeys.businessBrain.sources,
    queryFn: () => api.get<OnboardingSourceLearningProjection>('/api/business-brain/sources'),
    staleTime: 20_000,
  })
}

export function useBusinessBrainSourceIntake() {
  return useQuery({
    queryKey: queryKeys.businessBrain.sourceIntake,
    queryFn: () => api.get<SourceIntakeProjection>('/api/business-brain/source-intake'),
    staleTime: 20_000,
  })
}

export function useBusinessBrainFactReviewAction() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: BusinessBrainFactReviewActionInput) =>
      api.post<BusinessBrainFactReviewActionResult>('/api/business-brain/facts/review-actions', payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.facts })
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.objects() })
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.sources })
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.sourceIntake })
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.catalog })
      void queryClient.invalidateQueries({ queryKey: queryKeys.actionRuntime.inbox })
    },
  })
}

export function useBusinessBrainManualFactUpdate() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: BusinessBrainManualFactUpdateInput) =>
      api.post<BusinessBrainManualFactUpdateResult>('/api/business-brain/facts/manual', payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.facts })
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.objects() })
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.sources })
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.sourceIntake })
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.catalog })
      void queryClient.invalidateQueries({ queryKey: queryKeys.actionRuntime.inbox })
    },
  })
}

export function useCreateBusinessBrainSource() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: BusinessBrainSourceCreateInput) =>
      api.post<BusinessBrainSourceCreateResponse>('/api/business-brain/sources', payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.facts })
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.objects() })
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.sources })
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.sourceIntake })
    },
  })
}

export function useRunBusinessBrainSourceLearning() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: { limit?: number; max_attempts?: number; background?: boolean } = {}) =>
      api.post<BusinessBrainSourceLearningResult>('/api/business-brain/sources/learn', payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.facts })
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.objects() })
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.sources })
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.sourceIntake })
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.catalog })
      void queryClient.invalidateQueries({ queryKey: queryKeys.actionRuntime.inbox })
      void queryClient.invalidateQueries({ queryKey: queryKeys.onboarding.runtime })
    },
  })
}

export function useRetryBusinessBrainSourceLearning() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: { source_ref?: string; limit?: number; max_attempts?: number; background?: boolean } = {}) =>
      api.post<BusinessBrainSourceLearningResult>('/api/business-brain/sources/retry', payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.facts })
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.objects() })
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.sources })
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.sourceIntake })
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.catalog })
      void queryClient.invalidateQueries({ queryKey: queryKeys.actionRuntime.inbox })
      void queryClient.invalidateQueries({ queryKey: queryKeys.onboarding.runtime })
    },
  })
}

export function useBusinessBrainSourceControl() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: BusinessBrainSourceControlInput) =>
      api.post<BusinessBrainSourceControlResponse>('/api/business-brain/source-intake/actions', payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.facts })
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.objects() })
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.sources })
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.sourceIntake })
      void queryClient.invalidateQueries({ queryKey: queryKeys.actionRuntime.inbox })
      void queryClient.invalidateQueries({ queryKey: queryKeys.onboarding.runtime })
    },
  })
}

export interface BusinessMdSection {
  id: number
  section_key: string
  title: string
  body: string
  order_index: number
  generated_by: string
  created_at: string
  updated_at: string
}

export interface BusinessMdDocument {
  schema_version: 'business_md_document.v1'
  workspace_id: number
  rendered: {
    kind: 'business'
    title: string
    markdown: string
    sections_used: number
  }
  sections: BusinessMdSection[]
}

export interface BusinessMdSectionInput {
  section_key: string
  title: string
  body?: string
  order_index?: number
  generated_by?: string
}

export function useBusinessMdDocument() {
  return useQuery({
    queryKey: queryKeys.businessBrain.businessMd,
    queryFn: () => api.get<BusinessMdDocument>('/api/business-brain/business-md'),
    staleTime: 30_000,
  })
}

export function useUpsertBusinessMdSection() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: BusinessMdSectionInput) =>
      api.post<{ schema_version: 'business_md_section.v1'; section: BusinessMdSection }>(
        '/api/business-brain/business-md/sections',
        payload,
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.businessBrain.businessMd })
    },
  })
}
