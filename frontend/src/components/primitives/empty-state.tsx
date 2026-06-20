import type { ReactNode, ElementType } from 'react'

interface EmptyStateProps {
  icon: ElementType
  title: string
  description: string
  action?: ReactNode
}

export function EmptyState({ icon: Icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <div className="mb-4 rounded-xl bg-muted p-4">
        <Icon size={28} weight="thin" className="text-muted-foreground" />
      </div>
      <h3 className="text-sm font-medium">{title}</h3>
      <p className="mt-1 max-w-xs text-sm text-muted-foreground">{description}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}
