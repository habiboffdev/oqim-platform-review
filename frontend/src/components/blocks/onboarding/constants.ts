import { uz } from '@/lib/uz'
import type {
  DefaultAgentKey,
  MessageVolumeKey,
  PermissionModeKey,
  ReplyModeKey,
  ReplyTeamKey,
  RevenueBandKey,
  ToneKey,
} from './types'

export const ONBOARDING_STAGES = 3
export const DEFAULT_CATEGORY = 'retail'

export const CATEGORY_OPTIONS = [
  { value: 'retail', label: uz.onboarding.categories.retail },
  { value: 'courses', label: uz.onboarding.categories.courses },
  { value: 'medicine', label: uz.onboarding.categories.medicine },
  { value: 'real_estate', label: uz.onboarding.categories.real_estate },
  { value: 'services', label: uz.onboarding.categories.services },
  { value: 'electronics', label: uz.onboarding.categories.electronics },
  { value: 'fashion', label: uz.onboarding.categories.fashion },
  { value: 'beauty', label: uz.onboarding.categories.beauty },
  { value: 'food', label: uz.onboarding.categories.food },
  { value: 'other', label: uz.onboarding.categories.other },
] as const

export const REVENUE_OPTIONS: { value: RevenueBandKey; label: string }[] = [
  { value: 'under_10m', label: uz.onboarding.revenueBands.under_10m },
  { value: 'from_10m_to_50m', label: uz.onboarding.revenueBands.from_10m_to_50m },
  { value: 'from_50m_to_100m', label: uz.onboarding.revenueBands.from_50m_to_100m },
  { value: 'from_100m_to_300m', label: uz.onboarding.revenueBands.from_100m_to_300m },
  { value: 'above_300m', label: uz.onboarding.revenueBands.above_300m },
]

export const MESSAGE_VOLUME_OPTIONS: { value: MessageVolumeKey; label: string }[] = [
  { value: '1_10', label: uz.onboarding.messageVolumes['1_10'] },
  { value: '10_50', label: uz.onboarding.messageVolumes['10_50'] },
  { value: '50_200', label: uz.onboarding.messageVolumes['50_200'] },
  { value: '200_plus', label: uz.onboarding.messageVolumes['200_plus'] },
]

export const REPLY_TEAM_OPTIONS: { value: ReplyTeamKey; label: string }[] = [
  { value: 'owner_only', label: uz.onboarding.replyTeams.owner_only },
  { value: 'small_team', label: uz.onboarding.replyTeams.small_team },
  { value: 'dedicated_manager', label: uz.onboarding.replyTeams.dedicated_manager },
]

export const TONE_OPTIONS: { value: ToneKey; label: string }[] = [
  { value: 'short_warm', label: uz.onboarding.tones.short_warm },
  { value: 'formal', label: uz.onboarding.tones.formal },
  { value: 'friendly', label: uz.onboarding.tones.friendly },
]

export const REPLY_MODE_OPTIONS: { value: ReplyModeKey; label: string }[] = [
  { value: 'draft', label: uz.onboarding.replyModes.draft },
  { value: 'safe_autopilot', label: uz.onboarding.replyModes.safe_autopilot },
]

export const PERMISSION_MODE_OPTIONS: { value: PermissionModeKey; label: string; description: string }[] = [
  {
    value: 'ask_always',
    label: 'Har doim so‘rasin',
    description: 'Javob, yangilash va yuborishdan oldin siz tasdiqlaysiz.',
  },
  {
    value: 'auto_approve',
    label: 'Xavfsizini o‘zi qilsin',
    description: 'Past xavfli ishlarni bajaradi, noaniq joyda ruxsat so‘raydi.',
  },
  {
    value: 'full_access',
    label: 'To‘liq ruxsat',
    description: 'Faqat ishonchli manba va siyosat doirasida avtomatik ishlaydi.',
  },
]

export const DEFAULT_AGENT_OPTIONS: { value: DefaultAgentKey; label: string; description: string; tools: string }[] = [
  {
    value: 'seller',
    label: 'Sotuvchi',
    description: 'Mijozga javob taklif qiladi va savdoni oldinga siljitadi.',
    tools: 'Telegram, Brain, suhbat tarixi',
  },
  {
    value: 'support',
    label: 'Mijoz yordami',
    description: 'Kompaniya, xizmat, kafolat va qoida savollariga javob beradi.',
    tools: 'Bilim bazasi, qoidalar',
  },
  {
    value: 'follow_up',
    label: 'Qayta aloqa',
    description: 'Sovib qolgan mijozlar va va’da qilingan ishlarni eslatadi.',
    tools: 'Suhbatlar, vazifalar',
  },
  {
    value: 'catalog_update',
    label: 'Katalog yangilash',
    description: 'Kanal, PDF yoki jadvaldan yangi mahsulot/SKU taklif qiladi.',
    tools: 'Manbalar, katalog',
  },
  {
    value: 'bi',
    label: 'BI yordamchi',
    description: 'Brain, vazifalar va agentlarni buyruq bilan boshqarishga yordam beradi.',
    tools: 'Barcha ichki dalillar',
  },
]
