import type { ReactNode } from 'react'

interface PageHeaderProps {
  title: string
  count?: number
  description?: string
  actions?: ReactNode
}

export function PageHeader({ title, count, description, actions }: PageHeaderProps) {
  return (
    <div className="flex items-start justify-between gap-4 px-6 pt-6 pb-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">
          {title}
          {count !== undefined && (
            <span className="ml-2 text-muted-foreground">{count}</span>
          )}
        </h1>
        {description && (
          <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}
