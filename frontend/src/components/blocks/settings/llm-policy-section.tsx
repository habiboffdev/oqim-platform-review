import { useMemo } from 'react'
import { Brain, Sparkle, WarningCircle } from '@phosphor-icons/react'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Badge } from '@/components/primitives/badge'
import { useLlmPolicies, useUpdateLlmPolicies } from '@/hooks/use-llm-policies'
import { uz } from '@/lib/uz'

export function LlmPolicySection() {
  const { data, isLoading, isError, refetch } = useLlmPolicies()
  const updatePolicies = useUpdateLlmPolicies()

  const modelById = useMemo(
    () => new Map((data?.models ?? []).map((model) => [model.id, model])),
    [data?.models],
  )

  function handleModelChange(taskKey: string, value: string) {
    const nextModel = value === '__default__' ? null : value
    updatePolicies.mutate({
      overrides: {
        [taskKey]: nextModel,
      },
    })
  }

  return (
    <section className="rounded-xl border border-border bg-card p-5">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <Brain size={18} weight="thin" className="text-muted-foreground" />
          <div>
            <h2 className="text-sm font-medium">{uz.settings.llmPolicy}</h2>
            <p className="text-xs text-muted-foreground">{uz.settings.llmPolicyDesc}</p>
          </div>
        </div>
        <Badge variant="success">
          <Sparkle size={12} weight="fill" className="mr-1" />
          {uz.settings.llmPolicyCentralized}
        </Badge>
      </div>

      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, idx) => (
            <div key={idx} className="h-16 animate-pulse rounded-2xl bg-muted" />
          ))}
        </div>
      ) : isError || !data ? (
        <div className="space-y-3">
          <p className="flex items-center gap-2 text-sm text-destructive">
            <WarningCircle size={16} weight="thin" />
            {uz.common.error}
          </p>
          <Button variant="outline" size="sm" onClick={() => refetch()}>
            {uz.common.retry}
          </Button>
        </div>
      ) : (
        <div className="space-y-3">
          {data.tasks.map((task) => {
            const currentModel = modelById.get(task.effective_model)
            const isOverride = Boolean(task.override_model)
            return (
              <div
                key={task.key}
                className="grid gap-3 rounded-2xl border border-border bg-background/60 p-3 sm:grid-cols-[1fr_230px]"
              >
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-sm font-medium">{task.label}</p>
                    <Badge variant={task.lane === 'quality' ? 'success' : 'default'}>
                      {task.lane}
                    </Badge>
                    {isOverride && <Badge variant="warning">{uz.settings.llmOverride}</Badge>}
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">{task.description}</p>
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    {uz.settings.llmCurrentModel}: {currentModel?.label ?? task.effective_model}
                  </p>
                </div>

                <Select
                  value={task.override_model ?? '__default__'}
                  onValueChange={(value) => handleModelChange(task.key, value ?? '__default__')}
                  disabled={!task.allow_override || updatePolicies.isPending}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__default__">
                      {uz.settings.llmUseDefault(modelById.get(task.default_model)?.label ?? task.default_model)}
                    </SelectItem>
                    {data.models.map((model) => (
                      <SelectItem key={model.id} value={model.id}>
                        {model.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}
