import { useState, type ChangeEvent, type FormEvent } from 'react'
import { Link, useNavigate } from '@tanstack/react-router'
import { Eye, EyeSlash } from '@phosphor-icons/react'
import { useAuth } from '@/lib/auth-context'
import { ApiError } from '@/lib/api-client'
import { uz } from '@/lib/uz'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Separator } from '@/components/ui/separator'
import { AuthLayout } from '@/components/blocks/landing/auth-layout'

export function LoginPage() {
  const [phone, setPhone] = useState('')
  const [password, setPassword] = useState('')
  const [reveal, setReveal] = useState(false)
  const [error, setError] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const { login } = useAuth()
  const navigate = useNavigate()

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError('')
    setIsSubmitting(true)
    try {
      await login(phone, password)
      navigate({ to: '/' })
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.status === 401 ? uz.auth.wrongCredentials : uz.auth.networkError)
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
        <div className="font-mono text-[11px] uppercase tracking-[0.3em] text-muted-foreground">
          Sotuvchi ish joyi
        </div>
        <h1 className="mt-2 font-heading text-3xl leading-tight">
          {uz.auth.welcome}
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Saqlangan ish joyingizdan davom eting.
        </p>

        <form onSubmit={handleSubmit} className="mt-8 flex flex-col gap-4">
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

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
                placeholder="••••••••"
                autoComplete="current-password"
                className="pr-11"
                required
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
          </div>

          <Button type="submit" size="lg" disabled={isSubmitting} className="mt-2 w-full">
            {isSubmitting ? uz.auth.signingIn : uz.auth.signIn}
          </Button>
        </form>

        <div className="my-6 flex items-center gap-3">
          <Separator className="flex-1" />
          <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-muted-foreground">
            yoki
          </span>
          <Separator className="flex-1" />
        </div>

        <div className="flex flex-col gap-2">
          <Link to="/onboarding">
            <Button variant="outline" size="lg" type="button" className="w-full">
              {uz.auth.newAccount}
            </Button>
          </Link>
        </div>
      </div>
    </AuthLayout>
  )
}
