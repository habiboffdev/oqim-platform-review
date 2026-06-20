import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { uz } from '@/lib/uz'
import { CheckIcon } from '@/components/icons/doc-icons'
import { BrainIcon, ChatIcon, RobotIcon } from '@/components/icons/nav-icons'
import { useOnboardingDocuments } from '@/hooks/use-onboarding-documents'
import type { OnboardingDocumentBlock } from '@/lib/types'
import type { DefaultAgentKey, PermissionModeKey } from './types'

function readyOverTotal(block: OnboardingDocumentBlock | undefined) {
  return uz.onboarding.launch.learnedReady(
    (block?.approved ?? 0) + (block?.proposed ?? 0),
    block?.total ?? 0,
  )
}

interface PhaseLaunchProps {
  enabled?: boolean
  permissionMode: PermissionModeKey
  enabledDefaultAgents: DefaultAgentKey[]
  isSubmitting: boolean
  onLaunch: (mode: 'start' | 'later') => void
}

const ArrowRightIcon = ({ className }: { className?: string }) => (
  <svg
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={1.5}
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden="true"
    focusable="false"
  >
    <path d="M5 12h14M13 6l6 6-6 6" />
  </svg>
)

const DEFAULT_AGENTS: { key: DefaultAgentKey; label: string }[] = [
  { key: 'seller', label: uz.onboarding.launch.agentSeller },
  { key: 'support', label: uz.onboarding.launch.agentSupport },
  { key: 'catalog_update', label: uz.onboarding.launch.agentCatalogUpdate },
  { key: 'follow_up', label: uz.onboarding.launch.agentFollowUp },
  { key: 'bi', label: uz.onboarding.launch.agentBi },
]

const PERMISSION_LABELS: Record<PermissionModeKey, string> = {
  ask_always: uz.onboarding.launch.permissionAskAlways,
  auto_approve: uz.onboarding.launch.permissionAutoApprove,
  full_access: uz.onboarding.launch.permissionFullAccess,
}

const FIRST_WORK = [
  uz.onboarding.launch.firstWorkOne,
  uz.onboarding.launch.firstWorkTwo,
  uz.onboarding.launch.firstWorkThree,
]

export function PhaseLaunch({
  enabled = true,
  permissionMode,
  enabledDefaultAgents,
  isSubmitting,
  onLaunch,
}: PhaseLaunchProps) {
  const { data: projection } = useOnboardingDocuments(enabled)
  const business = projection?.documents.business
  const agent = projection?.documents.agent
  const skill = projection?.documents.skill

  const learned = [
    {
      key: 'business',
      icon: BrainIcon,
      label: uz.onboarding.launch.learnedBusiness,
      value: readyOverTotal(business),
    },
    {
      key: 'agent',
      icon: RobotIcon,
      label: uz.onboarding.launch.learnedAgent,
      value: readyOverTotal(agent),
    },
    {
      key: 'skill',
      icon: ChatIcon,
      label: uz.onboarding.launch.learnedSkill,
      value: uz.onboarding.launch.learnedCount(skill?.candidates ?? 0),
    },
  ]

  const permissionLabel = PERMISSION_LABELS[permissionMode] ?? uz.onboarding.launch.permissionAskAlways

  return (
    <section className="mx-auto flex w-full max-w-2xl flex-col gap-5 py-2">
      <header className="grid gap-1.5">
        <h1 className="font-sans text-2xl font-semibold tracking-tight text-foreground">
          {uz.onboarding.launch.title}
        </h1>
        <p className="text-sm text-muted-foreground">{uz.onboarding.launch.subtitle}</p>
      </header>

      {/* Block 1 — learned counts */}
      <div className="rounded-lg border border-border bg-card px-5 py-4">
        <p className="text-sm font-semibold text-foreground">{uz.onboarding.launch.learnedTitle}</p>
        <div className="mt-3 grid gap-2 sm:grid-cols-3">
          {learned.map((item) => (
            <div
              key={item.key}
              className="flex items-center gap-3 rounded-md border border-border/70 bg-background px-3 py-2.5"
            >
              <span className="grid size-8 shrink-0 place-items-center rounded-md bg-muted text-foreground [&_svg]:size-4">
                <item.icon />
              </span>
              <span className="grid min-w-0 gap-0.5">
                <span className="truncate text-xs text-muted-foreground">{item.label}</span>
                <span className="text-sm font-medium text-foreground">{item.value}</span>
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Block 2 — agents + permission */}
      <div className="rounded-lg border border-border bg-card px-5 py-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <p className="text-sm font-semibold text-foreground">{uz.onboarding.launch.agentsTitle}</p>
          <p className="text-xs text-muted-foreground">
            {uz.onboarding.launch.permissionLabel}: <span className="text-foreground">{permissionLabel}</span>
          </p>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">{uz.onboarding.launch.agentsSubtitle}</p>
        <ul className="mt-3 grid gap-2">
          {DEFAULT_AGENTS.map((item) => {
            const active = enabledDefaultAgents.includes(item.key)
            return (
              <li
                key={item.key}
                className="flex items-center justify-between gap-3 rounded-md border border-border/70 bg-background px-3 py-2.5"
              >
                <span className="flex min-w-0 items-center gap-2.5">
                  <span className="grid size-7 shrink-0 place-items-center rounded-md bg-muted text-foreground [&_svg]:size-4">
                    <RobotIcon />
                  </span>
                  <span className="truncate text-sm font-medium text-foreground">{item.label}</span>
                </span>
                <Badge
                  variant={active ? 'secondary' : 'outline'}
                  className={cn('gap-1', active && 'text-foreground')}
                >
                  <CheckIcon className="size-3" />
                  {uz.onboarding.launch.agentBadgeReady}
                </Badge>
              </li>
            )
          })}
        </ul>
      </div>

      {/* Block 3 — first work */}
      <div className="rounded-lg border border-border bg-card px-5 py-4">
        <p className="text-sm font-semibold text-foreground">{uz.onboarding.launch.firstWorkTitle}</p>
        <ul className="mt-3 grid gap-2">
          {FIRST_WORK.map((line) => (
            <li key={line} className="flex items-start gap-2.5 text-sm text-foreground">
              <span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full bg-muted text-foreground [&_svg]:size-3">
                <CheckIcon />
              </span>
              <span className="text-muted-foreground">{line}</span>
            </li>
          ))}
        </ul>
      </div>

      {/* Actions */}
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:items-center sm:justify-end">
        <Button
          type="button"
          variant="ghost"
          disabled={isSubmitting}
          onClick={() => onLaunch('later')}
        >
          {uz.onboarding.launch.later}
        </Button>
        <Button
          type="button"
          loading={isSubmitting}
          disabled={isSubmitting}
          onClick={() => onLaunch('start')}
        >
          {uz.onboarding.launch.start}
          <ArrowRightIcon />
        </Button>
      </div>
    </section>
  )
}
