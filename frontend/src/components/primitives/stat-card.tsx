import type { ElementType } from 'react'
import { Skeleton } from './skeleton'

interface StatCardProps {
  icon: ElementType
  label: string
  value: string | number
  sublabel?: string
  trend?: { value: number; isPositive: boolean }
  isLoading?: boolean
}

export function StatCard({ icon: Icon, label, value, sublabel, trend, isLoading }: StatCardProps) {
  return (
    <div className="rounded-xl border bg-card p-5">
      <div className="flex items-center gap-3">
        <div className="rounded-lg bg-muted p-2.5">
          <Icon size={20} weight="thin" className="text-muted-foreground" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-xs text-muted-foreground">{label}</p>
          {isLoading ? (
            <Skeleton className="mt-1 h-7 w-14" />
          ) : (
            <div className="flex items-baseline gap-2">
              <p className="text-2xl font-semibold tracking-tight">{value}</p>
              {trend && (
                <span
                  className={`text-xs font-medium ${trend.isPositive ? 'text-success' : 'text-destructive'}`}
                >
                  {trend.isPositive ? '+' : ''}{trend.value}%
                </span>
              )}
            </div>
          )}
          {sublabel && <p className="mt-0.5 text-[10px] text-muted-foreground">{sublabel}</p>}
        </div>
      </div>
    </div>
  )
}
