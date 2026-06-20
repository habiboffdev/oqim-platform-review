import type { ReactNode } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'

export function SourceChoiceCard({
  icon,
  title,
  description,
  active,
  onClick,
  children,
}: {
  icon: ReactNode
  title: string
  description: string
  active: boolean
  onClick?: () => void
  children?: ReactNode
}) {
  const content = (
    <Card
      size="sm"
      className={cn(
        'min-h-36 justify-center py-0 transition-colors',
        active ? 'ring-2 ring-foreground' : 'hover:bg-muted/20',
      )}
    >
      <CardContent className="grid justify-items-center gap-3 px-5 py-6 text-center">
        <span className="grid size-16 place-items-center rounded-2xl bg-muted text-foreground [&_svg]:size-7">
          {icon}
        </span>
        <span>
          <span className="block font-medium">{title}</span>
          <span className="mt-1 block text-sm text-muted-foreground">{description}</span>
        </span>
        {children}
      </CardContent>
    </Card>
  )

  if (!onClick) return content
  return (
    <button type="button" className="w-full text-left" onClick={onClick}>
      {content}
    </button>
  )
}
