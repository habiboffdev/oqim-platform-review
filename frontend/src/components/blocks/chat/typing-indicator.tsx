import { cn } from '@/lib/utils'
import { uz } from '@/lib/uz'

export function TypingIndicator() {
  return (
    <div className="absolute bottom-1 left-4 z-10" aria-live="polite">
      <div
        className={cn(
          'inline-flex items-center gap-1.5 px-3 py-1.5',
          'bg-background/90 backdrop-blur-sm rounded-full',
          'text-xs text-muted-foreground',
          'shadow-sm border border-border',
        )}
      >
        <span>{uz.conversations.typing}</span>
        <span className="flex gap-1">
          <span
            className="animate-bounce size-1 rounded-full bg-muted-foreground"
            style={{ animationDelay: '0ms' }}
          />
          <span
            className="animate-bounce size-1 rounded-full bg-muted-foreground"
            style={{ animationDelay: '150ms' }}
          />
          <span
            className="animate-bounce size-1 rounded-full bg-muted-foreground"
            style={{ animationDelay: '300ms' }}
          />
        </span>
      </div>
    </div>
  )
}
