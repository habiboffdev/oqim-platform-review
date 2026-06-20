import { useRef, useState, type ChangeEvent } from 'react'
// eslint-disable-next-line no-restricted-imports
// eslint-disable-next-line no-restricted-imports -- TODO: migrate to useMountEffect or TanStack Query
import { useEffect } from 'react'
import { useMountEffect } from '@/hooks/use-mount-effect'
import { MagnifyingGlass } from '@phosphor-icons/react'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

interface SearchInputProps {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  debounceMs?: number
  className?: string
}

export function SearchInput({
  value,
  onChange,
  placeholder,
  debounceMs = 200,
  className,
}: SearchInputProps) {
  const [local, setLocal] = useState(value)
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined)

  // TODO: convert to TanStack Query — sync external → local (e.g. parent resets value)
  useEffect(() => { setLocal(value) }, [value])

  function handleChange(next: string) {
    setLocal(next)
    clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => onChange(next), debounceMs)
  }

  // Cleanup timer on unmount
  useMountEffect(() => () => clearTimeout(timerRef.current))

  return (
    <div className={cn('relative max-w-sm', className)}>
      <MagnifyingGlass
        size={16}
        weight="thin"
        className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground"
      />
      <Input
        type="text"
        value={local}
        onChange={(e: ChangeEvent<HTMLInputElement>) => handleChange(e.target.value)}
        placeholder={placeholder}
        className="h-9 w-full pl-9 pr-3 text-sm"
      />
    </div>
  )
}
