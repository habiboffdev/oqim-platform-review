import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function truncate(str: string, length: number): string {
  if (str.length <= length) return str
  return str.slice(0, length).trimEnd() + '…'
}

export function formatRelativeTime(dateStr: string | null | undefined): string {
  if (!dateStr) return '—'
  const date = new Date(dateStr)
  if (isNaN(date.getTime())) return '—'
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60_000)
  const diffHours = Math.floor(diffMs / 3_600_000)
  const diffDays = Math.floor(diffMs / 86_400_000)

  if (diffMins < 1) return 'hozir'
  if (diffMins < 60) return `${diffMins} daq`
  if (diffHours < 24) return `${diffHours} soat`
  if (diffDays < 7) return `${diffDays} kun`
  return date.toLocaleDateString('uz-UZ', { day: 'numeric', month: 'short' })
}
