import type { ReactNode } from 'react'
import type { Message } from '@/lib/types'
import {
  getMediaRuntimeDisplay,
  getRenderableMediaType,
} from '@/lib/media-ui-state'
import { PhotoMessage } from './photo-message'
import { VideoMessage } from './video-message'
import { DocumentMessage } from './document-message'
import { StickerMessage } from './sticker-message'
import { GifMessage } from './gif-message'
import { ContactMessage } from './contact-message'
import { LocationMessage } from './location-message'
import { VoiceMessage } from './voice-message'
import { VideoNoteMessage } from './video-note-message'
import { AudioMessage } from './audio-message'
import { MediaRuntimeStatus } from './media-runtime-status'

interface MediaContentProps {
  message: Message
  onPhotoClick?: (messageId: number) => void
}

export function MediaContent({ message, onPhotoClick }: MediaContentProps) {
  const runtimeDisplay = getMediaRuntimeDisplay(message)
  if (runtimeDisplay?.blocking) {
    return <MediaRuntimeStatus display={runtimeDisplay} />
  }

  let rendered: ReactNode = null
  switch (getRenderableMediaType(message)) {
    case 'photo':
      rendered = <PhotoMessage message={message} onPhotoClick={onPhotoClick} />
      break
    case 'voice':
      rendered = <VoiceMessage message={message} />
      break
    case 'video':
      rendered = <VideoMessage message={message} />
      break
    case 'video_note':
      rendered = <VideoNoteMessage message={message} />
      break
    case 'document':
      rendered = <DocumentMessage message={message} />
      break
    case 'audio':
      rendered = <AudioMessage message={message} />
      break
    case 'sticker':
      rendered = <StickerMessage message={message} />
      break
    case 'gif':
      rendered = <GifMessage message={message} />
      break
    case 'contact':
      rendered = <ContactMessage message={message} />
      break
    case 'location':
    case 'live_location':
      rendered = <LocationMessage message={message} />
      break
  }

  return (
    <>
      {rendered}
      {runtimeDisplay && <MediaRuntimeStatus display={runtimeDisplay} />}
    </>
  )
}
