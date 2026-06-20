import { ClockCounterClockwise, WarningCircle } from '@phosphor-icons/react'

import type { MediaRuntimeDisplay } from '@/lib/media-ui-state'
import { cn } from '@/lib/utils'

export function MediaRuntimeStatus({ display }: { display: MediaRuntimeDisplay }) {
  const Icon = display.tone === 'unavailable' ? WarningCircle : ClockCounterClockwise

  return (
    <div
      className={cn(
        'tg-media-runtime',
        display.tone === 'unavailable' && 'tg-media-runtime-unavailable',
      )}
    >
      <Icon size={16} weight="thin" />
      <span>{display.label}</span>
    </div>
  )
}
