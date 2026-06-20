// Canonical model types — single source of truth for the frontend.
// Every hook and component imports from here.

export interface Workspace {
  id: number
  phone_number: string
  name: string
  type?: string
  monthly_revenue_band?: string | null
  subscription_tier?: string
  telegram_connected: boolean
  onboarding_completed: boolean
  created_at: string
  updated_at: string
}

export interface TelegramConnectionStatus {
  state: 'connected' | 'disconnected' | 'connecting' | 'reconnecting' | 'degraded' | 'failed' | 'revoked' | 'stale'
  workspaceId: number
  userId: string | null
  phone: string | null
  reconnectAttempts: number
  lastError?: string | null
  queueSize?: number
  lastCatchUpAt?: string | null
  lastCatchUpCount?: number
  identityLinked?: boolean
  identityMismatch?: boolean
  identityVerified?: boolean
  needsReconnect?: boolean
}

export interface User {
  id: number
  phone_number: string
  name: string
  full_name?: string
  workspace_id: number
  platform_role?: 'business_owner' | 'founder'
  is_founder?: boolean
  type?: string
  monthly_revenue_band?: string | null
  subscription_tier?: string
  onboarding_completed?: boolean
  telegram_connected?: boolean
}

export interface AuthSessionProjection {
  schema_version: 'auth_session_projection.v1'
  authenticated: boolean
  workspace: Workspace
  platform_role: 'business_owner' | 'founder'
  is_founder: boolean
  onboarding_completed: boolean
  integrations: AuthIntegrationProjection[]
}

export interface AuthIntegrationProjection {
  provider: 'telegram_personal' | 'telegram_business_bot' | 'instagram'
  state: 'not_linked' | 'linked' | 'connected' | 'needs_reconnect' | 'degraded'
  identity_linked: boolean
  durable_connected: boolean
  needs_reconnect: boolean
  source: 'workspace_projection'
  external_id?: string | null
  live_state: 'not_checked'
}

export interface WorkspaceOSProjection {
  schema_version: 'workspace_os_projection.v1'
  workspace_id: number
  workspace_name: string
  onboarding_completed: boolean
  telegram_connected: boolean
  generated_at: string
  readiness: WorkspaceOSReadiness
  agents: WorkspaceOSAgentStatus[]
  documents: WorkspaceOSDocumentStatus
  sources: WorkspaceOSSourceStatus
  actions: WorkspaceOSActionStatus
  tasks: WorkspaceOSTaskStatus
}

export interface WorkspaceOSReadiness {
  status: 'not_provisioned' | 'degraded' | 'needs_review' | 'ready'
  percent: number
  issues: WorkspaceOSIssue[]
}

export interface WorkspaceOSIssue {
  code: string
  severity: 'info' | 'warning' | 'critical'
  target_kind: string
  target_ref: string
  title_uz: string
  detail_uz: string
  action_label_uz?: string | null
}

export interface WorkspaceOSAgentStatus {
  package_key: 'seller' | 'support' | 'catalog_update' | 'follow_up' | 'bi' | string
  expected: boolean
  present: boolean
  id?: number | null
  name: string
  agent_type: string
  is_active: boolean
  permission_mode: 'ask_always' | 'auto_approve' | 'full_access' | string
  trust_mode: string
  skill_count: number
  document_section_count: number
  capability_count: number
  tool_grant_count: number
  active_tool_grant_count: number
  trigger_count: number
  active_trigger_count: number
  missing_capability_scopes: string[]
  missing_tool_scopes: string[]
  missing_trigger_count: number
  skill_names: string[]
  document_preview: WorkspaceOSDocumentSectionPreview[]
  health: 'missing' | 'degraded' | 'ready'
}

export interface WorkspaceOSDocumentSectionPreview {
  section_key: string
  title: string
  body_preview: string
  generated_by: string
  source_evidence_count: number
}

export interface WorkspaceOSDocumentStatus {
  business_section_count: number
  agent_section_count: number
  skill_section_count: number
  owner_edited_section_count: number
  missing_business_sections: string[]
  sections_preview: WorkspaceOSDocumentSectionPreview[]
  business_md_ready: boolean
}

export interface WorkspaceOSSourceStatus {
  status: string
  summary: Record<string, number>
  sources: Array<{
    source_ref?: string
    kind: string
    purpose?: 'brain_data' | 'agent_data' | string
    label?: string
    status: string
    raw_state?: string
    source_unit_count?: number
    source_media_count?: number
    degraded_reasons?: string[]
    retryable?: boolean
    fact_id?: string
    entity_ref?: string
    source_refs?: string[]
  }>
}

export interface WorkspaceOSActionStatus {
  needs_approval: number
  scheduled: number
  running: number
  done: number
  failed: number
  rejected: number
}

export interface WorkspaceOSTaskStatus {
  proposed: number
  active: number
  done: number
  failed: number
}

export interface Conversation {
  id: number
  customer_id: number
  customer_name: string
  channel: string
  telegram_chat_id: number
  external_chat_id?: string
  pipeline_stage: string
  override_mode?: string
  summary?: string
  needs_attention: boolean
  needs_followup?: boolean
  last_message_at: string
  unread_count: number
  latest_conversation_seq?: number | null
  latest_conversation_revision?: number | null
  deal_value?: number
  products_mentioned?: string[]
  latest_action?: string
  crm_snapshot?: CrmSnapshot
  crm_stage?: CrmStageProjection | null
  next_best_action?: ConversationNextBestAction
  created_at: string
  last_message_text?: string
  contact_type?: string
  has_pending_reply?: boolean
  latest_reply_confidence?: number | null
  hydration?: ConversationHydrationProjection | null
}

export interface ConversationFilters {
  contact_type?: string
  has_pending_reply?: boolean
}

export interface LiveChat {
  telegram_chat_id: number
  telegram_user_id: number
  display_name: string
  phone: string | null
  unread_count: number
  last_message_text: string
  last_message_date: string | null
  last_message_is_outgoing: boolean
  read_outbox_max_id: number
  contact_type: string | null
  has_ai: boolean
  has_pending_reply: boolean
  conversation_id: number | null
  customer_id: number | null
}

export interface LiveChatsResponse {
  chats: LiveChat[]
  count: number
}

export interface PaginatedConversations {
  items: Conversation[]
  next_cursor: string | null
}

export interface CrmPipelineProjection {
  schema_version: 'crm_pipeline.v1'
  total: number
  stages: CrmPipelineColumn[]
}

export interface CrmPipelineColumn {
  stage: CrmStageProjection['stage']
  count: number
  cards: CrmPipelineCard[]
}

export interface CrmPipelineCard {
  conversation_id: number
  customer_id: number
  customer_name?: string | null
  channel: string
  stage: CrmStageProjection
  last_message_text?: string | null
  last_message_at?: string | null
  unread_count: number
  has_pending_reply: boolean
  latest_reply_confidence?: number | null
  contact_type?: string | null
  needs_attention: boolean
  deal_value?: number | null
}

export interface CrmSnapshot {
  pipeline_stage: string
  lead_score: number | null
  last_intent: string | null
  products_interested: string[]
  urgency: boolean | null
  needs_attention: boolean
  media_ready?: boolean | null
  last_updated: string
}

export interface CrmStageProjection {
  schema_version: 'crm_stage.v1'
  stage:
    | 'new'
    | 'qualified'
    | 'negotiation'
    | 'proposal'
    | 'payment'
    | 'delivery'
    | 'waiting'
    | 'won'
    | 'lost'
    | 'manual_review'
  source: 'crm_state' | 'defaulted'
  raw_stage?: string | null
  normalized_from?: string | null
  confidence?: number | null
  last_intent?: string | null
  products_interested: string[]
  urgency?: boolean | null
  needs_attention: boolean
  last_updated?: string | null
  field_provenance: Record<string, string>
}

export interface ConversationNextBestAction {
  action: string
  ready: boolean
  reason: string
}

export interface ConversationTailProjection {
  schema_version: 'conversation_tail.v1'
  status: 'ok' | 'stale' | 'gap_detected' | string
  source: 'local_message' | 'dialog_projection' | 'summary_fallback' | 'none' | string
  latest_message_text?: string | null
  latest_message_at?: string | null
  unread_count: number
  unread_source: 'local_rows' | 'dialog_projection' | string
  latest_conversation_seq: number
  latest_conversation_revision: number
  gap?: {
    reason: string
    before_external_message_id?: string | null
    after_external_message_id?: string | null
  } | null
}

export interface ConversationHydrationProjection {
  schema_version: 'conversation_hydration_runtime.v1'
  state: 'idle' | 'queued' | 'running' | 'ready' | 'empty' | 'failed' | 'deferred' | string
  reason: string
  needed: boolean
  can_retry: boolean
  attempt_count: number
  max_attempts: number
  requested_count: number
  persisted_count: number
  duplicate_count: number
  last_error?: string | null
  next_attempt_at?: string | null
  requested_at?: string | null
  started_at?: string | null
  completed_at?: string | null
  failed_at?: string | null
  updated_at?: string | null
}

export interface MessageTextEntity {
  type: string
  offset: number
  length: number
  document_id?: string
}

export interface DeliveryRuntimeProjection {
  schema_version: 'delivery_runtime.v1'
  state: 'requested' | 'sending' | 'unknown' | 'confirmed' | 'reconciled' | 'failed'
  customer_status: 'sending' | 'uncertain' | 'sent' | 'failed'
  next_action: 'wait' | 'reconcile' | 'retry' | 'none'
  is_terminal: boolean
  requires_reconciliation: boolean
  can_retry: boolean
  attempt_count: number
  max_attempts: number
  retry_budget_remaining: number
  external_message_id?: string | null
  last_error?: string | null
  requested_at?: string | null
  sending_at?: string | null
  confirmed_at?: string | null
  failed_at?: string | null
  unknown_at?: string | null
  reconciled_at?: string | null
  updated_at?: string | null
}

export interface Message {
  id: number
  conversation_id: number
  sender_type: string
  content: string
  channel: string
  telegram_message_id?: number
  is_read: boolean
  media_type?: string
  media_url?: string
  media_preview_url?: string
  media_full_url?: string
  media_metadata?: Record<string, unknown>
  media_runtime?: {
    asset_state?: string
    semantic_state?: string
    hydration_status?: string
    action_state?: string | null
    ai_relevant?: boolean
    attempt_count?: number
    max_attempts?: number
    next_attempt_at?: string | null
    retry_after_seconds?: number | null
  } | null
  text_entities?: MessageTextEntity[]
  reply_to_msg_id?: number
  forward_from_name?: string
  edited_at?: string
  reactions?: string[]
  created_at: string
  grouped_id?: number
  telegram_timestamp?: string
  external_message_id?: string
  client_message_uuid?: string
  delivery_state?: 'pending' | 'unknown' | 'confirmed' | 'failed'
  delivery_runtime?: DeliveryRuntimeProjection | null
  conversation_seq?: number
  conversation_revision?: number
}

export interface PaginatedMessages {
  items: Message[]
  has_older: boolean
  latest_conversation_seq?: number | null
  latest_conversation_revision?: number | null
  history_gap?: {
    reason: string
    before_external_message_id?: string | null
    after_external_message_id?: string | null
  } | null
  tail?: ConversationTailProjection | null
  hydration?: ConversationHydrationProjection | null
}

export interface Customer {
  id: number
  display_name: string
  name?: string
  phone_number: string
  phone?: string
  email?: string
  contact_type: string
  classification_confidence?: number
  classification_corrected?: boolean
  language?: string
  tags: string[]
  lifetime_value: number
  notes: string
  ai_brief?: string
  address?: string
  ai_muted?: boolean
  conversation_count: number
  last_conversation_at?: string
  stage?: string
  crm_stage?: CrmStageProjection | null
  latest_conversation_id?: number | null
  latest_conversation_tail?: ConversationTailProjection | null
  next_best_action?: ConversationNextBestAction | null
  needs_followup?: boolean
  has_pending_reply?: boolean
  latest_reply_confidence?: number | null
  created_at: string
  updated_at?: string
}

export interface CustomerDetail extends Customer {
  conversations: CustomerConversation[]
  ai_summary?: string
}

export interface CustomerConversation {
  id: number
  pipeline_stage: string
  crm_stage?: CrmStageProjection | null
  summary?: string
  last_message_at: string
  agent_name?: string
  avg_confidence?: number
}

export interface CatalogWorkspaceProjection {
  schema_version: 'catalog_workspace_projection.v1'
  workspace_id: number
  products: CatalogWorkspaceProduct[]
}

export interface CatalogWorkspaceProduct {
  schema_version: 'catalog_workspace_product.v1'
  product_ref: string
  product: Record<string, unknown>
  variants: CatalogFactValue[]
  offers: CatalogFactValue[]
  media: CatalogFactValue[]
  source_refs: string[]
  conflict_refs: string[]
  index_state: 'ready' | 'pending' | 'degraded' | 'unavailable'
  extraction_state: 'available' | 'degraded' | 'unavailable'
}

export type CatalogFactValue = Record<string, unknown>

export interface BusinessBrainFactReadModel {
  schema_version: 'business_brain_fact_read_model.v1'
  fact_id: string
  workspace_id: number
  fact_type: string
  entity_ref: string
  value: Record<string, unknown>
  confidence: number
  status: string
  risk_tier: string
  source_refs: string[]
  freshness: Record<string, unknown>
  supersedes_fact_id?: string | null
  valid_from: string
  valid_until?: string | null
}

export interface BusinessBrainFactsResponse {
  items: BusinessBrainFactReadModel[]
}

export type BrainObjectDomain =
  | 'catalog'
  | 'knowledge'
  | 'rules'
  | 'voice'
  | 'examples'
  | 'issues'
  | 'sources'

export type BrainObjectState = 'ready' | 'needs_review' | 'conflict' | 'degraded' | 'archived'

export type BrainObjectEvidenceKind =
  | 'telegram'
  | 'file'
  | 'website'
  | 'manual'
  | 'conversation'
  | 'integration'
  | 'source'

export type BrainObjectSourceLifecycle =
  | 'live'
  | 'snapshot'
  | 'expired'
  | 'archived'
  | 'conflicting'
  | 'failed'
  | 'retrying'

export interface BrainObjectEvidence {
  schema_version: 'brain_object_evidence.v1'
  label: string
  kind: BrainObjectEvidenceKind
  freshness_label: string
  detail?: string | null
  unit_label?: string | null
  source_ref?: string | null
}

export interface BrainObjectItem {
  schema_version: 'brain_object_item.v1'
  object_id: string
  domain: BrainObjectDomain
  title: string
  summary: string
  status: BrainObjectState
  status_label: string
  confidence: number
  risk_tier: string
  source_lifecycle: BrainObjectSourceLifecycle
  evidence: BrainObjectEvidence[]
  evidence_count: number
  updated_at: string
  can_edit: boolean
  can_archive: boolean
  needs_review: boolean
  fact_ids: string[]
  proposal_refs: string[]
}

export interface BrainObjectProjection {
  schema_version: 'brain_object_projection.v1'
  workspace_id: number
  objects: BrainObjectItem[]
  counts: Record<BrainObjectDomain, number>
  issues_count: number
  ready_count: number
  review_count: number
}

export type SourceIntakeLifecycle =
  | 'live'
  | 'snapshot'
  | 'learning'
  | 'needs_review'
  | 'retrying'
  | 'failed'
  | 'conflicting'
  | 'archived'

export type SourceIntakePurpose = 'brain_data' | 'agent_data'

export interface SourceIntakeItem {
  schema_version: 'source_intake_item.v1'
  source_ref: string
  title: string
  kind: string
  kind_label: string
  purpose: SourceIntakePurpose
  purpose_label: string
  lifecycle: SourceIntakeLifecycle
  status_label: string
  summary: string
  preview: string
  learned_object_count: number
  learned_object_labels: string[]
  source_unit_count: number
  media_count: number
  issue_label?: string | null
  retryable: boolean
  can_retry: boolean
  can_archive: boolean
  can_pause: boolean
  can_resume: boolean
  fact_id?: string | null
  updated_at: string
}

export interface SourceIntakeProjection {
  schema_version: 'source_intake_projection.v1'
  workspace_id: number
  sources: SourceIntakeItem[]
  counts: Record<SourceIntakeLifecycle, number>
  kind_counts: Record<string, number>
  ready_count: number
  review_count: number
  failed_count: number
  live_count: number
}

export interface BusinessBrainFactReviewActionInput {
  action: 'approve' | 'reject' | 'edit' | 'merge'
  target_ref: string
  value_patch?: Record<string, unknown>
  merge_into_ref?: string
}

export interface BusinessBrainFactReviewActionResult {
  schema_version: 'onboarding_learned_review_action_result.v1'
  action: 'approve' | 'reject' | 'edit' | 'merge'
  target_type: 'fact' | 'product'
  target_ref: string
  applied_count: number
  rejected_count: number
  edited_count: number
  merged_count: number
  fact_ids: string[]
}

export interface BusinessBrainManualFactUpdateInput {
  fact_id: string
  update_id: string
  fact_type: string
  entity_ref: string
  value: Record<string, unknown>
  confidence: number
  risk_tier: string
  source_refs: string[]
  idempotency_key: string
  correlation_id?: string
  supersedes_fact_id?: string
}

export interface BusinessBrainManualFactUpdateResult {
  schema_version: 'business_brain_write_result.v1'
  fact: BusinessBrainFactReadModel
  update: Record<string, unknown>
  fact_created: boolean
  update_created: boolean
  projection?: Record<string, unknown> | null
}

export interface BusinessBrainSourceCreateInput {
  kind: 'website' | 'telegram_channel' | 'file' | 'text' | 'voice_note' | string
  purpose?: 'brain_data' | 'agent_data'
  label?: string
  text?: string
  url?: string
  handle?: string
  file_name?: string
  content_type?: string
  content_base64?: string
  byte_size?: number
  transcript?: string
  date_from?: string
  date_to?: string
}

export interface BusinessBrainSourceCreateResponse {
  source_ref: string
  fact: BusinessBrainFactReadModel
}

export interface BusinessBrainAudioTranscriptResponse {
  schema_version: 'business_brain_audio_transcript.v1'
  status: 'ready' | 'degraded'
  transcript: string
  confidence: number
  model_used?: string | null
  trace_id?: string | null
  error_label?: string | null
}

export interface BusinessBrainSourceLearningResult {
  schema_version: 'onboarding_source_runtime_result.v1'
  processed_count: number
  review_ready_count: number
  learned_count: number
  retrying_count: number
  failed_count: number
  skipped_count: number
  items: Array<{
    schema_version: 'onboarding_source_runtime_item.v1'
    source_ref: string
    source_kind: string
    source_fact_id: string
    status: 'processed' | 'review_ready' | 'learned' | 'retrying' | 'failed' | 'skipped'
    attempt_count: number
    degraded_reasons: string[]
  }>
}

export interface BusinessBrainSourceControlInput {
  source_ref: string
  action: 'archive' | 'pause' | 'resume'
  idempotency_key?: string
}

export interface BusinessBrainSourceControlResponse {
  schema_version: 'source_intake_control_result.v1'
  source_ref: string
  action: 'archive' | 'pause' | 'resume'
  fact: BusinessBrainFactReadModel
  event_created: boolean
  update_created: boolean
}

export interface KnowledgeItem {
  id: number
  workspace_id: number
  title: string
  content: string
  category: string
  source: string
  ai_confidence: number | null
  confirmed: boolean
  frequency: number | null
  is_active: boolean
  created_at: string
  updated_at: string
}

export type AgentTrustMode = 'draft' | 'autonomous' | 'autopilot'

export interface Agent {
  id: number
  workspace_id: number
  name: string
  is_default: boolean
  is_active: boolean
  persona: string | { role?: string; tone?: string; style?: string }
  instructions?: string
  example_responses?: string[]
  knowledge_config?: Record<string, unknown>
  channel_config?: Record<string, unknown>
  tools_config?: { enabled_tools?: string[] }
  tools_enabled?: string[]
  trust_mode: AgentTrustMode
  auto_send_threshold?: number
  escalation_topics?: string[]
  agent_type?: string
  created_at: string
  updated_at: string
}

export interface SellerAgentRuntimeStatus {
  workspace_id: number
  has_runtime_override: boolean
  max_inflight_replies: number
  max_ready_claims_per_tick: number
  consecutive_failures: number
  cooldown_until?: string | null
  disabled_reason?: string | null
  is_disabled: boolean
  seller_agent_enabled: boolean
  cooldown_active: boolean
  generation_blocked: boolean
  last_candidate_selected_at?: string | null
  last_candidate_completed_at?: string | null
  created_at?: string | null
  updated_at?: string | null
  default_max_inflight_replies: number
  default_max_ready_claims_per_tick: number
  active_candidates: number
  open_candidates: number
  ready_candidates: number
  leased_candidates: number
  generating_candidates: number
  failed_candidates: number
  suppressed_candidates: number
  superseded_candidates: number
  completed_candidates: number
}

export interface SellerAgentRuntimeUpdate {
  max_inflight_replies?: number
  max_ready_claims_per_tick?: number
  is_disabled?: boolean
  disabled_reason?: string | null
  clear_cooldown?: boolean
}

export interface LlmModelPolicy {
  id: string
  label: string
  lane: string
  provider: string
  description: string
}

export interface LlmTaskPolicy {
  key: string
  label: string
  lane: string
  description: string
  default_model: string
  effective_model: string
  override_model?: string | null
  allow_override: boolean
}

export interface LlmPolicyStatus {
  workspace_id: number
  models: LlmModelPolicy[]
  tasks: LlmTaskPolicy[]
  overrides: Record<string, string>
}

export interface LlmPolicyUpdate {
  overrides: Record<string, string | null>
}

export type ChipType =
  | 'approve'
  | 'approve_and_stage'
  | 'edit_instruction'
  | 'tool_action'
  | 'quick_reply'
  | 'escalate'
  | 'tone_shift'

export interface TypedChip {
  type: ChipType
  label: string
  payload: Record<string, unknown>
}

export type SellerAgentReplyStatus = 'draft' | 'approved' | 'rejected' | 'sent' | 'expired' | 'sending' | 'suppressed' | 'delivery_failed' | 'delivery_unknown' | 'partially_sent'

export type SellerAgentLearningRuntimeState =
  | 'not_applicable'
  | 'queued'
  | 'learned'
  | 'skipped'
  | 'failed'

export type SellerAgentLearningRuntimeAction =
  | 'none'
  | 'wait'
  | 'retry'

export interface SellerAgentLearningRuntimeProjection {
  schema_version: 'seller_agent_learning_runtime.v1'
  state: SellerAgentLearningRuntimeState
  source_action_id?: number | null
  signal_id?: number | null
  next_action: SellerAgentLearningRuntimeAction
  last_error?: string | null
}

export interface RecentMessage {
  sender_type: 'customer' | 'seller' | 'ai'
  content: string
  created_at: string
}

export interface SellerAgentReply {
  id: number
  conversation_id: number
  trigger_message_id?: number
  trigger_type?: 'inbound_reply' | 'follow_up' | string
  channel?: string
  trigger_id?: string
  message_id?: number
  agent_id?: number
  confidence_score: number
  status: SellerAgentReplyStatus
  draft_content: string
  final_content?: string
  override_reason?: string
  override_note?: string
  reviewed_at?: string
  model_used?: string
  response_time_ms?: number
  chips: TypedChip[] | null
  split_messages: string[] | null
  intent?: string
  is_auto_sent: boolean
  suppressed_at?: string
  customer_name?: string
  telegram_chat_id?: number
  trigger_message_text?: string
  recent_messages?: RecentMessage[]
  learning_runtime?: SellerAgentLearningRuntimeProjection | null
  delivery_runtime?: DeliveryRuntimeProjection | null
  created_at: string
}

export interface SellerAgentReplyTraceEvent {
  sequence: number
  at: string
  stage: string
  event: string
  [key: string]: unknown
}

export interface SellerAgentReplyTraceDebug {
  intent?: string
  plan?: Record<string, unknown> | null
  evidence?: Record<string, unknown> | null
  evidence_trace?: Record<string, unknown> | null
  generation?: Record<string, unknown> | null
  quality?: Record<string, unknown> | null
  quality_summary?: Record<string, unknown> | null
  finalizer?: Record<string, unknown> | null
  confidence?: Record<string, unknown> | null
  choice?: Record<string, unknown> | null
  prompt_debug?: Record<string, unknown> | null
  grounding?: Record<string, unknown> | null
  provenance?: {
    trigger_type?: string
    channel?: string
    trigger_id?: string
  } | null
}

export interface SellerAgentReplyTrace {
  reply_id: number
  debug: SellerAgentReplyTraceDebug | null
  events: SellerAgentReplyTraceEvent[]
}

export type WebSocketSellerAgentReplyEventType =
  | 'ai_thinking'
  | 'ai_thinking_failed'
  | 'ai_reply_created'
  | 'ai_reply_sent'
  | 'ai_reply_updated'
  | 'delivery_failed'
  | 'delivery_unknown'

export interface WebSocketSellerAgentReplyEvent {
  type: WebSocketSellerAgentReplyEventType
  conversation_id?: number
  reply_id?: number
  accuracy?: number
  total_reviewed?: number
}

// Onboarding / Ingestion
export interface VoiceDiscovery {
  icon: string
  label: string
  subtitle?: string
}

export interface IngestionProgress {
  workspace_id: number
  phase: string
  percent: number
  contacts_found: number
  customers_identified: number
  visible_dialog_limit?: number
  history_learning_conversation_limit?: number
  history_learning_message_limit?: number
  history_prefetched_conversations?: number
  history_replayed_conversations?: number
  history_replayed_messages?: number
  products_extracted: number
  knowledge_items: number
  voice_profile_ready: boolean
  voice_profile_degraded?: boolean
  voice_profile_error?: string | null
  contact_classification_degraded?: boolean
  ai_learning_degraded?: boolean
  ai_learning_error?: string | null
  voice_discoveries: VoiceDiscovery[]
  completed: boolean
  errors: string[]
}

export interface OnboardingRuntimeStage {
  id: string
  label: string
  status: string
  percent: number
  detail?: string | null
  retryable?: boolean
  error?: string | null
}

export interface OnboardingSourceLearningSource {
  source_ref: string
  kind: string
  label: string
  purpose?: 'brain_data' | 'agent_data' | string
  status: string
  stage?: string
  raw_state: string
  attempt_count?: number
  max_attempts?: number
  started_at?: string
  updated_at?: string
  completed_at?: string
  input_cache_reused?: boolean
  source_unit_count: number
  source_media_count: number
  catalog_candidate_count?: number
  memory_candidate_count?: number
  rejected_candidate_count?: number
  degraded_reasons: string[]
  retryable: boolean
  fact_id: string
  entity_ref: string
  source_refs: string[]
}

export interface OnboardingSourceLearningEvent {
  event_ref: string
  source_ref: string
  kind: string
  status: string
  stage?: string
  created_at?: string
  source_unit_count?: number
  source_media_count?: number
  catalog_candidate_count?: number
  memory_candidate_count?: number
  rejected_candidate_count?: number
  attempt_count?: number
  max_attempts?: number
  input_cache_reused?: boolean
  title_uz: string
  detail_uz: string
}

export interface OnboardingSourceLearningProjection {
  schema_version: 'onboarding_source_learning.v1'
  status: string
  percent: number
  summary: {
    total: number
    queued?: number
    learning: number
    learned: number
    needs_review: number
    missing: number
    conflict: number
    retrying: number
    failed: number
  }
  sources: OnboardingSourceLearningSource[]
  events?: OnboardingSourceLearningEvent[]
}

export interface OnboardingLearnedReviewProduct {
  product_ref: string
  fact_id: string
  title: string
  category?: string | null
  description?: string | null
  confidence: number
  risk_tier: string
  source_refs: string[]
  source_evidence?: OnboardingSourceEvidence[]
  offers: Array<Record<string, unknown>>
  media: Array<Record<string, unknown>>
}

export interface OnboardingSourceEvidence {
  ref: string
  kind: string
  label: string
  detail?: string | null
  unit_label?: string | null
}

export interface OnboardingLearnedReviewItem {
  fact_id: string
  fact_type: string
  entity_ref: string
  topic?: string | null
  question?: string | null
  answer?: string | null
  summary?: string | null
  requirement?: string | null
  rule?: string | null
  details: Record<string, unknown>
  observations: string[]
  confidence: number
  risk_tier: string
  source_refs: string[]
  source_evidence?: OnboardingSourceEvidence[]
}

export interface OnboardingLearnedReviewProjection {
  schema_version: 'onboarding_learned_review.v1'
  status: string
  summary: {
    products: number
    knowledge: number
    rules: number
    voice: number
    integrations: number
    media: number
    offers: number
    total_review_items: number
  }
  products: OnboardingLearnedReviewProduct[]
  knowledge: OnboardingLearnedReviewItem[]
  rules: OnboardingLearnedReviewItem[]
  voice: OnboardingLearnedReviewItem[]
  integrations: OnboardingLearnedReviewItem[]
}

export interface OnboardingRuntimeProjection {
  schema_version: 'onboarding_runtime.v1'
  workspace_id: number
  state: string
  phase: string
  percent: number
  current_stage_id: string
  stages: OnboardingRuntimeStage[]
  is_running: boolean
  is_terminal: boolean
  is_retryable: boolean
  is_dlq: boolean
  can_requeue: boolean
  lease_expired: boolean
  attempt_count: number
  max_attempts: number
  lease_owner?: string | null
  leased_until?: string | null
  next_attempt_at?: string | null
  started_at?: string | null
  completed_at?: string | null
  failed_at?: string | null
  last_error?: string | null
  progress: IngestionProgress
  source_learning: OnboardingSourceLearningProjection
  learned_review: OnboardingLearnedReviewProjection
}

export interface EventSpineSignals {
  status: string
  error: string | null
  publish_failures: number
  global_divergences: Record<string, number>
  workspace_divergences: Record<string, number>
  persist_shadow: Record<string, number>
  persist_shadow_ready: boolean
  persist_shadow_blockers: string[]
}

export interface SellerAgentReplyFreshnessSignals {
  replies_total: number
  expired_count: number
  suppressed_count: number
  freshness_loss_count: number
  freshness_loss_rate: number
  suppressed_reasons: Record<string, number>
}

export interface MediaRuntimeSignals {
  ai_relevant_media_total: number
  hydrated_count: number
  pending_count: number
  deferred_count: number
  unavailable_count: number
  due_count: number
  leased_count: number
  stale_lease_count: number
  stuck_count: number
}

export interface DeliveryRuntimeSignals {
  active_count: number
  unknown_count: number
  failed_count: number
  retryable_count: number
  stale_unknown_count: number
}

export interface ConversationHydrationSignals {
  active_count: number
  queued_count: number
  running_count: number
  deferred_count: number
  failed_count: number
  stale_lease_count: number
  retryable_count: number
}

export interface SellerAgentQueueSignals {
  active_candidates: number
  open_candidates: number
  ready_candidates: number
  leased_candidates: number
  generating_candidates: number
  failed_candidates: number
  suppressed_candidates: number
  superseded_candidates: number
}

export interface AutopilotRuntimeSignals {
  decisions_total: number
  allowed_count: number
  blocked_count: number
  scheduled_count: number
  sent_count: number
  delivery_failed_count: number
  delivery_unknown_count: number
  blocked_reasons: Record<string, number>
}

export interface ActionRuntimeSignals {
  degraded_total: number
  degraded_by_action: Record<string, number>
}

export interface WorkspaceQuotaSignals {
  seller_agent_max_inflight: number
  seller_agent_max_ready_claims_per_tick: number
  media_max_claims_per_workspace: number
  scheduled_send_max_claims_per_workspace: number
  universal_extraction_daily_count: number
  universal_extraction_daily_cap: number
  universal_extraction_exceeded: boolean
}

export interface WorkspaceUsageAccountingSignals {
  daily_input_tokens: number
  daily_output_tokens: number
  daily_total_tokens: number
  daily_operation_count: number
  daily_estimated_cost_micros: number
  by_operation: Record<string, number>
  by_provider: Record<string, number>
  by_operation_estimated_cost_micros: Record<string, number>
  by_provider_estimated_cost_micros: Record<string, number>
  cost_policy: Record<string, Record<string, number>>
  daily_history: Array<{
    date: string
    input_tokens: number
    output_tokens: number
    total_tokens: number
    operation_count: number
    estimated_cost_micros: number
  }>
}

export interface RuntimeSLOSignals {
  status: string
  message_visible_under_1s_status: string
  message_visible_p95_ms: number | null
  message_visible_sample_count: number
  seller_agent_or_degraded_under_20s_status: string
  oldest_seller_agent_wait_seconds: number | null
  media_hydration_lag_seconds: number | null
  workspace_deadletter_length: number
  replay_drift_status: string
  replay_drift_count: number
}

export interface RuntimeDependencySignals {
  status: string
  database: string
  redis: string
  errors: Record<string, string>
}

export interface WorkerLeaseSnapshot {
  role: string
  lifecycle_model: string
  proof_status: string
  active: boolean
  owner: string | null
  ttl_seconds: number | null
  contended_count: number
  lost_count: number
  supervisor_status: string | null
  heartbeat_stale: boolean | null
  restart_count: number | null
  last_error: string | null
}

export interface WorkerLifecycleSignals {
  status: string
  error: string | null
  roles: Record<string, WorkerLeaseSnapshot>
}

export interface RuntimeRepairAction {
  key: string
  severity: string
  scope: string
  reason: string
  replay_entrypoint: string | null
  repair_entrypoint: string | null
}

export interface RuntimeRepairSignals {
  status: string
  degraded_reasons: string[]
  actions: RuntimeRepairAction[]
}

export interface RuntimeOperatorFinding {
  key: string
  severity: string
  owner: string
  state: string
  scope: string
  reason: string
  safe_action: string | null
  replay_entrypoint: string | null
  repair_entrypoint: string | null
}

export interface RuntimeOperatorReport {
  status: string
  workspace_id: number
  summary: string
  finding_count: number
  critical_count: number
  warning_count: number
  findings: RuntimeOperatorFinding[]
}

export interface RuntimeSignalsResponse {
  schema_version: 'runtime_signals.v1'
  workspace_id: number
  period_days: number
  event_spine: EventSpineSignals
  seller_agent_reply_freshness: SellerAgentReplyFreshnessSignals
  media: MediaRuntimeSignals
  delivery: DeliveryRuntimeSignals
  conversation_hydration: ConversationHydrationSignals
  seller_agent_queue: SellerAgentQueueSignals
  autopilot: AutopilotRuntimeSignals
  action_runtime: ActionRuntimeSignals
  quotas: WorkspaceQuotaSignals
  usage_accounting: WorkspaceUsageAccountingSignals
  slo: RuntimeSLOSignals
  dependencies: RuntimeDependencySignals
  worker_lifecycle: WorkerLifecycleSignals
  repair: RuntimeRepairSignals
  operator_report: RuntimeOperatorReport
}

// Insights
export interface InsightsMetrics {
  stats: {
    total_messages: number
    ai_handled: number
    ai_rate: number
    avg_response_time_ms: number
    pipeline_conversion_rate: number
  }
  agents: { agent_id: number; agent_name: string; replies_count: number; avg_confidence: number; auto_send_count: number }[]
  top_customers: { customer_id: number; customer_name: string; lifetime_value: number; conversation_count: number }[]
  pipeline: { stage: string; count: number; percentage: number }[]
}

export interface IntelligenceReport {
  success_patterns: { title: string; description: string }[]
  loss_reasons: { title: string; description: string }[]
  segments: { name: string; description: string; count: number }[]
  recommendations: { title: string; description: string; action_url?: string }[]
}

export interface BIInsight {
  schema_version: 'bi_insight.v1'
  workspace_id: number
  insight_id: string
  insight_type: string
  answer: string
  metrics: Record<string, unknown>
  records: Record<string, unknown>[]
  source_refs: string[]
  confidence: number
  freshness: 'projection_current' | 'projection_partial' | 'degraded'
  suggested_action_proposal_ids: string[]
  degraded_reasons: string[]
}

export interface BIAnalyticsDashboard {
  schema_version: 'bi_analytics_dashboard.v1'
  workspace_id: number
  summary: Record<string, number | string | boolean | null>
  breakdowns: Record<string, Record<string, unknown>[]>
  insights: BIInsight[]
  source_refs: string[]
  freshness: 'projection_current' | 'projection_partial' | 'degraded'
  degraded_reasons: string[]
}

export interface BIInvestigationFinding {
  schema_version: 'bi_investigation_finding.v1'
  finding_ref: string
  finding_type: string
  severity: 'low' | 'medium' | 'high'
  title: string
  summary: string
  source_refs: string[]
  confidence: number
  suggested_action?: string | null
}

export interface BIInvestigationFixCandidate {
  schema_version: 'bi_investigation_fix_candidate.v1'
  target_ref: string
  proposal_type: string
  proposed_value: Record<string, unknown>
  evidence_refs: string[]
  risk_tier: 'low' | 'medium' | 'high' | 'critical'
  approval_state: 'proposed'
}

export interface BIInvestigationResult {
  schema_version: 'bi_investigation_result.v1'
  workspace_id: number
  investigation_ref: string
  status: 'ready' | 'degraded'
  findings: BIInvestigationFinding[]
  fix_candidates: BIInvestigationFixCandidate[]
  source_refs: string[]
  confidence: number
  freshness: 'projection_current' | 'projection_partial' | 'degraded'
  degraded_reasons: string[]
  llm_trace_ids: string[]
}

export interface BICommandRequest {
  command_kind: 'create_agent' | 'create_owner_task' | 'create_reply_action'
  command_text: string
  agent_name?: string
  permission_mode?: 'ask_always' | 'auto_approve' | 'full_access'
  brain_scopes?: string[]
  tool_scopes?: string[]
  trigger_sources?: string[]
  task_title?: string
  task_detail?: string
  task_kind?: OwnerTaskKind
  due_at?: string | null
  customer_label?: string
  conversation_id?: number
  customer_id?: number
  reply_text?: string
  source_proposal_id?: string
  correlation_id?: string
}

export interface BICommandResult {
  schema_version: 'bi_command_result.v1'
  workspace_id: number
  command_kind: 'create_agent' | 'create_owner_task' | 'create_reply_action'
  status: 'proposal_created' | 'proposal_reused'
  message_uz: string
  proposal: CommercialActionProposal
  action_route: '/actions'
  source_refs: string[]
}

export interface PromoterPolicy {
  schema_version: 'promoter_policy.v1'
  workspace_id: number
  enabled: boolean
  approved: boolean
  allowed_stages: string[]
  max_contacts_per_7d: number
  quiet_hours: Record<string, unknown>
  source_refs: string[]
  correlation_id: string
}

export interface CommercialActionProposal {
  schema_version: 'commercial_action_proposal.v2'
  proposal_id: string
  workspace_id: number
  conversation_id: number
  customer_id: number
  action_type: string
  lifecycle_state: string
  execution_mode: string
  risk_level: string
  requires_approval: boolean
  executor_runtime?: string | null
  priority: string
  confidence: number
  reason_code: string
  source_refs: string[]
  payload: Record<string, unknown>
  idempotency_key: string
  correlation_id?: string | null
  trace_id?: string | null
}

export type ActionRuntimeState =
  | 'proposed'
  | 'waiting_approval'
  | 'approved'
  | 'executing'
  | 'executed'
  | 'rejected'
  | 'blocked'
  | 'failed'
  | 'expired'
  | 'cancelled'

export type ActionRuntimeExecutionStatus = 'executed' | 'blocked' | 'failed' | 'unsupported'

export interface ActionRuntimePolicy {
  schema_version: 'action_runtime_policy.v1'
  workspace_id: number
  enabled: boolean
  confidence_threshold: number
  low_risk_allowlist: string[]
  quiet_hours: Record<string, unknown>
  escalation_destination: 'in_app' | 'telegram_seller_bot'
  source_refs: string[]
  correlation_id: string
}

export interface ActionRuntimeExecution {
  schema_version: 'action_runtime_execution.v1'
  execution_id: string
  workspace_id: number
  conversation_id: number
  customer_id: number
  proposal_id: string
  action_type: string
  status: ActionRuntimeExecutionStatus
  reason_code: string
  idempotency_key: string
  attempt: number
  delivery_state?: string | null
  external_message_id?: string | null
  payload: Record<string, unknown>
  error?: string | null
}

export interface ActionRuntimeDecision {
  schema_version: 'action_runtime_decision.v1'
  workspace_id: number
  proposal_id: string
  state: ActionRuntimeState
  reason_code: string
  allowed_to_execute: boolean
  notification_refs: string[]
  execution?: ActionRuntimeExecution | null
}

export interface ActionRuntimeInbox {
  schema_version: 'action_runtime_inbox.v1'
  workspace_id: number
  items: CommercialActionProposal[]
}

export type AgentRunState =
  | 'queued'
  | 'running'
  | 'waiting_approval'
  | 'waiting_tool'
  | 'completed'
  | 'failed'
  | 'cancelled'

export type AgentRunVisibility = 'internal' | 'owner' | 'customer_action'
export type AgentToolState = 'planned' | 'called' | 'succeeded' | 'failed' | 'blocked'

export interface AgentRun {
  schema_version: 'agent_run.v1'
  run_id: string
  workspace_id: number
  agent_id: number
  agent_kind: string
  trigger_ref: string
  conversation_id: number
  customer_id: number
  state: AgentRunState
  permission_mode: string
  cache_key?: string | null
  correlation_id: string
  idempotency_key: string
  source_refs: string[]
  started_at: string
  completed_at?: string | null
}

export interface AgentRunEvent {
  schema_version: 'agent_run_event.v1'
  event_id: string
  run_id: string
  workspace_id: number
  sequence: number
  event_type: string
  visibility: AgentRunVisibility
  owner_label: string
  owner_detail: string
  tool_name?: string | null
  tool_state?: AgentToolState | null
  action_proposal_id?: string | null
  source_refs: string[]
  payload: Record<string, unknown>
  correlation_id: string
  idempotency_key: string
  created_at: string
}

export interface AgentRunTimeline {
  schema_version: 'agent_run_timeline.v1'
  workspace_id: number
  run_id: string
  run?: AgentRun | null
  events: AgentRunEvent[]
}

export interface AgentRunFeed {
  schema_version: 'agent_run_feed.v1'
  workspace_id: number
  timelines: AgentRunTimeline[]
}

export type OwnerTaskKind =
  | 'business'
  | 'meeting'
  | 'delivery'
  | 'stock'
  | 'call'
  | 'payment'
  | 'follow_up'

export type OwnerTaskState = 'proposed' | 'accepted' | 'blocked' | 'completed' | 'dismissed'
export type OwnerTaskDueBucket = 'today' | 'overdue' | 'upcoming' | 'completed' | 'proposed'

export interface OwnerTaskItem {
  schema_version: 'owner_task_item.v1'
  task_id: string
  workspace_id: number
  proposal_id: string
  action_type: string
  kind: OwnerTaskKind
  state: OwnerTaskState
  due_bucket: OwnerTaskDueBucket
  title: string
  detail: string
  customer_label: string
  conversation_id: number
  customer_id: number
  due_at?: string | null
  status_label: string
  source_label: string
  evidence_labels: string[]
  priority: string
  risk_level: string
  confidence: number
  can_accept: boolean
  can_complete: boolean
  can_snooze: boolean
  can_message: boolean
  proposal: CommercialActionProposal
}

export interface OwnerTaskProjection {
  schema_version: 'owner_task_projection.v1'
  workspace_id: number
  items: OwnerTaskItem[]
  proposed: OwnerTaskItem[]
  counts: Record<string, number>
}

export interface PromoterCampaignPlan {
  schema_version: 'promoter_campaign_plan.v1'
  workspace_id: number
  campaign_ref: string
  status: 'planned' | 'blocked'
  blocked_reasons: string[]
  decisions: Record<string, unknown>[]
  proposals: CommercialActionProposal[]
  source_refs: string[]
  confidence: number
}

// Onboarding stream events
export interface StreamEvent {
  _id: number | string
  kind: string
  [key: string]: unknown
}

// Contact correction
export interface ContactForReview {
  id: number
  display_name: string
  contact_type: string
  classification_confidence: number | null
  classification_corrected: boolean
  last_conversation_at: string | null
  phone_number: string | null
}

// Classification results (from POST /api/customers/classify-batch)
export interface ClassificationResultItem {
  customer_id: number
  name: string | null
  suggested_type: string
  current_type: string
}

export interface ClassificationBatchResponse {
  items: ClassificationResultItem[]
  total: number
}

// Review items
export interface ReviewableProduct {
  id: number
  name: string
  price: number
  description: string
  category: string
  status: string
  confirmed: boolean
  source: string
  ai_confidence: number | null
  images: string[]
}

export interface ReviewableKnowledge {
  id: number
  title: string
  content: string
  source: string
  category: string
  confirmed: boolean
  ai_confidence: number | null
  frequency: number | null
}

// BI Agent
export interface BIMessage {
  role: 'user' | 'assistant'
  content: string
  created_at?: string
}

// API response wrappers
export interface PaginatedResponse<T> {
  items: T[]
  total: number
}

export interface CustomerListResponse {
  customers: Customer[]
  total: number
  avg_ltv: number
  new_this_week: number
  crm_summary?: CustomerCrmListProjection | null
}

export interface CustomerCrmListProjection {
  schema_version: 'customer_crm_list.v1'
  scope: 'page'
  total: number
  stages: CustomerCrmStageSummary[]
  needs_attention_count: number
  pending_reply_count: number
}

export interface CustomerCrmStageSummary {
  stage: CrmStageProjection['stage'] | string
  count: number
}

export interface TelegramAuthAttemptDiagnostic {
  id: number
  workspace_id: number | null
  phone_masked: string
  temp_session_id: string | null
  state: string
  recovery_state: string | null
  delivery_type: string | null
  preferred_delivery_type: string | null
  delivery_degraded: boolean
  delivery_degraded_reason: string | null
  auth_transport: string | null
  auth_client_profile: string | null
  attempted_dc_ids: number[]
  connected_initial_dc_id: number | null
  next_delivery_type: string | null
  timeout_seconds: number | null
  attempt_count: number
  recovery_attempt_count: number
  max_recovery_attempts: number
  next_recovery_at: string | null
  last_recovery_at: string | null
  retry_after_seconds: number | null
  last_step: string | null
  last_error: string | null
  has_temp_session_data: boolean
  created_at: string
  updated_at: string
}

export interface TelegramAuthDiagnosticsResponse {
  count: number
  attempts: TelegramAuthAttemptDiagnostic[]
}

export type OnboardingDocumentSectionStatus =
  | 'pending'
  | 'generating'
  | 'proposed'
  | 'approved'

export interface OnboardingDocumentSection {
  key: string
  title: string
  status: OnboardingDocumentSectionStatus
  body: string
  evidence_count: number
}

export interface OnboardingDocumentBlock {
  total: number
  approved: number
  proposed: number
  generating: string | null
  sections: OnboardingDocumentSection[]
}

export type OnboardingSkillStatus =
  | 'pending'
  | 'learning'
  | 'proposed'
  | 'degraded'
  | string

export interface OnboardingSkillBlock {
  status: OnboardingSkillStatus
  candidates: number
}

export interface OnboardingDocumentsProjection {
  schema_version: 'onboarding_documents.v1'
  workspace_id: number
  running: boolean
  current_doc: 'business' | 'agent' | 'skill' | null
  error: string | null
  percent: number
  documents: {
    business: OnboardingDocumentBlock
    agent: OnboardingDocumentBlock
    skill: OnboardingSkillBlock
  }
}

// A reviewable SKILL.md candidate from the learner (or an upload). Distinct
// trigger/action/example fields — unlike a document section's single body — so
// the review card renders them as a labeled definition list. Lives in
// `proposed` until the owner approves (promotes into agent_skills) or rejects.
export interface SkillCandidate {
  id: number
  slug: string
  name: string
  trigger: string
  action: string
  example_phrase: string
  dimension: string
  confidence: number
  evidence_conv_ids: number[]
  status: string
  source: string
}
