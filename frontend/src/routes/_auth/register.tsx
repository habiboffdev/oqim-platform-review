import { useMemo, useState, type ChangeEvent, type FormEvent } from 'react'
import { Link, useNavigate } from '@tanstack/react-router'
import { Check, Eye, EyeSlash } from '@phosphor-icons/react'
import { useAuth } from '@/lib/auth-context'
import { ApiError } from '@/lib/api-client'
import { uz } from '@/lib/uz'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Separator } from '@/components/ui/separator'
import { AuthLayout } from '@/components/blocks/landing/auth-layout'

type Strength = 'empty' | 'weak' | 'ok' | 'strong'

function computeStrength(password: string): Strength {
  if (!password) return 'empty'
  const hasDigit = /\d/.test(password)
  const hasSymbol = /[^\w\s]/.test(password)
  if (password.length >= 12 || (password.length >= 8 && hasDigit && hasSymbol)) return 'strong'
  if (password.length >= 8 && (hasDigit || hasSymbol)) return 'ok'
  return 'weak'
}

const STRENGTH_META: Record<Strength, { label: string; segments: number; color: string; text: string }> = {
  empty: { label: '', segments: 0, color: 'bg-border', text: 'text-muted-foreground' },
  weak: { label: 'Past', segments: 1, color: 'bg-amber-500', text: 'text-amber-600 dark:text-amber-400' },
  ok: { label: 'Yaxshi', segments: 2, color: 'bg-sky-500', text: 'text-sky-600 dark:text-sky-400' },
  strong: { label: 'Kuchli', segments: 3, color: 'bg-emerald-500', text: 'text-emerald-600 dark:text-emerald-400' },
}

export function RegisterPage() {
  const [fullName, setFullName] = useState('')
  const [phone, setPhone] = useState('')
  const [password, setPassword] = useState('')
  const [reveal, setReveal] = useState(false)
  const [error, setError] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const { register } = useAuth()
  const navigate = useNavigate()
  const strength = useMemo(() => computeStrength(password), [password])
  const strengthMeta = STRENGTH_META[strength]

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError('')
    setIsSubmitting(true)
    try {
      await register(phone, password, fullName)
      navigate({ to: '/onboarding' })
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.status === 409 ? uz.auth.accountExists : uz.auth.networkError)
      } else {
        setError(uz.auth.networkError)
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <AuthLayout>
      <div className="w-full max-w-lg">
        <div className="mb-6 flex flex-wrap items-center gap-2">
          {['Avval Telegram', 'Saqlangan sozlama', 'Bitta ish joyi'].map((label) => (
            <span
              key={label}
              className="inline-flex items-center gap-1.5 rounded-full border border-border bg-background/60 px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.2em] text-muted-foreground backdrop-blur-sm"
            >
              <Check size={12} weight="thin" className="text-emerald-500" />
              {label}
            </span>
          ))}
        </div>

        <div className="font-mono text-[11px] uppercase tracking-[0.3em] text-muted-foreground">
          Birinchi ish joyi
        </div>
        <h1 className="mt-2 font-heading text-3xl leading-tight">
          {uz.auth.signUp}
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Ish joyini yarating, keyin Telegramni ulang.
        </p>

        <form onSubmit={handleSubmit} className="mt-8 flex flex-col gap-4">
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="fullName">{uz.auth.fullName}</Label>
            <Input
              id="fullName"
              type="text"
              value={fullName}
              onChange={(e: ChangeEvent<HTMLInputElement>) => setFullName(e.target.value)}
              placeholder={uz.auth.fullNamePlaceholder}
              autoComplete="name"
              required
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="phone">{uz.auth.phone}</Label>
            <Input
              id="phone"
              type="tel"
              value={phone}
              onChange={(e: ChangeEvent<HTMLInputElement>) => setPhone(e.target.value)}
              placeholder={uz.auth.phonePlaceholder}
              autoComplete="tel"
              required
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="password">{uz.auth.password}</Label>
            <div className="relative">
              <Input
                id="password"
                type={reveal ? 'text' : 'password'}
                value={password}
                onChange={(e: ChangeEvent<HTMLInputElement>) => setPassword(e.target.value)}
                placeholder="Kamida 8 ta belgi"
                autoComplete="new-password"
                className="pr-11"
                required
                minLength={6}
              />
              <button
                type="button"
                onClick={() => setReveal((value) => !value)}
                aria-label={reveal ? 'Parolni yashirish' : 'Parolni ko‘rsatish'}
                className="absolute right-3 top-1/2 -translate-y-1/2 rounded p-1 text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
              >
                {reveal ? <EyeSlash size={16} weight="thin" /> : <Eye size={16} weight="thin" />}
              </button>
            </div>
            <div className="mt-1 flex items-center gap-2">
              <div className="flex flex-1 gap-1">
                {[0, 1, 2].map((index) => (
                  <div
                    key={index}
                    className={`h-1.5 flex-1 rounded-full transition-colors ${
                      index < strengthMeta.segments ? strengthMeta.color : 'bg-border'
                    }`}
                  />
                ))}
              </div>
              <span className={`min-w-[3.5rem] text-right font-mono text-[10px] uppercase tracking-[0.2em] ${strengthMeta.text}`}>
                {strengthMeta.label}
              </span>
            </div>
          </div>

          <Button type="submit" size="lg" disabled={isSubmitting} className="mt-2 w-full">
            {isSubmitting ? uz.auth.creatingAccount : uz.auth.signUp}
          </Button>
        </form>

        <div className="my-6 flex items-center gap-3">
          <Separator className="flex-1" />
          <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-muted-foreground">
            yoki
          </span>
          <Separator className="flex-1" />
        </div>

        <Link to="/login">
          <Button variant="outline" size="lg" type="button" className="w-full">
            {uz.auth.hasAccount} {uz.auth.signIn}
          </Button>
        </Link>
      </div>
    </AuthLayout>
  )
}
