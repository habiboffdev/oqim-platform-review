import { User, Phone } from '@phosphor-icons/react'
import type { Message } from '@/lib/types'

interface ContactMeta {
  phone_number?: string
  first_name?: string
  last_name?: string
}

interface ContactMessageProps {
  message: Message
}

export function ContactMessage({ message }: ContactMessageProps) {
  const meta = message.media_metadata as ContactMeta | undefined

  if (!meta?.phone_number) return null

  const name = [meta.first_name, meta.last_name].filter(Boolean).join(' ')

  return (
    <div className="tg-contact-card">
      {name && (
        <div className="tg-contact-card-row">
          <User size={20} weight="thin" />
          <span className="tg-contact-name">{name}</span>
        </div>
      )}
      <div className="tg-contact-card-row">
        <Phone size={16} weight="thin" />
        <span className="tg-contact-phone">{meta.phone_number}</span>
      </div>
    </div>
  )
}
