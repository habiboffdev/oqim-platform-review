export interface OnboardingFileSource {
  fileName: string
  contentType: string
  contentBase64: string
  byteSize: number
}

const MAX_ONBOARDING_FILE_BYTES = 8 * 1024 * 1024

export async function readOnboardingFileSource(file: File): Promise<OnboardingFileSource> {
  if (file.size > MAX_ONBOARDING_FILE_BYTES) {
    throw new Error('onboarding_file_too_large')
  }
  const buffer = await file.arrayBuffer()
  return {
    fileName: file.name,
    contentType: file.type || 'application/octet-stream',
    contentBase64: arrayBufferToBase64(buffer),
    byteSize: file.size,
  }
}

export function formatBytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

export function isOnboardingScreenshotSource(source: OnboardingFileSource) {
  const contentType = source.contentType.toLowerCase()
  const fileName = source.fileName.toLowerCase()
  return (
    contentType.startsWith('image/')
    || fileName.endsWith('.png')
    || fileName.endsWith('.jpg')
    || fileName.endsWith('.jpeg')
    || fileName.endsWith('.webp')
    || fileName.endsWith('.heic')
  )
}

function arrayBufferToBase64(buffer: ArrayBuffer) {
  const bytes = new Uint8Array(buffer)
  const chunkSize = 0x8000
  let binary = ''
  for (let index = 0; index < bytes.length; index += chunkSize) {
    const chunk = bytes.subarray(index, index + chunkSize)
    binary += String.fromCharCode(...chunk)
  }
  return btoa(binary)
}
