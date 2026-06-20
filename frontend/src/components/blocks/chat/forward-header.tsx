import { ArrowBendUpRight } from '@phosphor-icons/react'
import { uz } from '@/lib/uz'

interface ForwardHeaderProps {
  fromName: string
}

export function ForwardHeader({ fromName }: ForwardHeaderProps) {
  return (
    <div className="tg-forward-header">
      <ArrowBendUpRight size={14} weight="thin" className="tg-forward-icon" />
      <span className="tg-forward-label">
        {uz.conversations.forwarded} {fromName}
      </span>
    </div>
  )
}
