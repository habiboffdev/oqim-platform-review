import type { ReactNode } from 'react'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'
import { ONBOARDING_STAGES } from './constants'
import { onboardingStageCopy } from './copy'
import type { Phase } from './types'

export function OnboardingFrame({
  phase,
  children,
}: {
  phase: Phase
  children: ReactNode
}) {
  const copy = onboardingStageCopy(phase)

  return (
    <main
      data-onboarding-scroll-root="true"
      className="min-h-[100dvh] overflow-x-hidden bg-background px-4 py-4 text-foreground sm:px-5 lg:px-6"
    >
      <div className="mx-auto flex min-h-[calc(100dvh-2rem)] w-full max-w-[1680px] flex-col">
        <header className="flex shrink-0 items-center justify-between gap-4 border-b border-border/80 pb-4">
          <div className="flex min-w-0 items-center gap-5">
            <div className="text-xl font-semibold tracking-tight">OQIM</div>
            <Separator orientation="vertical" className="hidden h-6 sm:block" />
            <p className="truncate text-sm text-muted-foreground">
              {copy.crumb} <span className="text-foreground">· {copy.section}</span>
            </p>
          </div>
          <Stepper step={copy.stage} />
        </header>
        <div className="flex min-h-0 flex-1 items-stretch py-4">
          {children}
        </div>
      </div>
    </main>
  )
}

export function OnboardingWorkbench({
  phase: _phase,
  learnedPanel,
  rightRail,
  statusBar,
  children,
}: {
  phase: Phase
  learnedPanel: ReactNode
  rightRail?: ReactNode
  statusBar?: ReactNode
  children: ReactNode
}) {
  return (
    <section
      className={cn(
        'flex min-h-0 w-full flex-col gap-3 lg:h-[calc(100dvh-7.25rem)]',
      )}
    >
      {statusBar ? (
        <div>
          {statusBar}
        </div>
      ) : null}
      <div
        className={cn(
          'grid min-h-0 flex-1 w-full gap-3',
          rightRail
            ? 'xl:grid-cols-[minmax(320px,420px)_minmax(0,1fr)_minmax(260px,320px)]'
            : 'lg:grid-cols-[minmax(340px,440px)_minmax(0,1fr)]',
        )}
      >
        <div className="min-h-0 min-w-0 lg:h-full">{children}</div>
        <div className="min-h-0 min-w-0 lg:h-full">{learnedPanel}</div>
        {rightRail ? <div className="min-h-0 min-w-0 xl:h-full">{rightRail}</div> : null}
      </div>
    </section>
  )
}

function Stepper({ step }: { step: number }) {
  return (
    <div className="flex items-center gap-2 text-xs text-muted-foreground">
      <div className="ml-2 flex items-center gap-1.5">
        {Array.from({ length: ONBOARDING_STAGES }, (_, index) => index + 1).map((item) => (
          <span
            key={item}
            className={cn(
              'flex size-7 items-center justify-center rounded-full border text-xs font-medium transition-colors',
              item === step
                ? 'border-foreground bg-foreground text-background'
                : item < step
                  ? 'border-border bg-muted text-foreground'
                  : 'border-border bg-background text-muted-foreground',
            )}
          >
            {item}
          </span>
        ))}
      </div>
    </div>
  )
}
