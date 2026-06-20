import { cn } from '@/lib/utils'

interface AvatarProps {
  name: string
  size?: 'sm' | 'md' | 'lg'
  className?: string
}

const sizeMap = {
  sm: 'h-7 w-7 text-[10px]',
  md: 'h-8 w-8 text-xs',
  lg: 'h-10 w-10 text-sm',
}

export function Avatar({ name, size = 'md', className }: AvatarProps) {
  const initial = avatarInitial(name)

  return (
    <div
      className={cn(
        'flex items-center justify-center rounded-full border border-border/70 bg-muted/60 font-medium text-muted-foreground',
        sizeMap[size],
        className,
      )}
    >
      {initial}
    </div>
  )
}

export function avatarInitial(name: string) {
  const normalized = (name || '').normalize('NFKD')
  const match = normalized.match(/[\p{Letter}\p{Number}]/u)
  return match?.[0]?.toUpperCase() || 'M'
}
