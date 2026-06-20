import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@/lib/api-client'
import { queryKeys } from '@/lib/query-keys'
import { uz } from '@/lib/uz'
import type { CommercialActionProposal } from '@/lib/types'

export interface AgentWorkbenchRow {
  id: number
  name: string
  agent_type: string
  trust_mode: string
  is_active: boolean
  package_key: string
  permission_mode: string
  skill_count: number
  document_section_count: number
  tool_grant_count: number
  trigger_count: number
}

export type WizardAgentKind = 'seller' | 'support' | 'follow_up' | 'custom'

export interface WizardSectionDraft {
  section_key: string
  title: string
  body: string
  order_index: number
}

export interface CreateCustomAgentInput {
  name: string
  agent_kind: WizardAgentKind
  mission: string
  permission_mode: 'ask_always' | 'auto_approve' | 'full_access'
  brain_scopes: string[]
  tool_scopes: string[]
  trigger_sources: string[]
  sections?: WizardSectionDraft[]
  starter_skill_name?: string
  starter_skill_instructions?: string
}

export interface CreateCustomAgentResponse {
  schema_version: string
  created: boolean
  agent: { id: number; name: string; agent_type: string; trust_mode: string; is_active: boolean }
  package_key: string
  permission_mode: string
}

export interface DraftCustomAgentInput {
  agent_kind: WizardAgentKind
  name: string
  does_what: string
  when_replies?: string
  never_does?: string
}

export interface DraftCustomAgentResponse {
  schema_version: string
  agent_kind: WizardAgentKind
  name: string
  sections: WizardSectionDraft[]
  brain_scopes: string[]
  tool_scopes: string[]
  trigger_sources: string[]
  permission_mode: 'ask_always' | 'auto_approve' | 'full_access'
  trust_mode: string
}

export interface AgentToolGrantProposalInput {
  action: 'grant' | 'revoke'
  scope: string
  reason?: string
  correlation_id?: string
  idempotency_key?: string
}

export interface AgentToolGrantProposalResponse {
  schema_version: 'agent_tool_grant_proposal.v1'
  created: boolean
  proposal: CommercialActionProposal
}

export interface AgentToolCatalogItem {
  scope: string
  connector: string
  verb: string
  label_uz: string
  description_uz: string
  short_label: string
  operation_kind: 'read' | 'write' | 'watch' | 'sync' | 'media'
  risk_level: 'low' | 'medium' | 'high'
  mutates_external_state: boolean
  requires_action_proposal: boolean
  default_permission_mode: 'ask_always' | 'auto_approve' | 'full_access'
  owner_visible: boolean
  runtime_boundary: string
}

export interface AgentToolCatalogResponse {
  schema_version: 'intelligence_tool_catalog.v1'
  items: AgentToolCatalogItem[]
}

export interface AgentTriggerProposalInput {
  operation: 'create' | 'deactivate'
  trigger_id?: number
  event_source?: string
  action_proposal_type?: string
  matching_scope?: Record<string, unknown>
  permission_mode?: 'ask_always' | 'auto_approve' | 'full_access'
  retry_policy?: Record<string, unknown>
  notes?: string
  correlation_id?: string
}

export interface AgentTriggerProposalResponse {
  schema_version: 'agent_trigger_proposal.v1'
  created: boolean
  proposal: CommercialActionProposal
}

export interface AgentDocumentSection {
  id: number
  section_key: string
  title: string
  body: string
  order_index: number
  generated_by: string
  source_evidence?: Array<Record<string, unknown>>
}

export interface AgentWorkbenchSkill {
  id: number
  slug: string
  name: string
  description: string
  instructions: string
  when_to_use: string
  when_not_to_use: string
  tools: string[]
  enabled: boolean
}

export interface AgentToolGrant {
  id: number
  agent_id: number
  scope: string
  grant_reason: string
  granted_by: string
  active: boolean
  use_count: number
  last_used_at?: string | null
}

export interface AgentTrigger {
  id: number
  owner_agent_id: number
  event_source: string
  action_proposal_type: string
  matching_scope?: Record<string, unknown>
  permission_mode: string
  retry_policy?: Record<string, unknown>
  last_run_status?: string | null
  last_run_at?: string | null
  run_count: number
  notes: string
  active: boolean
}

export interface AgentDriftWarning {
  code: string
  title_uz: string
  detail_uz: string
  document_value?: string | null
  enforced_value: string
}

export interface AgentRecentAction {
  proposal_id: string
  action_type: string
  lifecycle_state: string
  risk_level: string
  reason_code: string
  summary_uz: string
  created_at: string
}

export interface AgentDetailResponse {
  schema_version: 'intelligence_agent_detail.v1'
  agent: AgentWorkbenchRow & {
    contact_scope: string
  }
  enforced_config: {
    permission_mode: string
    trust_mode: string
    is_active: boolean
    contact_scope: string
    brain_scopes: string[]
    tool_scopes: string[]
    channel_mode: string
  }
  drift_warnings: AgentDriftWarning[]
  sections: AgentDocumentSection[]
  skills: AgentWorkbenchSkill[]
  tool_grants: AgentToolGrant[]
  triggers: AgentTrigger[]
  recent_actions: AgentRecentAction[]
  rendered: { kind: 'agent'; title: string; markdown: string; sections_used: number }
}

export function useAgentWorkbenchAgents() {
  return useQuery({
    queryKey: queryKeys.agents.all,
    queryFn: () =>
      api.get<{ schema_version: 'intelligence_agents.v1'; items: AgentWorkbenchRow[] }>(
        '/api/intelligence/agents',
      ),
    staleTime: 30_000,
  })
}

export function useDraftCustomAgent() {
  return useMutation({
    mutationFn: (payload: DraftCustomAgentInput) =>
      api.post<DraftCustomAgentResponse>('/api/intelligence/agents/custom/draft', payload),
    onError: () => toast.error(uz.agents.create.draftError),
  })
}

export function useCreateCustomAgent() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: CreateCustomAgentInput) =>
      api.post<CreateCustomAgentResponse>('/api/intelligence/agents/custom', payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.agents.all })
      void queryClient.invalidateQueries({ queryKey: queryKeys.workspaceOS.state })
      toast.success(uz.agents.create.created)
    },
    onError: () => toast.error(uz.agents.create.createError),
  })
}

export function useAgentDetail(agentId: number | null) {
  return useQuery({
    queryKey: queryKeys.agents.detail(agentId ?? 0),
    queryFn: () => api.get<AgentDetailResponse>(`/api/intelligence/agents/${agentId}`),
    enabled: agentId !== null,
    staleTime: 20_000,
  })
}

export function useToolCatalog(connector?: string) {
  return useQuery({
    queryKey: queryKeys.agents.toolCatalog(connector),
    queryFn: () => {
      const params = new URLSearchParams()
      if (connector) params.set('connector', connector)
      const suffix = params.toString()
      return api.get<AgentToolCatalogResponse>(
        `/api/intelligence/tool-catalog${suffix ? `?${suffix}` : ''}`,
      )
    },
    staleTime: 60_000,
  })
}

export function useUpsertAgentSection(agentId: number | null) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: {
      section_key: string
      title: string
      body: string
      order_index: number
    }) =>
      api.post(`/api/intelligence/agents/${agentId}/sections`, {
        document_kind: 'agent',
        subject_type: 'agent',
        subject_id: agentId,
        ...payload,
        generated_by: 'owner',
      }),
    onSuccess: () => {
      if (agentId !== null) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.agents.detail(agentId) })
      }
      void queryClient.invalidateQueries({ queryKey: queryKeys.agents.all })
      toast.success('Agent hujjati yangilandi')
    },
    onError: () => toast.error('Bo‘lim saqlanmadi'),
  })
}

export function useUpdateAgentState(agentId: number | null) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: { is_active?: boolean; trust_mode?: 'autopilot' | 'disabled' }) =>
      api.put(`/api/agents/${agentId}`, payload),
    onSuccess: () => {
      if (agentId !== null) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.agents.detail(agentId) })
      }
      void queryClient.invalidateQueries({ queryKey: queryKeys.agents.all })
      void queryClient.invalidateQueries({ queryKey: queryKeys.workspaceOS.state })
      toast.success('Agent holati yangilandi')
    },
    onError: () => toast.error('Agent holatini o‘zgartirib bo‘lmadi'),
  })
}

export function useProposeAgentToolGrant(agentId: number | null) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: AgentToolGrantProposalInput) =>
      api.post<AgentToolGrantProposalResponse>(
        `/api/intelligence/agents/${agentId}/tool-grants/propose`,
        payload,
      ),
    onSuccess: () => {
      if (agentId !== null) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.agents.detail(agentId) })
      }
      void queryClient.invalidateQueries({ queryKey: queryKeys.agents.all })
      void queryClient.invalidateQueries({ queryKey: queryKeys.actionRuntime.inbox })
      void queryClient.invalidateQueries({ queryKey: queryKeys.workspaceOS.state })
      toast.success('Ruxsat taklifi Amallarga qo‘shildi')
    },
    onError: () => toast.error('Ruxsat taklifi yaratilmadi'),
  })
}

export function useProposeAgentTrigger(agentId: number | null) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: AgentTriggerProposalInput) =>
      api.post<AgentTriggerProposalResponse>(
        `/api/intelligence/agents/${agentId}/triggers/propose`,
        payload,
      ),
    onSuccess: () => {
      if (agentId !== null) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.agents.detail(agentId) })
      }
      void queryClient.invalidateQueries({ queryKey: queryKeys.agents.all })
      void queryClient.invalidateQueries({ queryKey: queryKeys.actionRuntime.inbox })
      void queryClient.invalidateQueries({ queryKey: queryKeys.workspaceOS.state })
      toast.success('Trigger taklifi Amallarga qo‘shildi')
    },
    onError: () => toast.error('Trigger taklifi yaratilmadi'),
  })
}
