import { useMemo, type ChangeEvent, type FormEvent } from 'react'
import { ArrowLeft, ArrowRight, CheckCircle, ShieldCheck, Sparkle, UserCircle } from '@phosphor-icons/react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { Textarea } from '@/components/ui/textarea'
import { uz } from '@/lib/uz'
import { cn } from '@/lib/utils'
import {
  CATEGORY_OPTIONS,
  DEFAULT_AGENT_OPTIONS,
  PERMISSION_MODE_OPTIONS,
} from './constants'
import type { DefaultAgentKey, LaunchStep, PermissionModeKey } from './types'

const LAUNCH_STEPS: { value: LaunchStep; label: string; hint: string }[] = [
  { value: 'agents', label: 'Agentlar', hint: 'Qaysi ishlar boshlanadi' },
  { value: 'permission', label: 'Ruxsat', hint: 'Nimani o‘zi qiladi' },
  { value: 'profile', label: 'Kirish', hint: 'Workspace nomi va parol' },
]

export function CredentialsStep({
  businessName,
  category,
  offerSummary,
  region,
  telegramPhone,
  loginPhone,
  password,
  useTelegramPhone,
  enabledDefaultAgents,
  permissionMode,
  launchStep,
  isSubmitting,
  onBusinessNameChange,
  onCategoryChange,
  onOfferSummaryChange,
  onRegionChange,
  onBack,
  onPhoneChange,
  onPasswordChange,
  onTogglePhoneMode,
  onToggleDefaultAgent,
  onPermissionModeChange,
  onLaunchStepChange,
  onSubmit,
}: {
  businessName: string
  category: string
  offerSummary: string
  region: string
  telegramPhone: string
  loginPhone: string
  password: string
  useTelegramPhone: boolean
  enabledDefaultAgents: DefaultAgentKey[]
  permissionMode: PermissionModeKey
  launchStep: LaunchStep
  isSubmitting: boolean
  onBusinessNameChange: (value: string) => void
  onCategoryChange: (value: string) => void
  onOfferSummaryChange: (value: string) => void
  onRegionChange: (value: string) => void
  onBack: () => void
  onPhoneChange: (value: string) => void
  onPasswordChange: (value: string) => void
  onTogglePhoneMode: (value: boolean) => void
  onToggleDefaultAgent: (value: DefaultAgentKey) => void
  onPermissionModeChange: (value: PermissionModeKey) => void
  onLaunchStepChange: (value: LaunchStep) => void
  onSubmit: () => void
}) {
  const stepIndex = LAUNCH_STEPS.findIndex((step) => step.value === launchStep)
  const selectedAgents = useMemo(
    () => DEFAULT_AGENT_OPTIONS.filter((agent) => enabledDefaultAgents.includes(agent.value)),
    [enabledDefaultAgents],
  )
  const selectedPermission =
    PERMISSION_MODE_OPTIONS.find((option) => option.value === permissionMode)
    ?? PERMISSION_MODE_OPTIONS[0]
  const canFinish = password.trim().length >= 8 && businessName.trim().length > 0 && !isSubmitting
  const canContinue = launchStep !== 'agents' || selectedAgents.length > 0

  const goNext = () => {
    if (launchStep === 'agents') {
      onLaunchStepChange('permission')
      return
    }
    if (launchStep === 'permission') {
      onLaunchStepChange('profile')
      return
    }
    onSubmit()
  }

  const goBack = () => {
    if (launchStep === 'profile') {
      onLaunchStepChange('permission')
      return
    }
    if (launchStep === 'permission') {
      onLaunchStepChange('agents')
      return
    }
    onBack()
  }

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault()
    goNext()
  }
  const isProfileStep = launchStep === 'profile'

  return (
    <Card className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg py-0">
      <form onSubmit={handleSubmit} className="flex h-full min-h-0 flex-col">
        <CardHeader className="shrink-0 px-5 py-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <CardTitle className="font-sans text-2xl font-semibold tracking-tight">
                {isProfileStep ? 'Ro‘yxatdan o‘tishni yakunlash' : 'Ishga tushirish'}
              </CardTitle>
              <CardDescription className="mt-1 max-w-[46ch]">
                {isProfileStep
                  ? 'Bu faqat kirish va workspace nomi uchun. Savdo bilimlari oldingi manbalardan olinadi.'
                  : 'Avval kim ishlashini, keyin nimaga ruxsat borligini tanlang. Kirish maʼlumotlari oxirida.'}
              </CardDescription>
            </div>
            <Badge variant="outline">{isProfileStep ? 'Yakunlash' : `${stepIndex + 1} / ${LAUNCH_STEPS.length}`}</Badge>
          </div>
          {!isProfileStep ? (
            <nav className="mt-5 grid gap-1.5 rounded-lg border border-border bg-muted/25 p-1 sm:grid-cols-3" aria-label="Ishga tushirish bosqichlari">
              {LAUNCH_STEPS.map((step, index) => {
                const active = step.value === launchStep
                const done = index < stepIndex
                return (
                  <button
                    key={step.value}
                    type="button"
                    className={cn(
                      'rounded-md px-3 py-2 text-left transition-colors',
                      active
                        ? 'bg-background text-foreground shadow-xs'
                        : done
                          ? 'text-foreground'
                          : 'text-muted-foreground hover:bg-background/70',
                    )}
                    onClick={() => onLaunchStepChange(step.value)}
                  >
                    <span className="flex items-center gap-2 text-xs font-medium">
                      <span className={cn(
                        'grid size-4 place-items-center rounded-full text-[10px]',
                        active ? 'bg-foreground text-background' : done ? 'bg-muted text-foreground' : 'bg-muted text-muted-foreground',
                      )}
                      >
                        {done ? <CheckCircle className="size-3" /> : index + 1}
                      </span>
                      {step.label}
                    </span>
                    <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">
                      {step.hint}
                    </span>
                  </button>
                )
              })}
            </nav>
          ) : null}
        </CardHeader>

        <CardContent className="min-h-0 flex-1 overflow-y-auto px-5 pb-5">
          {launchStep === 'agents' ? (
            <AgentLaunchSection
              enabledDefaultAgents={enabledDefaultAgents}
              selectedAgents={selectedAgents.length}
              onToggleDefaultAgent={onToggleDefaultAgent}
            />
          ) : null}

          {launchStep === 'permission' ? (
            <PermissionLaunchSection
              permissionMode={permissionMode}
              onPermissionModeChange={onPermissionModeChange}
            />
          ) : null}

          {launchStep === 'profile' ? (
            <ProfileLaunchSection
              businessName={businessName}
              category={category}
              offerSummary={offerSummary}
              region={region}
              telegramPhone={telegramPhone}
              loginPhone={loginPhone}
              password={password}
              useTelegramPhone={useTelegramPhone}
              selectedAgents={selectedAgents.length}
              permissionLabel={selectedPermission.label}
              onBusinessNameChange={onBusinessNameChange}
              onCategoryChange={onCategoryChange}
              onOfferSummaryChange={onOfferSummaryChange}
              onRegionChange={onRegionChange}
              onPhoneChange={onPhoneChange}
              onPasswordChange={onPasswordChange}
              onTogglePhoneMode={onTogglePhoneMode}
            />
          ) : null}
        </CardContent>

        <CardFooter className="shrink-0 justify-between border-t px-5 py-4">
          <Button variant="ghost" size="lg" type="button" onClick={goBack}>
            <ArrowLeft size={16} weight="thin" />
            {launchStep === 'agents' ? uz.onboarding.back : 'Oldingi'}
          </Button>
          <Button
            size="lg"
            type="submit"
            disabled={launchStep === 'profile' ? !canFinish : !canContinue}
          >
            {launchStep === 'profile' ? uz.onboarding.finishSetup : uz.onboarding.businessContinue}
            <ArrowRight size={16} weight="thin" />
          </Button>
        </CardFooter>
      </form>
    </Card>
  )
}

function AgentLaunchSection({
  enabledDefaultAgents,
  selectedAgents,
  onToggleDefaultAgent,
}: {
  enabledDefaultAgents: DefaultAgentKey[]
  selectedAgents: number
  onToggleDefaultAgent: (value: DefaultAgentKey) => void
}) {
  const groupedAgents = [
    { title: 'Mijoz bilan ishlash', values: ['seller', 'support'] as DefaultAgentKey[] },
    { title: 'Jarayonlar', values: ['follow_up', 'catalog_update'] as DefaultAgentKey[] },
    { title: 'Boshqaruv', values: ['bi'] as DefaultAgentKey[] },
  ]

  return (
    <section className="grid gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 font-sans text-lg font-semibold tracking-tight">
            <Sparkle className="size-5" />
            Qaysi agentlar kerak?
          </h2>
          <p className="mt-1 max-w-[46ch] text-sm leading-6 text-muted-foreground">
            Bular boshlang‘ich ish rollari. Har bir agent keyin alohida qoida, trigger va ruxsat oladi.
          </p>
        </div>
        <Badge variant={selectedAgents > 0 ? 'success' : 'warning'}>{selectedAgents} ta tanlandi</Badge>
      </div>

      <div className="grid gap-4">
        {groupedAgents.map((group) => (
          <div key={group.title} className="grid gap-2">
            <p className="text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">{group.title}</p>
            <div className="overflow-hidden rounded-lg border border-border">
              {group.values.map((value, index) => {
                const agent = DEFAULT_AGENT_OPTIONS.find((item) => item.value === value)
                if (!agent) return null
                const selected = enabledDefaultAgents.includes(agent.value)
                return (
                  <label
                    key={agent.value}
                    className={cn(
                      'grid cursor-pointer grid-cols-[auto_minmax(0,1fr)] items-start gap-3 px-3 py-3 transition-colors',
                      index > 0 ? 'border-t border-border' : '',
                      selected ? 'bg-muted/35' : 'bg-background hover:bg-muted/25',
                    )}
                  >
                    <Checkbox
                      checked={selected}
                      onCheckedChange={() => onToggleDefaultAgent(agent.value)}
                      aria-label={`${agent.label} agentini yoqish`}
                      className="mt-0.5"
                    />
                    <span className="min-w-0">
                      <span className="flex items-center justify-between gap-3">
                        <span className="font-medium leading-5">{agent.label}</span>
                        {selected ? <span className="size-2 rounded-full bg-emerald-500" /> : null}
                      </span>
                      <span className="mt-1 block text-sm leading-5 text-muted-foreground">{agent.description}</span>
                      <span className="mt-1 block truncate text-xs text-muted-foreground/80">{agent.tools}</span>
                    </span>
                  </label>
                )
              })}
            </div>
          </div>
        ))}
      </div>

      <div className="rounded-lg bg-muted/35 px-3 py-3 text-sm leading-6 text-muted-foreground">
        <span className="font-medium text-foreground">Tavsiya:</span> BI yordamchini qoldiring. U “mijozlarga yoz”, “katalogni yangila”, “kimga follow-up kerak?” kabi buyruqlarni boshqa agentlarga taqsimlaydi.
      </div>
    </section>
  )
}

function PermissionLaunchSection({
  permissionMode,
  onPermissionModeChange,
}: {
  permissionMode: PermissionModeKey
  onPermissionModeChange: (value: PermissionModeKey) => void
}) {
  return (
    <section className="grid gap-5">
      <div>
        <h2 className="flex items-center gap-2 font-sans text-lg font-semibold tracking-tight">
          <ShieldCheck className="size-5" />
          Ruxsat qanday ishlaydi?
        </h2>
        <p className="mt-1 max-w-[58ch] text-sm leading-6 text-muted-foreground">
          OQIM javob, katalog o‘zgarishi va triggerlarni audit bilan bajaradi. Xavfli ishlar promptga emas, policyga bo‘ysunadi.
        </p>
      </div>

      <div className="overflow-hidden rounded-lg border border-border">
        {PERMISSION_MODE_OPTIONS.map((option, index) => {
          const selected = option.value === permissionMode
          return (
            <button
              key={option.value}
              type="button"
              className={cn(
                'grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-4 px-4 py-4 text-left transition-colors',
                index > 0 ? 'border-t border-border' : '',
                selected ? 'bg-foreground text-background' : 'bg-background hover:bg-muted/30',
              )}
              onClick={() => onPermissionModeChange(option.value)}
            >
              <span className="min-w-0">
                <span className="block font-medium">{option.label}</span>
                <span className={cn('mt-1 block text-sm leading-5', selected ? 'text-background/70' : 'text-muted-foreground')}>
                  {option.description}
                </span>
              </span>
              <Badge variant={selected ? 'secondary' : 'outline'}>
                {selected ? 'Tanlandi' : 'Tanlash'}
              </Badge>
            </button>
          )
        })}
      </div>

      <div className="grid gap-2 rounded-lg bg-muted/35 px-4 py-3 text-sm leading-6 text-muted-foreground">
        <p>Past xavf: holat xabari, javob taklifi, qayta eslatish.</p>
        <p>Yuqori xavf: pul, katalogni o‘chirish, yangi integratsiya, ommaviy yuborish.</p>
      </div>
    </section>
  )
}

function ProfileLaunchSection({
  businessName,
  category,
  offerSummary,
  region,
  telegramPhone,
  loginPhone,
  password,
  useTelegramPhone,
  selectedAgents,
  permissionLabel,
  onBusinessNameChange,
  onCategoryChange,
  onOfferSummaryChange,
  onRegionChange,
  onPhoneChange,
  onPasswordChange,
  onTogglePhoneMode,
}: {
  businessName: string
  category: string
  offerSummary: string
  region: string
  telegramPhone: string
  loginPhone: string
  password: string
  useTelegramPhone: boolean
  selectedAgents: number
  permissionLabel: string
  onBusinessNameChange: (value: string) => void
  onCategoryChange: (value: string) => void
  onOfferSummaryChange: (value: string) => void
  onRegionChange: (value: string) => void
  onPhoneChange: (value: string) => void
  onPasswordChange: (value: string) => void
  onTogglePhoneMode: (value: boolean) => void
}) {
  return (
    <section className="grid gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 font-sans text-lg font-semibold tracking-tight">
            <UserCircle className="size-5" />
            Workspace nomi va kirish
          </h2>
          <p className="mt-1 max-w-[58ch] text-sm leading-6 text-muted-foreground">
            Bu savollar ro‘yxatdan o‘tish uchun. Savdo bilimlari va agent qoidalari oldingi bosqichlarda berilgan manbalardan olinadi.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge variant="outline">{selectedAgents} agent</Badge>
          <Badge variant="outline">{permissionLabel}</Badge>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="grid gap-2">
          <Label htmlFor="business-name">{uz.onboarding.businessName}</Label>
          <Input
            id="business-name"
            value={businessName}
            onChange={(event: ChangeEvent<HTMLInputElement>) => onBusinessNameChange(event.target.value)}
            placeholder={uz.onboarding.businessNamePlaceholder}
            autoComplete="organization"
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="business-category">{uz.onboarding.businessCategory}</Label>
          <Select value={category} onValueChange={(value) => value && onCategoryChange(value)}>
            <SelectTrigger id="business-category" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                {CATEGORY_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="grid gap-2 border-y border-border py-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm font-medium">Yakunlanganda OQIM shu fayllarni yaratadi</p>
          <Badge variant="outline">yakunlashda yaratiladi</Badge>
        </div>
        <div className="grid gap-2 sm:grid-cols-3">
          {['BUSINESS.md', 'AGENT.md', 'SKILL.md'].map((fileName) => (
            <div key={fileName} className="flex min-w-0 items-center justify-between gap-2">
              <span className="truncate text-sm">{fileName}</span>
              <span className="text-xs text-muted-foreground">yakunlashdan keyin</span>
            </div>
          ))}
        </div>
      </div>

      <div className="grid gap-2">
        <Label htmlFor="offer-summary">{uz.onboarding.offerSummary}</Label>
        <Textarea
          id="offer-summary"
          value={offerSummary}
          onChange={(event: ChangeEvent<HTMLTextAreaElement>) => onOfferSummaryChange(event.target.value)}
          placeholder={uz.onboarding.offerSummaryPlaceholder}
          className="min-h-20 resize-none"
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="grid gap-2">
          <Label htmlFor="business-region">{uz.onboarding.region}</Label>
          <Input
            id="business-region"
            value={region}
            onChange={(event: ChangeEvent<HTMLInputElement>) => onRegionChange(event.target.value)}
            placeholder={uz.onboarding.regionPlaceholder}
            autoComplete="address-level1"
          />
        </div>

        <div className="grid gap-2">
          <Label htmlFor="login-password">{uz.auth.password}</Label>
          <Input
            id="login-password"
            type="password"
            value={password}
            onChange={(event: ChangeEvent<HTMLInputElement>) => onPasswordChange(event.target.value)}
            placeholder="Kamida 8 ta belgi"
            autoComplete="new-password"
          />
        </div>
      </div>

      <Separator />

      <div className="grid gap-3">
        <Label>Telefon</Label>
        <div className="grid gap-2 sm:grid-cols-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => onTogglePhoneMode(true)}
            className={cn(
              'h-auto min-h-14 w-full justify-start overflow-hidden px-4 py-3 text-left',
              useTelegramPhone ? 'border-foreground bg-muted/45' : '',
            )}
          >
            <span className="min-w-0">
              <span className="block truncate text-sm font-medium">{uz.auth.useTelegramPhone}</span>
              <span className="mt-1 block truncate text-xs text-muted-foreground">{telegramPhone}</span>
            </span>
          </Button>

          <Button
            type="button"
            variant="outline"
            onClick={() => onTogglePhoneMode(false)}
            className={cn(
              'h-auto min-h-14 w-full justify-start overflow-hidden px-4 py-3 text-left',
              !useTelegramPhone ? 'border-foreground bg-muted/45' : '',
            )}
          >
            <span className="min-w-0">
              <span className="block truncate text-sm font-medium">{uz.auth.useAnotherPhone}</span>
              <span className="mt-1 block truncate text-xs text-muted-foreground">{uz.auth.loginPhoneHint}</span>
            </span>
          </Button>
        </div>

        {!useTelegramPhone ? (
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="login-phone">{uz.auth.phone}</Label>
            <Input
              id="login-phone"
              value={loginPhone}
              onChange={(event: ChangeEvent<HTMLInputElement>) => onPhoneChange(event.target.value)}
              placeholder={uz.auth.phonePlaceholder}
              autoComplete="tel"
            />
          </div>
        ) : null}
        <p className="text-xs leading-5 text-muted-foreground">{uz.onboarding.passwordHint}</p>
      </div>
    </section>
  )
}
