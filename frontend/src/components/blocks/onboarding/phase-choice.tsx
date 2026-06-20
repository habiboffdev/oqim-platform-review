import { SignIn, Sparkle } from '@phosphor-icons/react'
import { uz } from '@/lib/uz'

export function ChoiceStep({ onExisting, onNew }: { onExisting: () => void; onNew: () => void }) {
  return (
    <>
      <div className="mt-8 text-xs font-medium text-muted-foreground">
        Avval Telegram
      </div>
      <h1 className="mt-2 text-3xl font-semibold leading-tight tracking-tight">
        {uz.onboarding.chooseTitle}
      </h1>
      <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
        {uz.onboarding.chooseSubtitle}
      </p>

      <div className="mt-8 grid gap-2">
        <button
          type="button"
          onClick={onExisting}
          className="rounded-md border border-border/70 bg-background/40 px-3 py-3 text-left transition-colors hover:bg-foreground/[0.04]"
        >
          <div className="flex items-start gap-4">
            <div className="flex size-9 items-center justify-center rounded-md border border-border/70 bg-background text-foreground">
              <SignIn size={20} weight="thin" />
            </div>
            <div>
              <p className="text-base font-medium text-foreground">{uz.auth.existingAccount}</p>
              <p className="mt-1 text-sm text-muted-foreground">{uz.auth.existingAccountDesc}</p>
            </div>
          </div>
        </button>

        <button
          type="button"
          onClick={onNew}
          className="rounded-md border border-border/70 bg-background/40 px-3 py-3 text-left transition-colors hover:bg-foreground/[0.04]"
        >
          <div className="flex items-start gap-4">
            <div className="flex size-9 items-center justify-center rounded-md border border-border/70 bg-background text-foreground">
              <Sparkle size={20} weight="thin" />
            </div>
            <div>
              <p className="text-base font-medium text-foreground">{uz.auth.newAccount}</p>
              <p className="mt-1 text-sm text-muted-foreground">{uz.auth.newAccountDesc}</p>
            </div>
          </div>
        </button>
      </div>
    </>
  )
}
