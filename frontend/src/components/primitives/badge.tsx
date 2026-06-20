import { cn } from '@/lib/utils'

type BadgeVariant = 'default' | 'success' | 'warning' | 'danger' | 'info' | 'muted' | 'accent'

interface BadgeProps {
  children: React.ReactNode
  variant?: BadgeVariant
  className?: string
}

const variantStyles: Record<BadgeVariant, string> = {
  default: 'bg-muted text-muted-foreground',
  success: 'bg-success/10 text-success',
  warning: 'bg-warning/10 text-warning',
  danger: 'bg-destructive/10 text-destructive',
  info: 'bg-info/10 text-info',
  muted: 'bg-muted/60 text-muted-foreground',
  accent: 'bg-primary text-primary-foreground',
}

export function Badge({ children, variant = 'default', className }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-medium',
        variantStyles[variant],
        className,
      )}
    >
      {children}
    </span>
  )
}
