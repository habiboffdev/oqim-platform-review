import type { Message } from '@/lib/types'
import { uz } from '@/lib/uz'

export type MediaRuntimeTone = 'pending' | 'retrying' | 'unavailable'

export type MediaRuntimeDisplay = {
  tone: MediaRuntimeTone
  label: string
  blocking: boolean
}

const MEDIA_PLACEHOLDERS = /^\[(photo|video|sticker|gif|voice|video_note|audio|document|contact|location|live_location)\]$/
const PENDING_STATES = new Set(['pending', 'leased'])
const RETRYING_STATES = new Set(['deferred', 'retrying'])
const UNAVAILABLE_STATES = new Set(['unavailable', 'failed', 'expired'])

export function isMediaPlaceholder(message: Message): boolean {
  if (!message.media_type || !message.content) return false
  return MEDIA_PLACEHOLDERS.test(message.content.trim())
}

export function getRenderableMediaType(message: Message): string | undefined {
  const rawType = message.media_type
  const meta = message.media_metadata as Record<string, unknown> | undefined
  const mime = typeof meta?.mime_type === 'string' ? meta.mime_type.toLowerCase() : ''
  const fileName = typeof meta?.file_name === 'string' ? meta.file_name.toLowerCase() : ''
  const hasEmoji = typeof meta?.emoji === 'string' && meta.emoji.length > 0

  if (rawType === 'document') {
    if (
      mime === 'application/x-tgsticker'
      || fileName.endsWith('.tgs')
      || (mime === 'video/webm' && (hasEmoji || fileName.includes('sticker')))
      || (mime === 'image/webp' && (hasEmoji || fileName.includes('sticker')))
    ) {
      return 'sticker'
    }
    if (mime.startsWith('image/')) return 'photo'
  }

  return rawType
}

export function getMediaRuntimeDisplay(message: Message): MediaRuntimeDisplay | null {
  const runtime = message.media_runtime
  if (!runtime) return null

  const hydration = runtime.hydration_status?.toLowerCase()
  const action = runtime.action_state?.toLowerCase()
  const asset = runtime.asset_state?.toLowerCase()
  const semantic = runtime.semantic_state?.toLowerCase()

  if (
    UNAVAILABLE_STATES.has(hydration ?? '')
    || UNAVAILABLE_STATES.has(asset ?? '')
    || UNAVAILABLE_STATES.has(semantic ?? '')
    || action === 'failed'
  ) {
    return {
      tone: 'unavailable',
      label: uz.conversations.mediaUnavailable,
      blocking: true,
    }
  }

  if (
    RETRYING_STATES.has(hydration ?? '')
    || RETRYING_STATES.has(action ?? '')
    || RETRYING_STATES.has(asset ?? '')
    || RETRYING_STATES.has(semantic ?? '')
  ) {
    return {
      tone: 'retrying',
      label: uz.conversations.mediaRetrying,
      blocking: false,
    }
  }

  if (PENDING_STATES.has(hydration ?? '') || PENDING_STATES.has(action ?? '')) {
    return {
      tone: 'pending',
      label: uz.conversations.mediaPreparing,
      blocking: false,
    }
  }

  return null
}
