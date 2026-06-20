import { type ChangeEvent, useState } from "react"
import { motion } from "framer-motion"
import { useNavigate } from "@tanstack/react-router"

import { cn } from "@/lib/utils"
import { uz } from "@/lib/uz"
import { fadeIn, slideUp } from "@/lib/motion"
import {
  useDraftCustomAgent,
  useCreateCustomAgent,
  type WizardSectionDraft,
  type DraftCustomAgentResponse,
  type WizardAgentKind,
} from "@/hooks/use-agent-workbench"
import { WIZARD_KIND_ICONS, type WizardKind } from "@/components/blocks/agents/wizard-kind-icons"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { Spinner } from "@/components/ui/spinner"

type Step = "kind" | "behavior" | "review"
const STEP_ORDER: Step[] = ["kind", "behavior", "review"]

// role/when/never are the LLM-drafted behavior the owner should read; the rest are
// deterministic config (B0 critique P1 — group, don't wall).
const BEHAVIOR_KEYS = ["role", "when_to_act", "never_guess"]

const CheckIcon = ({ className }: { className?: string }) => (
  <svg
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={2.25}
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden="true"
    focusable="false"
  >
    <path d="m5 12.5 4.5 4.5L19 7.5" />
  </svg>
)

interface KindOption {
  kind: WizardKind
  title: string
  summary: string
}

const KIND_OPTIONS: KindOption[] = [
  { kind: "seller", title: "Sotuvchi", summary: "Mahsulot va narx savollariga javob beradi, sotuvni oldinga suradi." },
  { kind: "support", title: "Qo'llab-quvvatlash", summary: "Mijoz muammosini hal qiladi, kerak bo'lsa egaga uzatadi." },
  { kind: "follow_up", title: "Kuzatuv", summary: "Mijozni keyingi qadamga qaytaradi." },
  { kind: "custom", title: "Boshqa", summary: "Vazifani o'zingiz yozasiz." },
]

function StepRail({ step }: { step: Step }) {
  const current = STEP_ORDER.indexOf(step)
  const labels = [
    uz.agents.create.stepKindLabel,
    uz.agents.create.stepBehaviorLabel,
    uz.agents.create.stepReviewLabel,
  ]
  return (
    <nav className="flex flex-col gap-0.5">
      {labels.map((label, index) => {
        const state = index < current ? "done" : index === current ? "active" : "todo"
        return (
          <div key={label} className="flex items-center gap-3 py-1.5">
            <span
              className={cn(
                "flex size-6 shrink-0 items-center justify-center rounded-full border text-xs font-medium transition-colors",
                state === "active" && "border-foreground bg-foreground text-background",
                state === "done" && "border-foreground/30 bg-background text-foreground",
                state === "todo" && "border-border bg-background text-muted-foreground",
              )}
            >
              {state === "done" ? <CheckIcon className="size-3" /> : index + 1}
            </span>
            <span
              className={cn(
                "text-sm transition-colors",
                state === "active" ? "font-medium text-foreground" : "text-muted-foreground",
              )}
            >
              {label}
            </span>
          </div>
        )
      })}
    </nav>
  )
}

function StepHeading({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      <h2 className="text-lg font-medium tracking-tight text-foreground">{title}</h2>
      {hint ? <p className="text-sm leading-6 text-muted-foreground">{hint}</p> : null}
    </div>
  )
}

function SectionEditor({
  section,
  onChange,
}: {
  section: WizardSectionDraft
  onChange: (key: string, body: string) => void
}) {
  return (
    <div className="grid gap-2">
      <Label className="text-[0.8125rem] font-medium text-foreground" htmlFor={`sec-${section.section_key}`}>
        {section.title}
      </Label>
      <Textarea
        id={`sec-${section.section_key}`}
        className="min-h-20 resize-none text-sm leading-6"
        value={section.body}
        onChange={(event) => onChange(section.section_key, event.currentTarget.value)}
      />
    </div>
  )
}

export function AgentsNewPage() {
  const navigate = useNavigate()
  const draftMutation = useDraftCustomAgent()
  const createMutation = useCreateCustomAgent()

  const [step, setStep] = useState<Step>("kind")
  const [kind, setKind] = useState<WizardKind>("seller")
  const [name, setName] = useState("")
  const [doesWhat, setDoesWhat] = useState("")
  const [whenReplies, setWhenReplies] = useState("")
  const [neverDoes, setNeverDoes] = useState("")
  const [draft, setDraft] = useState<DraftCustomAgentResponse | null>(null)
  const [sections, setSections] = useState<WizardSectionDraft[]>([])
  const [permissionMode, setPermissionMode] = useState<"ask_always" | "auto_approve">("ask_always")

  // doesWhat >= 8 mirrors the backend (does_what/mission min_length) so Next never
  // produces a draft whose role body is too short to satisfy create's mission.
  const canBehaviorNext = name.trim().length >= 2 && doesWhat.trim().length >= 8

  const behaviorSections = sections.filter((s) => BEHAVIOR_KEYS.includes(s.section_key))
  const configSections = sections.filter((s) => !BEHAVIOR_KEYS.includes(s.section_key))

  async function runDraft() {
    const result = await draftMutation.mutateAsync({
      agent_kind: kind as WizardAgentKind,
      name: name.trim(),
      does_what: doesWhat.trim(),
      when_replies: whenReplies.trim() || undefined,
      never_does: neverDoes.trim() || undefined,
    })
    setDraft(result)
    setSections(result.sections)
    setPermissionMode(result.permission_mode === "ask_always" ? "ask_always" : "auto_approve")
    setStep("review")
  }

  async function runCreate() {
    if (!draft) return
    const roleSection = sections.find((s) => s.section_key === "role")
    const result = await createMutation.mutateAsync({
      name: name.trim(),
      agent_kind: kind as WizardAgentKind,
      mission: (roleSection?.body || doesWhat).slice(0, 2000),
      permission_mode: permissionMode,
      brain_scopes: draft.brain_scopes,
      tool_scopes: draft.tool_scopes,
      trigger_sources: draft.trigger_sources,
      sections,
    })
    await navigate({ to: "/agents/$agentId", params: { agentId: String(result.agent.id) } })
  }

  function updateSection(key: string, body: string) {
    setSections((prev) => prev.map((s) => (s.section_key === key ? { ...s, body } : s)))
  }

  return (
    <div className="h-full min-h-0 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-3xl flex-col px-6 py-10 pb-24">
        <header className="mb-10 flex items-center justify-between">
          <h1 className="text-sm font-medium tracking-tight text-foreground">{uz.agents.create.title}</h1>
          <Button variant="ghost" size="sm" onClick={() => void navigate({ to: "/agents" })}>
            {uz.agents.create.close}
          </Button>
        </header>

        <div className="grid grid-cols-[148px_1fr] gap-12">
          <div className="sticky top-10 self-start">
            <StepRail step={step} />
          </div>

          <motion.section {...fadeIn} className="min-w-0">
            {step === "kind" && (
              <div className="flex flex-col gap-6">
                <StepHeading title={uz.agents.create.stepKindTitle} hint={uz.agents.create.stepKindHint} />
                <div className="flex flex-col gap-2.5">
                  {KIND_OPTIONS.map((option) => {
                    const Icon = WIZARD_KIND_ICONS[option.kind]
                    const selected = kind === option.kind
                    return (
                      <button
                        key={option.kind}
                        type="button"
                        onClick={() => setKind(option.kind)}
                        aria-pressed={selected}
                        className={cn(
                          "flex items-center gap-4 rounded-xl border p-4 text-left transition-colors",
                          selected
                            ? "border-foreground bg-muted/50"
                            : "border-border hover:border-foreground/40 hover:bg-muted/30",
                        )}
                      >
                        <span
                          className={cn(
                            "flex size-10 shrink-0 items-center justify-center rounded-lg border transition-colors",
                            selected
                              ? "border-foreground bg-foreground text-background"
                              : "border-border bg-background text-foreground",
                          )}
                        >
                          <Icon className="size-5" />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block text-sm font-medium text-foreground">{option.title}</span>
                          <span className="mt-0.5 block text-[0.8125rem] leading-5 text-muted-foreground">
                            {option.summary}
                          </span>
                        </span>
                        <span
                          className={cn(
                            "flex size-5 shrink-0 items-center justify-center rounded-full border transition-colors",
                            selected ? "border-foreground bg-foreground text-background" : "border-border",
                          )}
                        >
                          {selected ? <CheckIcon className="size-3" /> : null}
                        </span>
                      </button>
                    )
                  })}
                </div>
                <div className="flex justify-end pt-2">
                  <Button onClick={() => setStep("behavior")}>{uz.agents.create.next}</Button>
                </div>
              </div>
            )}

            {step === "behavior" && (
              <div className="flex flex-col gap-6">
                <StepHeading title={uz.agents.create.stepBehaviorTitle} />
                <div className="flex flex-col gap-5">
                  <div className="grid gap-2">
                    <Label className="text-[0.8125rem] font-medium text-foreground" htmlFor="agent-name">
                      {uz.agents.create.nameLabel}
                    </Label>
                    <Input
                      id="agent-name"
                      value={name}
                      onChange={(event: ChangeEvent<HTMLInputElement>) => setName(event.currentTarget.value)}
                      placeholder={uz.agents.create.namePlaceholder}
                    />
                  </div>
                  <div className="grid gap-2">
                    <Label className="text-[0.8125rem] font-medium text-foreground" htmlFor="does-what">
                      {uz.agents.create.doesWhatLabel}
                    </Label>
                    <Textarea
                      id="does-what"
                      className="min-h-20 resize-none text-sm leading-6"
                      value={doesWhat}
                      onChange={(event) => setDoesWhat(event.currentTarget.value)}
                      placeholder={uz.agents.create.doesWhatPlaceholder}
                    />
                  </div>
                  <div className="grid gap-2">
                    <Label className="text-[0.8125rem] font-medium text-foreground" htmlFor="when-replies">
                      {uz.agents.create.whenRepliesLabel}
                    </Label>
                    <Textarea
                      id="when-replies"
                      className="min-h-16 resize-none text-sm leading-6"
                      value={whenReplies}
                      onChange={(event) => setWhenReplies(event.currentTarget.value)}
                      placeholder={uz.agents.create.whenRepliesPlaceholder}
                    />
                  </div>
                  <div className="grid gap-2">
                    <Label className="text-[0.8125rem] font-medium text-foreground" htmlFor="never-does">
                      {uz.agents.create.neverDoesLabel}
                    </Label>
                    <Textarea
                      id="never-does"
                      className="min-h-16 resize-none text-sm leading-6"
                      value={neverDoes}
                      onChange={(event) => setNeverDoes(event.currentTarget.value)}
                      placeholder={uz.agents.create.neverDoesPlaceholder}
                    />
                  </div>
                </div>
                {draftMutation.isError ? (
                  <p className="text-sm text-destructive">{uz.agents.create.draftError}</p>
                ) : null}
                <div className="flex items-center justify-between pt-2">
                  <Button variant="ghost" onClick={() => setStep("kind")}>{uz.agents.create.back}</Button>
                  <Button
                    onClick={() => void runDraft()}
                    disabled={!canBehaviorNext || draftMutation.isPending}
                    loading={draftMutation.isPending}
                  >
                    {draftMutation.isPending ? uz.agents.create.drafting : uz.agents.create.next}
                  </Button>
                </div>
              </div>
            )}

            {step === "review" && draft ? (
              <motion.div {...slideUp} className="flex flex-col gap-8">
                <div className="flex items-baseline justify-between border-b border-border/60 pb-4">
                  <StepHeading title={uz.agents.create.stepReviewTitle} />
                  <span className="text-xs text-muted-foreground">
                    {uz.agents.create.agentMdHeading} · {uz.agents.create.agentMdEditable}
                  </span>
                </div>

                {/* Behavior — prominent, fields stand on their own (no nested card). */}
                <div className="flex flex-col gap-4">
                  <h3 className="text-[0.7rem] font-medium uppercase tracking-[0.08em] text-muted-foreground">
                    {uz.agents.create.behaviorGroupHeading}
                  </h3>
                  <div className="flex flex-col gap-5">
                    {behaviorSections.map((section) => (
                      <SectionEditor key={section.section_key} section={section} onChange={updateSection} />
                    ))}
                  </div>
                </div>

                {/* Config — deterministic, secondary via a soft tint (no nested border). */}
                {configSections.length > 0 ? (
                  <div className="flex flex-col gap-4">
                    <h3 className="text-[0.7rem] font-medium uppercase tracking-[0.08em] text-muted-foreground">
                      {uz.agents.create.configGroupHeading}
                    </h3>
                    <div className="flex flex-col gap-5 rounded-xl bg-muted/40 p-5">
                      {configSections.map((section) => (
                        <SectionEditor key={section.section_key} section={section} onChange={updateSection} />
                      ))}
                    </div>
                  </div>
                ) : null}

                <div className="flex flex-col gap-3 rounded-xl border border-border p-5">
                  <h3 className="text-[0.7rem] font-medium uppercase tracking-[0.08em] text-muted-foreground">
                    {uz.agents.create.autoSettingsHeading}
                  </h3>
                  <dl className="grid gap-3 text-sm">
                    <div className="flex items-baseline justify-between gap-4">
                      <dt className="text-muted-foreground">{uz.agents.create.permissionsRow}</dt>
                      <dd className="text-right font-medium text-foreground">
                        {draft.tool_scopes.join(", ") || "—"}
                      </dd>
                    </div>
                    <div className="flex items-center justify-between gap-4">
                      <dt className="text-muted-foreground">{uz.agents.create.trustRow}</dt>
                      <dd className="flex gap-1.5">
                        <button
                          type="button"
                          onClick={() => setPermissionMode("ask_always")}
                          className={cn(
                            "rounded-md border px-2.5 py-1 text-xs transition-colors",
                            permissionMode === "ask_always"
                              ? "border-foreground bg-foreground text-background"
                              : "border-border text-muted-foreground hover:text-foreground",
                          )}
                        >
                          {uz.agents.create.trustDraft}
                        </button>
                        <button
                          type="button"
                          onClick={() => setPermissionMode("auto_approve")}
                          className={cn(
                            "rounded-md border px-2.5 py-1 text-xs transition-colors",
                            permissionMode === "auto_approve"
                              ? "border-foreground bg-foreground text-background"
                              : "border-border text-muted-foreground hover:text-foreground",
                          )}
                        >
                          {uz.agents.create.trustAutopilot}
                        </button>
                      </dd>
                    </div>
                    <div className="flex items-baseline justify-between gap-4">
                      <dt className="text-muted-foreground">{uz.agents.create.triggerRow}</dt>
                      <dd className="text-right font-medium text-foreground">
                        {draft.trigger_sources.length
                          ? uz.agents.create.triggerOnMessage
                          : uz.agents.create.triggerManual}
                      </dd>
                    </div>
                  </dl>
                </div>

                {createMutation.isError ? (
                  <p className="text-sm text-destructive">{uz.agents.create.createError}</p>
                ) : null}
                <div className="flex items-center justify-between border-t border-border/60 pt-6">
                  <Button variant="ghost" onClick={() => setStep("behavior")}>{uz.agents.create.back}</Button>
                  <Button
                    size="lg"
                    onClick={() => void runCreate()}
                    disabled={createMutation.isPending}
                    loading={createMutation.isPending}
                  >
                    {uz.agents.create.submit}
                  </Button>
                </div>
              </motion.div>
            ) : null}
          </motion.section>
        </div>
      </div>

      {draftMutation.isPending ? (
        <div className="pointer-events-none fixed inset-0 z-50 flex items-center justify-center bg-background/70 backdrop-blur-[1px]">
          <div className="flex items-center gap-3 rounded-xl border border-border bg-background px-5 py-3.5 text-sm text-foreground shadow-sm">
            <Spinner className="size-4" />
            {uz.agents.create.drafting}
          </div>
        </div>
      ) : null}
    </div>
  )
}
