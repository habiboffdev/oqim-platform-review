import type { ReactNode } from 'react'
import {
  Brain,
  Check,
  Database,
  ChatCircle,
  Path,
  ShieldCheck,
} from '@phosphor-icons/react'

interface AuthLayoutProps {
  children: ReactNode
  variant?: 'welcome' | 'onboarding'
}

export function AuthLayout({ children, variant = 'welcome' }: AuthLayoutProps) {
  const isOnboarding = variant === 'onboarding'
  const rows = isOnboarding
    ? [
        { icon: ChatCircle, label: 'Telegram ulanadi', detail: 'Mijoz xabarlari va javoblar bitta ish joyida ko‘rinadi.' },
        { icon: Brain, label: 'Bilim yig‘iladi', detail: 'Katalog, bilim bazasi, qoidalar va sotuvchi ovozi yig‘iladi.' },
        { icon: Database, label: 'Mijozlar tartiblanadi', detail: 'Bosqichlar, buyurtmalar, vazifalar va qayta yozish navbati paydo bo‘ladi.' },
        { icon: ShieldCheck, label: 'Avtopilot sozlanadi', detail: 'AI faqat ishonch va ruxsat chegarasidan keyin o‘zi yuboradi.' },
      ]
    : [
        { icon: ChatCircle, label: 'Ish joyi tayyor', detail: 'Sotuvchi suhbatlari, javoblar va mijozlar saqlangan holatdan ochiladi.' },
        { icon: Path, label: 'AI boshqariladi', detail: 'Javob sifati, cheklovlar va yumshoq to‘xtashlar nazoratda.' },
        { icon: ShieldCheck, label: 'Ruxsat birinchi', detail: 'Noaniq yoki xavfli ishlar sotuvchi tasdig‘idan o‘tadi.' },
      ]

  return (
    <div className="relative h-svh overflow-hidden bg-background text-foreground 2xl:flex 2xl:h-auto 2xl:min-h-svh 2xl:items-center 2xl:justify-center 2xl:overflow-visible 2xl:p-6">
      <div className="relative mx-auto flex h-full w-full max-w-[1600px] 2xl:aspect-[16/9] 2xl:h-auto 2xl:min-h-0 2xl:w-[min(94vw,calc(92svh*16/9))] 2xl:max-w-none 2xl:overflow-hidden 2xl:rounded-2xl 2xl:border 2xl:border-border/70 2xl:bg-background/95 2xl:shadow-[0_25px_80px_-24px_rgba(0,0,0,0.75)]">
        <div className="relative hidden flex-1 overflow-hidden border-r border-border/60 lg:block">
          <div
            aria-hidden
            className="absolute inset-0 opacity-70"
            style={{
              backgroundImage:
                'radial-gradient(circle, currentColor 1px, transparent 1.8px)',
              backgroundSize: '9px 9px',
              maskImage: 'radial-gradient(ellipse at 50% 48%, black 0 28%, transparent 68%)',
            }}
          />
          <div
            aria-hidden
            className="pointer-events-none absolute inset-0"
            style={{
              background:
                'radial-gradient(900px 600px at 50% 50%, transparent 45%, color-mix(in srgb, var(--background) 88%, transparent) 92%)',
            }}
          />
          <div className="pointer-events-none absolute inset-0 flex flex-col justify-between p-12">
            <div className="pointer-events-auto flex items-center gap-2 font-mono text-sm">
              <span className="inline-block h-2 w-2 rounded-full bg-foreground" />
              <span className="tracking-[0.2em] uppercase">OQIM Business</span>
            </div>
            <div className="max-w-md">
              <div className="font-mono text-[11px] uppercase tracking-[0.3em] text-muted-foreground">
                {isOnboarding ? 'Birinchi sozlash' : 'Kirish'}
              </div>
              <p className="mt-3 font-heading text-xl leading-snug md:text-2xl">
                {isOnboarding
                  ? 'OQIM birinchi sessiyadan biznesingizni tushuna boshlaydi.'
                  : 'Sotuvchi ish joyi suhbatlar va AI yordam bilan qayta ochiladi.'}
              </p>
              <div className="mt-6 grid gap-2">
                {rows.map((row) => (
                  <div
                    key={row.label}
                    className="grid grid-cols-[34px_1fr] gap-3 rounded-lg border border-border/60 bg-background/40 px-3 py-3"
                  >
                    <span className="flex size-8 items-center justify-center rounded-md border border-border/60 bg-background">
                      <row.icon className="size-4 text-muted-foreground" weight="thin" />
                    </span>
                    <div className="min-w-0">
                      <p className="text-sm font-medium">{row.label}</p>
                      <p className="mt-1 text-xs leading-5 text-muted-foreground">{row.detail}</p>
                    </div>
                  </div>
                ))}
              </div>
              <div className="mt-4 flex items-center justify-between rounded-md border border-border/60 bg-background/40 px-3 py-2 text-sm">
                <span className="text-muted-foreground">Promptlar</span>
                <span className="inline-flex items-center gap-1 font-medium">
                  <Check className="size-3.5" weight="thin" />
                  nazoratda
                </span>
              </div>
            </div>
          </div>
        </div>
        <div
          data-onboarding-scroll-root={isOnboarding ? 'true' : undefined}
          className={`relative flex w-full flex-col items-center overflow-y-auto px-6 py-10 lg:w-[620px] lg:px-14 ${
            isOnboarding ? 'justify-start' : 'justify-center'
          }`}
        >
          {children}
        </div>
      </div>
    </div>
  )
}
