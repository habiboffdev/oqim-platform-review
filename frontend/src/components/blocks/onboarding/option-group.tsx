import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
} from '@/components/ui/select'

export function OptionGroup<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string
  options: readonly { value: T; label: string }[]
  value: T
  onChange: (value: T) => void
}) {
  const selectedLabel = options.find((option) => option.value === value)?.label ?? value

  return (
    <div className="flex flex-col gap-2">
      <Label>{label}</Label>
      <Select value={value} onValueChange={(nextValue) => onChange(nextValue as T)}>
        <SelectTrigger className="w-full">
          <span className="truncate">{selectedLabel}</span>
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            {options.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
    </div>
  )
}
