import { api } from '@/lib/api-client'
import type { BusinessBrainAudioTranscriptResponse } from '@/lib/types'
import type { OnboardingFileSource } from '@/lib/file-source'

export async function transcribeOnboardingAudio(
  fileSource: OnboardingFileSource,
): Promise<BusinessBrainAudioTranscriptResponse> {
  return api.post<BusinessBrainAudioTranscriptResponse>('/api/business-brain/sources/audio-transcript', {
    content_base64: fileSource.contentBase64,
    content_type: fileSource.contentType || 'audio/webm',
    file_name: fileSource.fileName,
  })
}
