import { normalizeTextEntities } from '@/lib/active-tail-sync'
import type { DeliveryRuntimeProjection, Message } from '@/lib/types'

const NON_DOWNLOADABLE_MEDIA_TYPES = new Set(['poll', 'contact', 'location', 'live_location'])
const PREVIEW_MEDIA_TYPES = new Set(['photo', 'video', 'video_note', 'sticker', 'gif'])

function stringOrUndefined(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined
}

function numberOrUndefined(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function booleanOrDefault(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback
}

function deliveryStateOrUndefined(value: unknown): Message['delivery_state'] {
  if (
    value === 'pending'
    || value === 'unknown'
    || value === 'confirmed'
    || value === 'failed'
  ) {
    return value
  }
  return undefined
}

function deliveryRuntimeOrUndefined(value: unknown): DeliveryRuntimeProjection | null | undefined {
  if (!value || typeof value !== 'object') return undefined
  return value as DeliveryRuntimeProjection
}

export function normalizeLiveMessagePayload(
  rawMsg: Record<string, unknown>,
  conversationId: number,
): Message {
  const mediaType = stringOrUndefined(rawMsg.media_type)
  const telegramChatId = numberOrUndefined(rawMsg.telegram_chat_id)
  const telegramMsgId = numberOrUndefined(rawMsg.telegram_message_id)
  const mediaUrl = stringOrUndefined(rawMsg.media_url)
    || (mediaType && telegramChatId && telegramMsgId && !NON_DOWNLOADABLE_MEDIA_TYPES.has(mediaType)
      ? `/api/media/${telegramChatId}/${telegramMsgId}`
      : undefined)
  const mediaFullUrl = stringOrUndefined(rawMsg.media_full_url) || mediaUrl
  const mediaPreviewUrl = stringOrUndefined(rawMsg.media_preview_url)
    || (mediaType && PREVIEW_MEDIA_TYPES.has(mediaType) && mediaFullUrl
      ? `${mediaFullUrl}?thumb=true`
      : undefined)

  return {
    id: rawMsg.id as number,
    conversation_id: conversationId,
    sender_type: rawMsg.sender_type as string,
    content: rawMsg.content as string,
    channel: stringOrUndefined(rawMsg.channel) || 'telegram',
    telegram_message_id: telegramMsgId,
    is_read: booleanOrDefault(rawMsg.is_read, false),
    media_type: mediaType,
    media_url: mediaUrl,
    media_full_url: mediaFullUrl,
    media_preview_url: mediaPreviewUrl,
    media_metadata: (rawMsg.media_metadata as Record<string, unknown>) || undefined,
    text_entities: normalizeTextEntities(rawMsg.text_entities),
    reply_to_msg_id: numberOrUndefined(rawMsg.reply_to_msg_id),
    forward_from_name: stringOrUndefined(rawMsg.forward_from_name),
    edited_at: undefined,
    reactions: [],
    external_message_id: stringOrUndefined(rawMsg.external_message_id),
    created_at: rawMsg.created_at as string,
    grouped_id: numberOrUndefined(rawMsg.grouped_id),
    telegram_timestamp: stringOrUndefined(rawMsg.telegram_timestamp),
    client_message_uuid: stringOrUndefined(rawMsg.client_message_uuid),
    delivery_state: deliveryStateOrUndefined(rawMsg.delivery_state),
    delivery_runtime: deliveryRuntimeOrUndefined(rawMsg.delivery_runtime),
    conversation_seq: numberOrUndefined(rawMsg.conversation_seq),
    conversation_revision: numberOrUndefined(rawMsg.conversation_revision),
  }
}
