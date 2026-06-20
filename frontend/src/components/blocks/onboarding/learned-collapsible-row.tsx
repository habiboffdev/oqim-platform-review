import type { ReactNode } from 'react'
import { CaretDown } from '@phosphor-icons/react'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { Badge } from '@/components/ui/badge'

export function LearnedCollapsibleRow({
  icon,
  title,
  status,
  variant,
  defaultOpen,
  children,
}: {
  icon: ReactNode
  title: string
  status: string
  variant: 'success' | 'info' | 'warning' | 'outline'
  defaultOpen?: boolean
  children: ReactNode
}) {
  return (
    <Collapsible defaultOpen={defaultOpen} className="rounded-xl border border-border bg-background">
      <CollapsibleTrigger className="flex w-full items-center justify-between gap-4 px-5 py-4 text-left">
        <span className="flex min-w-0 items-center gap-3">
          <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-muted/70 text-foreground [&_svg]:size-4">
            {icon}
          </span>
          <span className="font-medium">{title}</span>
        </span>
        <span className="flex shrink-0 items-center gap-3">
          <Badge variant={variant}>{status}</Badge>
          <CaretDown className="size-4 text-muted-foreground" />
        </span>
      </CollapsibleTrigger>
      <CollapsibleContent className="border-t border-border px-5 py-5">
        {children}
      </CollapsibleContent>
    </Collapsible>
  )
}
