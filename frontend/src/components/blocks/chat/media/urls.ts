import type { Message } from '@/lib/types'

export function getFullMediaUrl(message: Message): string | undefined {
  return message.media_full_url ?? message.media_url
}

function appendCacheBust(url: string, key: string, value: string): string {
  const separator = url.includes('?') ? '&' : '?'
  return `${url}${separator}${key}=${value}`
}

export function getPreviewMediaUrl(message: Message): string | undefined {
  const previewUrl = message.media_preview_url ?? getFullMediaUrl(message)
  if (!previewUrl) {
    return previewUrl
  }
  if (message.media_type === 'video_note') {
    return appendCacheBust(previewUrl, 'preview_runtime', 'vnote')
  }
  return previewUrl
}
