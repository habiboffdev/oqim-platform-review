import { MapPin } from '@phosphor-icons/react'
import { uz } from '@/lib/uz'
import type { Message } from '@/lib/types'

interface LocationMeta {
  latitude?: number
  longitude?: number
}

interface LocationMessageProps {
  message: Message
}

export function LocationMessage({ message }: LocationMessageProps) {
  const meta = message.media_metadata as LocationMeta | undefined

  if (meta?.latitude == null || meta?.longitude == null) return null

  const coords = `${meta.latitude.toFixed(6)}, ${meta.longitude.toFixed(6)}`
  const mapsUrl = `https://maps.google.com/?q=${meta.latitude},${meta.longitude}`

  return (
    <div className="tg-location-card">
      <div className="tg-location-header">
        <MapPin size={20} weight="thin" />
        <span>{uz.conversations.locationMessage}</span>
      </div>
      <span className="tg-location-coords">{coords}</span>
      <a
        href={mapsUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="tg-location-link"
      >
        {uz.conversations.openInMaps}
      </a>
    </div>
  )
}
