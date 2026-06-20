import { uz } from '@/lib/uz'

interface UnreadDividerProps {
  count: number
}

export function UnreadDivider({ count }: UnreadDividerProps) {
  return (
    <div
      className="flex items-center gap-3 py-2"
      role="separator"
      aria-label={`${count} ${uz.conversations.newMessages}`}
    >
      <div className="flex-1 h-px bg-[var(--tg-primary)]" />
      <span className="text-xs font-medium text-[var(--tg-primary)]">
        {count} {uz.conversations.newMessages}
      </span>
      <div className="flex-1 h-px bg-[var(--tg-primary)]" />
    </div>
  )
}
