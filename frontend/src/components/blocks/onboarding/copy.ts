import type {
  IngestionProgress,
  OnboardingLearnedReviewItem,
  OnboardingLearnedReviewProduct,
  OnboardingRuntimeProjection,
  OnboardingSourceEvidence,
} from '@/lib/types'
import { uz } from '@/lib/uz'
import type { Phase } from './types'

export function onboardingStageCopy(phase: Phase) {
  if (phase === 'preferences') {
    return {
      stage: 2,
      crumb: 'Onboarding 2-bosqich',
      section: 'Agent sozlamalari',
    }
  }
  if (phase === 'sources') {
    return {
      stage: 1,
      crumb: 'Onboarding · 1-qadam',
      section: 'Business Brain',
    }
  }
  if (phase === 'documents') {
    return {
      stage: 2,
      crumb: uz.onboarding.documents.crumb,
      section: uz.onboarding.documents.section,
    }
  }
  if (phase === 'launch') {
    return {
      stage: 3,
      crumb: uz.onboarding.launch.crumb,
      section: uz.onboarding.launch.title,
    }
  }
  if (phase === 'credentials') {
    return {
      stage: 3,
      crumb: 'Onboarding 3-bosqich',
      section: 'Ishga tushirish',
    }
  }
  return {
    stage: 1,
    crumb: 'Onboarding',
    section: 'Telegram',
  }
}

export function runtimeStateLabel(
  runtime: OnboardingRuntimeProjection | undefined,
  isLoading: boolean,
  startFailed: boolean,
) {
  if (startFailed) return 'Yordam kerak'
  if (isLoading) return 'Tekshirilmoqda'
  if (!runtime) return 'Hali boshlanmagan'
  if (runtime.is_dlq) return 'Yordam kerak'
  if (runtime.is_running) return 'Ishlayapti'
  if (runtime.is_terminal) return 'Tayyor'
  return 'Kutilmoqda'
}

export function sellerSafeStatusText(value: string | null | undefined) {
  if (!value) return null
  const normalized = value.trim().toLowerCase()
  if (!normalized) return null
  if (normalized.includes('voice_profile_degraded')) {
    return 'Yozish uslubi uchun suhbat yetarli emas. Ko‘proq suhbat, audio yoki qoida qo‘shish mumkin.'
  }
  if (normalized.includes('no_source') || normalized.includes('missing')) {
    return 'Bu bo‘lim uchun hali manba qo‘shilmagan.'
  }
  if (normalized.includes('timeout')) return 'O‘rganish vaqtida tugamadi. Qayta urinish mumkin.'
  if (normalized.includes('fetch_failed') || normalized.includes('network')) return 'Manbaga ulanishda xatolik bo‘ldi.'
  if (/^[a-z0-9_:. -]+$/.test(normalized) && normalized.includes('_')) return 'OQIM bu qismni to‘liq tayyorlay olmadi.'
  return value
}

export function runtimeDetailText(value: string | null | undefined, fallback: string) {
  const safe = sellerSafeStatusText(value) ?? fallback
  return safe
    .replace(/\b(\d+)\s*ta\s*kontakt topildi\b/gi, '$1 ta suhbat ko‘rildi')
    .replace(/\b(\d+)\s*ta\s*bilim javobi\b/gi, '$1 ta bilim taklifi')
}

export function sellerSafeSourceTitle(value: string | null | undefined, kind: string) {
  const text = String(value || '').trim()
  const normalized = text.toLowerCase()
  if (!text || /^\d+:sources$/i.test(text) || /^onboarding:source/i.test(text)) {
    return ({
      website: 'Sayt',
      telegram_channel: 'Telegram kanal',
      screenshot: 'Rasm yoki screenshot',
      file: 'Fayl',
      voice_note: 'Ovoz',
      manual: 'Qo‘lda yozilgan ma’lumot',
    } as Record<string, string>)[kind] ?? 'Biznes manbasi'
  }
  if (/^(message|conversation):/i.test(normalized)) return 'Suhbatdan o‘rganilgan ma’lumot'
  if (normalized === 'manba' && kind === 'text') return 'Qo‘lda yozilgan ma’lumot'
  return text
}

export function stageStatusLabel(status: string) {
  return ({
    completed: 'Tayyor',
    running: 'Ishlayapti',
    in_progress: 'Ishlayapti',
    learned: 'O‘rganildi',
    needs_review: 'Ko‘rib chiqish kerak',
    failed: 'Xatolik',
    dlq: 'Yordam kerak',
    blocked: 'To‘xtagan',
    retryable: 'Qayta urinadi',
    degraded: 'Yordam kerak',
    not_applicable: 'Hali yo‘q',
    pending: 'Kutilmoqda',
    waiting: 'Kutilmoqda',
  } as Record<string, string>)[status] ?? status.replaceAll('_', ' ')
}

export function sourceLearningStatusLabel(status: string) {
  return ({
    learning: 'O‘rganilyapti',
    learned: 'O‘rganildi',
    needs_review: 'Tasdiqlash kerak',
    missing: 'Ma’lumot yetmayapti',
    conflict: 'Aniqlashtirish kerak',
    retrying: 'Qayta urinilyapti',
    failed: 'Yordam kerak',
  } as Record<string, string>)[status] ?? stageStatusLabel(status)
}

export function sourceLearningReasonLabel(reason: string) {
  const normalized = reason.trim().toLowerCase()
  if (!normalized) return null
  if (normalized.includes('missing_file_content')) return 'Fayldan ma’lumot o‘qilmadi'
  if (normalized.includes('unsupported')) return 'Bu manba turi hali to‘liq qo‘llanmaydi'
  if (normalized.includes('timeout')) return 'Manba vaqtida o‘qilmadi'
  if (normalized.includes('fetch') || normalized.includes('network')) return 'Manbaga ulanishda xatolik bo‘ldi'
  if (normalized.includes('empty')) return 'Manbada o‘rganadigan matn topilmadi'
  if (normalized.includes('gateway') || normalized.includes('provider')) return 'AI o‘rganish vaqtincha to‘liq ishlamadi'
  return 'Manbani qayta tekshirish kerak'
}

export function historyLearningDetail(progress: IngestionProgress | null) {
  const limit = progress?.history_learning_conversation_limit ?? 50
  const messages = progress?.history_learning_message_limit ?? 12
  return uz.onboarding.learningHistoryWindow(limit, messages)
}

export function historyLearningContactCount(progress: IngestionProgress | null) {
  const contacts = progress?.contacts_found ?? 0
  const limit = progress?.history_learning_conversation_limit ?? 50
  return Math.min(contacts, limit)
}

export function historyLearningCustomerCount(progress: IngestionProgress | null) {
  const customers = progress?.customers_identified ?? 0
  const limit = progress?.history_learning_conversation_limit ?? 50
  return Math.min(customers, limit)
}

export function historyProcessedDetail(progress: IngestionProgress | null) {
  const conversations = progress?.history_replayed_conversations ?? 0
  const messages = progress?.history_replayed_messages ?? 0
  if (conversations <= 0 && messages <= 0) return undefined
  return uz.onboarding.learningHistoryProcessed(conversations, messages)
}

export function voiceLearningDetail(progress: IngestionProgress) {
  const discoveries = progress.voice_discoveries ?? []
  if (progress.voice_profile_ready) {
    return discoveries.length
      ? uz.onboarding.voiceLearningDetailReady(discoveries.length)
      : uz.onboarding.voiceLearningDetailReady(0)
  }
  if (progress.voice_profile_degraded) return uz.onboarding.voiceLearningDetailWeak
  return uz.onboarding.voiceLearningDetailPending
}

export function onboardingHistoryRuntimeDetail(progress: IngestionProgress) {
  const limit = progress.history_learning_conversation_limit ?? 50
  const parts = [
    uz.onboarding.historyBackfillDetail(progress.contacts_found, progress.customers_identified, limit),
    historyLearningDetail(progress),
  ]
  const processed = historyProcessedDetail(progress)
  if (processed) parts.push(processed)
  return parts.join(' ')
}

export function learnedFactText(item: OnboardingLearnedReviewItem) {
  return item.answer || item.summary || item.rule || item.requirement || item.observations.join(' ')
}

export function reviewProductTitle(product: OnboardingLearnedReviewProduct) {
  const title = product.title.trim()
  if (/^(message|conversation):/i.test(title)) return 'Suhbatdan topilgan mahsulot yoki taklif'
  return title || 'Topilgan taklif'
}

export function reviewFactTitle(item: OnboardingLearnedReviewItem) {
  const title = item.topic || item.question || item.summary || item.rule || uz.onboarding.learnedReview.factFallback
  if (/^(message|conversation):/i.test(String(title))) return 'Suhbatdan topilgan qoida yoki javob'
  return title
}

export function reviewEvidenceCopy(sourceCount: number, confidence: number) {
  const confidenceText = Math.round(confidence * 100)
  if (sourceCount <= 0) return `${confidenceText}% ishonch`
  return `${sourceCount} dalil · ${confidenceText}%`
}

export function reviewEvidenceLabel(source: OnboardingSourceEvidence) {
  return sellerSafeSourceTitle(source.label, source.kind)
}

export function reviewEvidenceMeta(source: OnboardingSourceEvidence) {
  const unit = friendlyEvidenceUnit(source.unit_label)
  if (unit) return unit
  const detail = source.detail?.trim()
  return detail && detail !== source.label ? detail : null
}

export function friendlyEvidenceUnit(value: string | null | undefined) {
  const text = String(value || '').trim()
  if (!text) return null
  if (/^bo‘lak\s+0*\d+$/i.test(text)) return 'matn bo‘lagi'
  if (/^chunk\s+0*\d+$/i.test(text)) return 'matn bo‘lagi'
  return text
}

export function voiceDiscoveryLabel(item: { label?: string; subtitle?: string } | string) {
  if (typeof item === 'string') return safeVoiceDiscoveryText(item)
  const label = safeVoiceDiscoveryText(item.label) || 'Uslub belgisi'
  const subtitle = item.subtitle?.trim()
  const cleanSubtitle = safeVoiceDiscoveryText(subtitle)
  return cleanSubtitle ? `${label} · ${cleanSubtitle}` : label
}

function safeVoiceDiscoveryText(value: string | null | undefined) {
  const text = String(value || '').trim()
  if (!text) return ''
  if (/^burst=\d+$/i.test(text)) return ''
  if (/^learned$/i.test(text)) return 'O‘rganildi'
  return text.replace(/\blearned\b/gi, 'o‘rganildi')
}
