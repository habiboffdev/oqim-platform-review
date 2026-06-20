import { ArrowDown } from '@phosphor-icons/react'

import { uz } from '@/lib/uz'
import { cn } from '@/lib/utils'

interface ScrollToBottomFabProps {
  visible: boolean
  unreadCount: number
  onClick: () => void
}

export function ScrollToBottomFab({ visible, unreadCount, onClick }: ScrollToBottomFabProps) {
  if (!visible) return null

  return (
    <button
      onClick={onClick}
      className={cn(
        'absolute bottom-4 right-4 z-10',
        'flex items-center justify-center',
        'size-9 rounded-full',
        'bg-background border border-border shadow-md',
        'hover:bg-accent transition-colors',
      )}
      aria-label={uz.conversations.scrollToBottom}
    >
      <ArrowDown size={18} weight="thin" className="text-foreground" />
      {unreadCount > 0 && (
        <span
          className={cn(
            'absolute -top-2 -right-1',
            'flex items-center justify-center',
            'min-w-5 h-5 px-1 rounded-full',
            'bg-[var(--tg-primary)] text-white text-xs font-medium',
          )}
        >
          {unreadCount > 99 ? '99+' : unreadCount}
        </span>
      )}
    </button>
  )
}
