export type Phase = 'choice' | 'telegram' | 'basics' | 'preferences' | 'sources' | 'documents' | 'launch' | 'credentials'
export type LaunchStep = 'agents' | 'permission' | 'profile'

export type RevenueBandKey =
  | 'under_10m'
  | 'from_10m_to_50m'
  | 'from_50m_to_100m'
  | 'from_100m_to_300m'
  | 'above_300m'

export type MessageVolumeKey = '1_10' | '10_50' | '50_200' | '200_plus'
export type ReplyTeamKey = 'owner_only' | 'small_team' | 'dedicated_manager'
export type ToneKey = 'short_warm' | 'formal' | 'friendly'
export type ReplyModeKey = 'draft' | 'safe_autopilot'
export type PermissionModeKey = 'ask_always' | 'auto_approve' | 'full_access'
export type DefaultAgentKey = 'seller' | 'support' | 'follow_up' | 'catalog_update' | 'bi'

export interface TelegramChannelOption {
  id: number | string
  name: string
  username?: string
  member_count?: number | null
  is_own?: boolean
  is_broadcast?: boolean
}

export type LearnedReviewActionInput = {
  action: 'approve' | 'reject' | 'edit' | 'merge'
  targetType: 'product' | 'fact'
  targetRef: string
  valuePatch?: Record<string, unknown>
  mergeIntoRef?: string
}
