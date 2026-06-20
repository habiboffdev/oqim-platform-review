import { uz } from '@/lib/uz'

interface DateSeparatorProps {
  label: string
}

export function DateSeparator({ label }: DateSeparatorProps) {
  return (
    <div className="tg-date-separator">
      <span>{label}</span>
    </div>
  )
}

export function formatDateSeparator(iso: string): string {
  const d = new Date(iso)
  const now = new Date()
  const diff = now.getTime() - d.getTime()
  const days = Math.floor(diff / 86400000)
  if (days === 0) return uz.conversations.today
  if (days === 1) return uz.conversations.yesterday
  return d.toLocaleDateString('uz-UZ', { day: 'numeric', month: 'long', year: 'numeric' })
}
