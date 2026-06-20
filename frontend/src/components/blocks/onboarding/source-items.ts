import { toast } from 'sonner'
import {
  isOnboardingScreenshotSource,
  readOnboardingFileSource,
  type OnboardingFileSource,
} from '@/lib/file-source'
import { uz } from '@/lib/uz'

export function splitSourceLines(value: string) {
  return value
    .split(/[\n,]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

export function buildOnboardingSourceItems({
  sourceNotes = '',
  websiteSource,
  telegramChannelSource,
  telegramStartDate = '',
  telegramEndDate = '',
  fileSource,
  agentWebsiteSource = '',
  agentFileSource = null,
  voiceSource,
  voiceFileSource,
}: {
  sourceNotes?: string
  websiteSource: string
  telegramChannelSource: string
  telegramStartDate?: string
  telegramEndDate?: string
  fileSource: OnboardingFileSource | null
  agentWebsiteSource?: string
  agentFileSource?: OnboardingFileSource | null
  voiceSource: string
  voiceFileSource: OnboardingFileSource | null
}) {
  const items: Array<Record<string, unknown>> = []
  const manualText = sourceNotes.trim()
  if (manualText) {
    items.push({
      kind: 'text',
      label: uz.onboarding.manualBrainSource,
      text: manualText,
      purpose: 'brain_data',
    })
  }
  for (const websiteUrl of splitSourceLines(websiteSource)) {
    items.push({
      kind: 'website',
      label: websiteUrl,
      url: websiteUrl,
      purpose: 'brain_data',
    })
  }
  for (const channelHandle of splitSourceLines(telegramChannelSource)) {
    items.push({
      kind: 'telegram_channel',
      label: channelHandle,
      handle: channelHandle,
      purpose: 'brain_data',
      ...(telegramStartDate.trim() ? { date_from: telegramStartDate.trim() } : {}),
      ...(telegramEndDate.trim() ? { date_to: telegramEndDate.trim() } : {}),
    })
  }
  if (fileSource) {
    items.push({
      kind: isOnboardingScreenshotSource(fileSource) ? 'screenshot' : 'file',
      label: fileSource.fileName,
      file_name: fileSource.fileName,
      content_type: fileSource.contentType,
      content_base64: fileSource.contentBase64,
      byte_size: fileSource.byteSize,
      purpose: 'brain_data',
    })
  }
  for (const websiteUrl of splitSourceLines(agentWebsiteSource)) {
    items.push({
      kind: 'website',
      label: websiteUrl,
      url: websiteUrl,
      purpose: 'agent_data',
    })
  }
  if (agentFileSource) {
    items.push({
      kind: isOnboardingScreenshotSource(agentFileSource) ? 'screenshot' : 'file',
      label: agentFileSource.fileName,
      file_name: agentFileSource.fileName,
      content_type: agentFileSource.contentType,
      content_base64: agentFileSource.contentBase64,
      byte_size: agentFileSource.byteSize,
      purpose: 'agent_data',
    })
  }
  const transcript = voiceSource.trim()
  if (transcript) {
    items.push({
      kind: 'text',
      label: voiceFileSource?.fileName ? `Audio matni: ${voiceFileSource.fileName}` : uz.onboarding.voiceSource,
      text: transcript,
      purpose: 'agent_data',
    })
  }
  return items
}

export function toBusinessBrainSourcePayload(item: Record<string, unknown>) {
  const allowedKeys = [
    'kind',
    'label',
    'text',
    'url',
    'handle',
    'file_name',
    'content_type',
    'content_base64',
    'byte_size',
    'transcript',
    'purpose',
    'date_from',
    'date_to',
  ] as const
  const payload: Record<string, unknown> = {}
  for (const key of allowedKeys) {
    const value = item[key]
    if (value !== undefined && value !== null && value !== '') {
      payload[key] = value
    }
  }
  return payload
}

export async function readSelectedOnboardingFile(
  file: File | undefined,
  setter: (value: OnboardingFileSource | null) => void,
) {
  if (!file) {
    setter(null)
    return
  }
  try {
    setter(await readOnboardingFileSource(file))
  } catch {
    setter(null)
    toast.error(uz.onboarding.fileReadFailed)
  }
}
