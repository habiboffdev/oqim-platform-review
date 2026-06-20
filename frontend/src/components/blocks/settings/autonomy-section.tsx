import { useState } from 'react'
import { Robot, FloppyDisk } from '@phosphor-icons/react'
import { Button } from '@/components/ui/button'
import { uz } from '@/lib/uz'
import { useAgents, useUpdateAgent } from '@/hooks/use-agents'
import { cn } from '@/lib/utils'

const THRESHOLD_MARKS = [
  { value: 0, label: uz.autonomy.levels.off },
  { value: 50, label: uz.autonomy.levels.cautious },
  { value: 75, label: uz.autonomy.levels.moderate },
  { value: 90, label: uz.autonomy.levels.confident },
]

interface AutonomySliderProps {
  label: string
  description: string
  value: number
  min: number
  max: number
  step: number
  unit: string
  onChange: (v: number) => void
  marks?: { value: number; label: string }[]
}

function AutonomySlider({ label, description, value, min, max, step, unit, onChange, marks }: AutonomySliderProps) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <label className="text-xs font-medium text-foreground">{label}</label>
        <span className="font-mono text-xs text-muted-foreground">
          {value}{unit}
        </span>
      </div>
      <p className="text-xs text-muted-foreground">{description}</p>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className={cn(
          'h-1.5 w-full cursor-pointer appearance-none rounded-full bg-muted',
          '[&::-webkit-slider-thumb]:size-4 [&::-webkit-slider-thumb]:appearance-none',
          '[&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-foreground',
          '[&::-webkit-slider-thumb]:shadow-sm [&::-webkit-slider-thumb]:transition-transform',
          '[&::-webkit-slider-thumb]:hover:scale-110',
          '[&::-moz-range-thumb]:size-4 [&::-moz-range-thumb]:rounded-full',
          '[&::-moz-range-thumb]:border-0 [&::-moz-range-thumb]:bg-foreground',
        )}
      />
      {marks && (
        <div className="flex justify-between">
          {marks.map((m) => (
            <span
              key={m.value}
              className={cn(
                'text-[10px] transition-colors',
                value >= m.value ? 'text-foreground/70' : 'text-muted-foreground',
              )}
            >
              {m.label}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

export function AutonomySection() {
  const { data: agents, isLoading } = useAgents()
  const updateAgent = useUpdateAgent()

  const customerAgent = agents?.find((a) => a.agent_type === 'customer') ?? agents?.[0] ?? null

  const [threshold, setThreshold] = useState<number | null>(null)

  const currentThresholdPercent = Math.round((customerAgent?.auto_send_threshold ?? 0) * 100)
  const effectiveThreshold = threshold ?? currentThresholdPercent

  const hasChanges = threshold !== null && threshold !== currentThresholdPercent

  async function handleSave() {
    if (!customerAgent) return
    await updateAgent.mutateAsync({
      id: customerAgent.id,
      auto_send_threshold: effectiveThreshold / 100,
      trust_mode: effectiveThreshold > 0 ? 'autopilot' : 'disabled',
    })
    setThreshold(null)
  }

  return (
    <section className="rounded-xl border border-border bg-card p-5">
      <div className="mb-4 flex items-center gap-2.5">
        <Robot size={18} weight="thin" className="text-muted-foreground" />
        <h2 className="text-sm font-medium">{uz.autonomy.title}</h2>
      </div>

      {isLoading ? (
        <div className="space-y-3">
          <div className="h-4 animate-pulse rounded bg-muted" />
          <div className="h-2 animate-pulse rounded bg-muted" />
          <div className="h-4 animate-pulse rounded bg-muted" />
          <div className="h-2 animate-pulse rounded bg-muted" />
        </div>
      ) : (
        <div className="space-y-5">
          <AutonomySlider
            label={uz.autonomy.threshold}
            description={uz.autonomy.thresholdDesc(effectiveThreshold)}
            value={effectiveThreshold}
            min={0}
            max={100}
            step={5}
            unit="%"
            onChange={(v) => setThreshold(v)}
            marks={THRESHOLD_MARKS}
          />

          <div className="flex items-center justify-between">
            <p className="text-xs text-muted-foreground">
              {uz.autonomy.weeklyStats(0)}
            </p>
            {hasChanges && (
              <Button
                size="sm"
                onClick={handleSave}
                disabled={updateAgent.isPending || !customerAgent}
              >
                <FloppyDisk size={14} weight="thin" />
                {updateAgent.isPending ? uz.settings.saving : uz.autonomy.save}
              </Button>
            )}
          </div>
        </div>
      )}
    </section>
  )
}
