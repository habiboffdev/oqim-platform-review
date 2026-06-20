import { useState, useRef, useCallback, useEffect, type ChangeEvent, type KeyboardEvent } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Phone,
  ShieldCheck,
  LockSimple,
  ArrowLeft,
  Check,
  QrCode,
} from '@phosphor-icons/react'
import { fadeIn, slideUp, scalePulse } from '@/lib/motion'
import { uz } from '@/lib/uz'
import { ApiError, api } from '@/lib/api-client'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/primitives/spinner'

type AuthStep = 'phone' | 'code' | '2fa' | 'qr' | 'qr2fa' | 'success'
type RecoveryState = 'idle' | 'waiting' | 'switching'
type AuthTransport = 'web' | 'tcp'

const QR_REFRESH_SAFETY_WINDOW_MS = 8_000

interface TelegramUser {
  userId: string
  phone: string
  firstName: string
  lastName: string
  authMethod?: 'phone' | 'qr'
}

interface PhoneAuthProps {
  onSuccess: (user: TelegramUser) => void | Promise<void>
}

interface SendCodeDelivery {
  type?: string | null
  nextType?: string | null
  timeoutSeconds?: number | null
  length?: number | null
  preferredType?: string | null
  degraded?: boolean | null
  degradedReason?: string | null
  authTransport?: AuthTransport | null
  authClientProfile?: string | null
}

interface SendCodeResponse {
  phoneCodeHash?: string
  tempSessionId?: string
  error?: string
  delivery?: SendCodeDelivery
}

interface QrCodePayload {
  svg?: string
  tgUrl?: string
  expiresAt?: number
  expired?: boolean
}

interface QrStatusPayload {
  status?: string
  user?: TelegramUser
  error?: {
    code?: string
    message?: string
    retryable?: boolean
  } | null
  running?: boolean
}

function extractApiErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    const data = error.data as { detail?: string; error?: string } | undefined
    const serverMessage = data?.detail || data?.error
    if (typeof serverMessage === 'string' && serverMessage) {
      const normalized = serverMessage.toUpperCase()
      if (normalized.includes('SIDECAR UNREACHABLE')) return uz.connect.serviceDown
      return authResultErrorMessage(serverMessage)
    }
  }
  if (error instanceof Error && error.message) {
    if (error.message.toUpperCase().includes('SIDECAR UNREACHABLE')) return uz.connect.serviceDown
    return error.message
  }
  return fallback
}

function formatDeliveryHint(delivery?: SendCodeDelivery | null): string {
  if (!delivery?.type) return uz.connect.codeHintGeneric

  if (delivery.degraded && delivery.preferredType === 'auth.SentCodeTypeApp') {
    return uz.connect.codeHintAppDegraded
  }

  const normalizedType = delivery.type.toLowerCase()
  const timeout = delivery.timeoutSeconds
  const nextText = delivery.nextType ? ` ${uz.connect.codeNextRetryHint}` : ''
  const timeoutText = timeout ? ` ${uz.connect.codeTimeoutPrefix} ${timeout} ${uz.connect.codeTimeoutSuffix}.${nextText}` : nextText

  switch (normalizedType) {
    case 'auth.sentcodetypeapp':
      return `${uz.connect.codeHintApp}${timeoutText}`
    case 'auth.sentcodetypesms':
      return `${uz.connect.codeHintSms}${timeoutText}`
    case 'auth.sentcodetypecall':
      return `${uz.connect.codeHintCall}${timeoutText}`
    case 'auth.sentcodetypeflashcall':
      return `${uz.connect.codeHintFlashCall}${timeoutText}`
    case 'auth.sentcodetypemissedcall':
      return `${uz.connect.codeHintMissedCall}${timeoutText}`
    default:
      return uz.connect.codeHintGeneric
  }
}

function isQrStep(step: AuthStep): step is 'qr' | 'qr2fa' {
  return step === 'qr' || step === 'qr2fa'
}

function qrStatusErrorMessage(status: QrStatusPayload): string {
  switch (status.error?.code) {
    case 'AUTH_TOKEN_EXPIRED':
      return uz.connect.qrExpired
    case 'PASSWORD_HASH_INVALID':
      return uz.connect.wrongPassword
    case '2FA_TIMEOUT':
      return uz.connect.twoFaTimeout
    default:
      return status.error?.message || uz.connect.qrFailed
  }
}

function authResultErrorMessage(error?: string): string {
  const normalized = (error || '').toUpperCase()
  switch (error) {
    case 'PASSWORD_HASH_INVALID':
      return uz.connect.wrongPassword
    case 'SESSION_PASSWORD_NEEDED':
    case '2FA_REQUIRED':
      return uz.connect.twoFaPrompt
    case 'PHONE_NUMBER_INVALID':
      return uz.connect.phoneInvalid
    case 'PHONE_NUMBER_BANNED':
      return uz.connect.phoneBanned
    case 'PHONE_CODE_SEND_FAILED':
      return uz.connect.phoneCodeSendFailed
    case 'DEVICE_CODE_UNAVAILABLE':
      return uz.connect.deviceCodeUnavailable
    case 'RATE_LIMITED':
    case 'Rate limited':
      return uz.connect.rateLimited
    default:
      if (
        normalized.includes('REQUEST WAS UNSUCCESSFUL')
        || normalized.includes('TIMEOUT')
        || normalized.includes('CONNECT_TIMEOUT')
      ) {
        return uz.connect.phoneCodeSendFailed
      }
      return error || uz.connect.serviceDown
  }
}

export function PhoneAuth({ onSuccess }: PhoneAuthProps) {
  const [step, setStep] = useState<AuthStep>('phone')
  const [phone, setPhone] = useState('+998')
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [phoneCodeHash, setPhoneCodeHash] = useState('')
  const [tempSessionId, setTempSessionId] = useState('')
  const [delivery, setDelivery] = useState<SendCodeDelivery | null>(null)
  const [recoveryState, setRecoveryState] = useState<RecoveryState>('idle')
  const [deliveryHint, setDeliveryHint] = useState('')
  const [resendAvailableAt, setResendAvailableAt] = useState<number | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const [qrSvg, setQrSvg] = useState('')
  const [qrTgUrl, setQrTgUrl] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const codeInputRef = useRef<HTMLInputElement>(null)
  const passwordInputRef = useRef<HTMLInputElement>(null)
  const authCompletedRef = useRef(false)
  const finishAuthRef = useRef<((user: TelegramUser, authMethod: 'phone' | 'qr') => Promise<void>) | null>(null)

  const finishAuth = useCallback(async (user: TelegramUser, authMethod: 'phone' | 'qr') => {
    if (authCompletedRef.current) return
    authCompletedRef.current = true
    setLoading(true)
    setError('')
    try {
      await onSuccess({ ...user, authMethod })
      setStep('success')
    } catch {
      authCompletedRef.current = false
      setStep('phone')
      setError(uz.connect.serviceDown)
    } finally {
      setLoading(false)
    }
  }, [onSuccess])

  useEffect(() => {
    finishAuthRef.current = finishAuth
  }, [finishAuth])

  const requestCode = useCallback(async (options: { authTransport?: AuthTransport } = {}) => {
    if (phone.length < 9) return
    authCompletedRef.current = false
    setLoading(true)
    setError('')
    setDeliveryHint('')
    setDelivery(null)
    setRecoveryState(options.authTransport ? 'switching' : 'idle')
    setResendAvailableAt(null)
    try {
      const payload: { phone: string; tempSessionId?: string; authTransport?: AuthTransport } = { phone }
      if (options.authTransport) {
        payload.authTransport = options.authTransport
        if (tempSessionId) payload.tempSessionId = tempSessionId
      }
      const res = await api.post<SendCodeResponse>('/api/telegram/auth/send-code', payload)
      if (res.error) {
        setError(authResultErrorMessage(res.error))
      } else if (res.phoneCodeHash) {
        const sentAt = Date.now()
        setPhoneCodeHash(res.phoneCodeHash)
        setTempSessionId(res.tempSessionId || '')
        setDelivery(res.delivery || null)
        setRecoveryState(res.delivery?.nextType ? 'waiting' : 'idle')
        setCode('')
        setDeliveryHint(formatDeliveryHint(res.delivery))
        setNow(sentAt)
        setResendAvailableAt(
          res.delivery?.timeoutSeconds
            ? sentAt + res.delivery.timeoutSeconds * 1000
            : null,
        )
        setStep('code')
        setTimeout(() => codeInputRef.current?.focus(), 100)
      }
    } catch (err) {
      setError(extractApiErrorMessage(err, uz.connect.serviceDown))
    } finally {
      setLoading(false)
    }
  }, [phone, tempSessionId])

  const handleSendCode = useCallback(() => requestCode(), [requestCode])

  const handleTryAlternateCodeRoute = useCallback(
    () => requestCode({ authTransport: 'tcp' }),
    [requestCode],
  )

  const handleResendCode = useCallback(async () => {
    if (phone.length < 9) return
    if (resendAvailableAt && Date.now() < resendAvailableAt) return
    authCompletedRef.current = false
    setLoading(true)
    setRecoveryState('switching')
    setError('')
    try {
      const res = await api.post<SendCodeResponse>('/api/telegram/auth/resend-code', {
        phone,
        phoneCodeHash,
        tempSessionId,
      })
      if (res.error) {
        setError(authResultErrorMessage(res.error))
      } else if (res.phoneCodeHash) {
        const sentAt = Date.now()
        setPhoneCodeHash(res.phoneCodeHash)
        setTempSessionId(res.tempSessionId || tempSessionId)
        setDelivery(res.delivery || null)
        setRecoveryState(res.delivery?.nextType ? 'waiting' : 'idle')
        setCode('')
        setDeliveryHint(formatDeliveryHint(res.delivery))
        setNow(sentAt)
        setResendAvailableAt(
          res.delivery?.timeoutSeconds
            ? sentAt + res.delivery.timeoutSeconds * 1000
            : null,
        )
      }
    } catch (err) {
      setRecoveryState('idle')
      setError(extractApiErrorMessage(err, uz.connect.serviceDown))
    } finally {
      setLoading(false)
    }
  }, [phone, phoneCodeHash, resendAvailableAt, tempSessionId])

  useEffect(() => {
    if (step !== 'code' || !resendAvailableAt) return
    const intervalId = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(intervalId)
  }, [resendAvailableAt, step])

  const resendSecondsLeft = resendAvailableAt
    ? Math.max(0, Math.ceil((resendAvailableAt - now) / 1000))
    : 0
  const hasNextDeliveryRoute = Boolean(delivery?.nextType)
  const isAppDeliveryWithoutFallback = delivery?.type?.toLowerCase() === 'auth.sentcodetypeapp'
    && !hasNextDeliveryRoute
  const canTryAlternateCodeRoute = isAppDeliveryWithoutFallback && delivery?.authTransport !== 'tcp'
  const canResendCode = hasNextDeliveryRoute && !loading && resendSecondsLeft === 0

  const handleVerifyCode = useCallback(async () => {
    if (code.length < 3) return
    setLoading(true)
    setError('')
    try {
      const res = await api.post<{ user?: TelegramUser; error?: string }>(
        '/api/telegram/auth/sign-in',
        { phone, phoneCodeHash, code, tempSessionId },
      )
      if (res.error === '2FA_REQUIRED') {
        setStep('2fa')
        setTimeout(() => passwordInputRef.current?.focus(), 100)
      } else if (res.user) {
        await finishAuth(res.user, 'phone')
      } else if (res.error) {
        setError(authResultErrorMessage(res.error))
      }
    } catch (err) {
      setError(extractApiErrorMessage(err, uz.connect.invalidCode))
    } finally {
      setLoading(false)
    }
  }, [code, finishAuth, phone, phoneCodeHash, tempSessionId])

  const handleCheckPassword = useCallback(async () => {
    if (!password) return
    setLoading(true)
    setError('')
    let keepWaitingForQrAuth = false
    try {
      const endpoint = step === 'qr2fa' ? '/api/telegram/auth/qr/check-2fa' : '/api/telegram/auth/check-2fa'
      const res = await api.post<{ user?: TelegramUser; error?: string }>(endpoint, {
        password,
        ...(step === 'qr2fa' ? {} : { tempSessionId }),
      })
      if (step === 'qr2fa') {
        setPassword('')
        keepWaitingForQrAuth = true
      } else if (res.user) {
        await finishAuth(res.user, 'phone')
      } else if (res.error) {
        setError(authResultErrorMessage(res.error))
      }
    } catch (err) {
      setError(extractApiErrorMessage(err, uz.connect.wrongPassword))
    } finally {
      if (!keepWaitingForQrAuth) {
        setLoading(false)
      }
    }
  }, [finishAuth, password, step, tempSessionId])

  const handleBackToPhone = useCallback(() => {
    authCompletedRef.current = false
    setStep('phone')
    setCode('')
    setPassword('')
    setPhoneCodeHash('')
    setTempSessionId('')
    setDelivery(null)
    setRecoveryState('idle')
    setDeliveryHint('')
    setQrSvg('')
    setQrTgUrl('')
    setError('')
  }, [])

  const handleStartQr = useCallback(() => {
    authCompletedRef.current = false
    setStep('qr')
    setPassword('')
    setCode('')
    setDelivery(null)
    setRecoveryState('idle')
    setError('')
    setQrSvg('')
    setQrTgUrl('')
  }, [])

  useEffect(() => {
    if (!isQrStep(step)) return

    let cancelled = false
    let intervalId: number | undefined
    let failedPolls = 0

    const stopQrPolling = () => {
      if (intervalId) {
        window.clearInterval(intervalId)
        intervalId = undefined
      }
      setLoading(false)
      setQrSvg('')
      setQrTgUrl('')
    }

    const syncQr = async (): Promise<boolean> => {
      if (step !== 'qr') return true
      try {
        await api.post<{ status?: string; error?: string }>('/api/telegram/auth/qr/start')
        return true
      } catch (err) {
        const message = extractApiErrorMessage(err, uz.connect.serviceDown)
        if (!cancelled) {
          setError(message)
          stopQrPolling()
        }
        return false
      }
    }

    const refreshExpiredQr = async () => {
      setLoading(false)
      setQrSvg('')
      setQrTgUrl('')
      setError(uz.connect.qrExpired)
      try {
        await api.post<{ status?: string; error?: string }>('/api/telegram/auth/qr/start')
      } catch (err) {
        if (!cancelled) setError(extractApiErrorMessage(err, uz.connect.serviceDown))
      }
    }

    const pollQr = async () => {
      try {
        const status = await api.get<QrStatusPayload>('/api/telegram/auth/qr/status')
        if (cancelled) return

        failedPolls = 0

        if (status.status === 'success' && status.user) {
          await finishAuthRef.current?.(status.user, 'qr')
          return
        }

        if (status.status === '2fa_required') {
          setLoading(false)
          setStep('qr2fa')
          if (status.error?.code === 'PASSWORD_HASH_INVALID') {
            setError(uz.connect.wrongPassword)
          }
          setTimeout(() => passwordInputRef.current?.focus(), 100)
          return
        }

        if (status.status === 'expired') {
          await refreshExpiredQr()
          return
        }

        if (status.status === 'failed') {
          setLoading(false)
          setQrSvg('')
          setQrTgUrl('')
          setError(qrStatusErrorMessage(status))
          if (step === 'qr2fa') {
            setPassword('')
            setStep('qr')
          }
          return
        }

        if (step === 'qr2fa') return

        try {
          const codeData = await api.get<QrCodePayload>('/api/telegram/auth/qr/code')
          if (cancelled || !codeData.svg) return
          const expiresSoon = Boolean(
            codeData.expiresAt && codeData.expiresAt - Date.now() < QR_REFRESH_SAFETY_WINDOW_MS,
          )
          if (codeData.expired || expiresSoon) {
            await refreshExpiredQr()
            return
          }
          setQrSvg(codeData.svg)
          setQrTgUrl(codeData.tgUrl || '')
          setError('')
        } catch {
          // QR can take a moment to appear after startup.
        }
      } catch (err) {
        if (!cancelled) {
          failedPolls += 1
          setError(extractApiErrorMessage(err, uz.connect.serviceDown))
          if (failedPolls >= 3) {
            stopQrPolling()
          }
        }
      }
    }

    void syncQr().then((started) => {
      if (cancelled || !started) return
      void pollQr()
      intervalId = window.setInterval(() => {
        void pollQr()
      }, 2000)
    })

    return () => {
      cancelled = true
      if (intervalId) window.clearInterval(intervalId)
    }
  }, [step])

  return (
    <motion.div
      {...fadeIn}
      className="flex w-full max-w-[400px] flex-col items-center gap-6"
    >
      <div className="flex flex-col items-center gap-2">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">
          OQIM
        </h1>
        <p className="text-sm text-muted-foreground">
          {uz.auth.brandHeadline}
        </p>
      </div>

      <AnimatePresence mode="wait">
        {step === 'phone' && (
          <motion.div
            key="phone"
            {...slideUp}
            className="flex w-full flex-col gap-4"
          >
            <div className="flex items-center gap-3 self-center rounded-full bg-muted/50 p-3">
              <Phone size={24} weight="thin" className="text-foreground" />
            </div>
            <div className="flex flex-col gap-1 text-center">
              <h2 className="text-lg font-medium text-foreground">
                {uz.connect.phoneTitle}
              </h2>
              <p className="text-sm text-muted-foreground">
                {uz.connect.phoneSubtitle}
              </p>
            </div>
            <Input
              type="tel"
              value={phone}
              onChange={(e: ChangeEvent<HTMLInputElement>) => setPhone(e.target.value)}
              placeholder={uz.auth.phonePlaceholder}
              className="text-center text-lg"
              onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => e.key === 'Enter' && handleSendCode()}
            />
            {error && (
              <p className="text-center text-sm text-destructive">{error}</p>
            )}
            <Button
              size="lg"
              onClick={handleSendCode}
              disabled={loading || phone.length < 9}
            >
              {loading ? <Spinner size="sm" /> : uz.connect.sendCode}
            </Button>
            <button
              type="button"
              onClick={handleStartQr}
              className="flex items-center justify-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
            >
              <QrCode size={16} weight="thin" />
              {uz.connect.useQr}
            </button>
          </motion.div>
        )}

        {step === 'code' && (
          <motion.div
            key="code"
            {...slideUp}
            className="flex w-full flex-col gap-4"
          >
            <div className="flex items-center gap-3 self-center rounded-full bg-muted/50 p-3">
              <ShieldCheck size={24} weight="thin" className="text-foreground" />
            </div>
            <div className="flex flex-col gap-1 text-center">
              <h2 className="text-lg font-medium text-foreground">
                {uz.connect.codeTitle}
              </h2>
              <p className="text-sm text-muted-foreground">{phone}</p>
              {deliveryHint && (
                <p className="text-sm text-muted-foreground">{deliveryHint}</p>
              )}
              {recoveryState !== 'idle' && (
                <p className="text-xs font-medium text-foreground">
                  {recoveryState === 'switching'
                    ? uz.connect.codeRecoverySwitching
                    : uz.connect.codeRecoveryWaiting}
                </p>
              )}
            </div>
            {isAppDeliveryWithoutFallback && (
              <div className="rounded-xl border border-border/70 bg-muted/30 px-4 py-3 text-left text-xs leading-5 text-muted-foreground">
                <p className="font-medium text-foreground">{uz.connect.codeAppNoFallbackTitle}</p>
                <ul className="mt-2 list-disc space-y-1 pl-4">
                  <li>{uz.connect.codeAppNoFallback1}</li>
                  <li>{uz.connect.codeAppNoFallback2}</li>
                  <li>{uz.connect.codeAppNoFallback3}</li>
                </ul>
                <a
                  href="https://web.telegram.org/k/"
                  target="_blank"
                  rel="noreferrer"
                  className="mt-3 inline-flex text-foreground underline underline-offset-4"
                >
                  {uz.connect.openTelegramWeb}
                </a>
                {canTryAlternateCodeRoute && (
                  <button
                    type="button"
                    onClick={handleTryAlternateCodeRoute}
                    disabled={loading}
                    className="mt-3 flex w-full items-center justify-center rounded-lg border border-border bg-background px-3 py-2 text-sm font-medium text-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {loading ? <Spinner size="sm" /> : uz.connect.tryAlternateCodeRoute}
                  </button>
                )}
                {canTryAlternateCodeRoute && (
                  <p className="mt-2 text-[11px] leading-4 text-muted-foreground">
                    {uz.connect.tryAlternateCodeRouteHint}
                  </p>
                )}
              </div>
            )}
            <Input
              ref={codeInputRef}
              type="text"
              inputMode="numeric"
              value={code}
              onChange={(e: ChangeEvent<HTMLInputElement>) => setCode(e.target.value.replace(/\D/g, '').slice(0, 5))}
              placeholder={uz.connect.codePlaceholder}
              className="text-center text-2xl tracking-[0.3em]"
              autoComplete="one-time-code"
              onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => e.key === 'Enter' && handleVerifyCode()}
            />
            {error && (
              <p className="text-center text-sm text-destructive">{error}</p>
            )}
            <Button
              size="lg"
              onClick={handleVerifyCode}
              disabled={loading || code.length < 3}
            >
              {loading ? <Spinner size="sm" /> : uz.connect.verify}
            </Button>
            {hasNextDeliveryRoute && (
              <button
                type="button"
                onClick={handleResendCode}
                disabled={!canResendCode}
                className="flex items-center justify-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
              >
                <ArrowLeft size={14} weight="thin" />
                {resendSecondsLeft > 0
                  ? `${uz.connect.resendAfter} ${resendSecondsLeft}s`
                  : uz.connect.codeRecoveryReady}
              </button>
            )}
            <button
              type="button"
              onClick={handleStartQr}
              className="flex items-center justify-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
            >
              <QrCode size={16} weight="thin" />
              {uz.connect.useQr}
            </button>
          </motion.div>
        )}

        {step === '2fa' && (
          <motion.div
            key="2fa"
            {...slideUp}
            className="flex w-full flex-col gap-4"
          >
            <div className="flex items-center gap-3 self-center rounded-full bg-muted/50 p-3">
              <LockSimple size={24} weight="thin" className="text-foreground" />
            </div>
            <div className="flex flex-col gap-1 text-center">
              <h2 className="text-lg font-medium text-foreground">
                {uz.connect.twoFaTitle}
              </h2>
              <p className="text-sm text-muted-foreground">
                {uz.connect.twoFaSubtitle}
              </p>
            </div>
            <Input
              ref={passwordInputRef}
              type="password"
              value={password}
              onChange={(e: ChangeEvent<HTMLInputElement>) => setPassword(e.target.value)}
              placeholder={uz.auth.password}
              className="text-center"
              onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => e.key === 'Enter' && handleCheckPassword()}
            />
            {error && (
              <p className="text-center text-sm text-destructive">{error}</p>
            )}
            <Button
              size="lg"
              onClick={handleCheckPassword}
              disabled={loading || !password}
            >
              {loading ? <Spinner size="sm" /> : uz.auth.signIn}
            </Button>
            <button
              type="button"
              onClick={handleBackToPhone}
              className="flex items-center justify-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
            >
              <ArrowLeft size={14} weight="thin" />
              {uz.connect.usePhone}
            </button>
          </motion.div>
        )}

        {isQrStep(step) && (
          <motion.div
            key={step}
            {...slideUp}
            className="flex w-full flex-col gap-4"
          >
            <div className="flex items-center gap-3 self-center rounded-full bg-muted/50 p-3">
              {step === 'qr2fa' ? (
                <LockSimple size={24} weight="thin" className="text-foreground" />
              ) : (
                <QrCode size={24} weight="thin" className="text-foreground" />
              )}
            </div>
            <div className="flex flex-col gap-1 text-center">
              <h2 className="text-lg font-medium text-foreground">
                {step === 'qr2fa' ? uz.connect.twoFaTitle : uz.connect.qrTitle}
              </h2>
              <p className="text-sm text-muted-foreground">
                {step === 'qr2fa' ? uz.connect.twoFaSubtitle : uz.connect.qrSubtitle}
              </p>
            </div>

            {step === 'qr2fa' ? (
              <>
                <Input
                  ref={passwordInputRef}
                  type="password"
                  value={password}
                  onChange={(e: ChangeEvent<HTMLInputElement>) => setPassword(e.target.value)}
                  placeholder={uz.connect.twoFaPrompt}
                  className="text-center"
                  onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => e.key === 'Enter' && handleCheckPassword()}
                />
                {error && (
                  <p className="text-center text-sm text-destructive">{error}</p>
                )}
                {loading && (
                  <p className="text-center text-sm text-muted-foreground">
                    {uz.connect.twoFaChecking}
                  </p>
                )}
                <Button
                  size="lg"
                  onClick={handleCheckPassword}
                  disabled={loading || !password}
                >
                  {loading ? <Spinner size="sm" /> : uz.auth.signIn}
                </Button>
              </>
            ) : (
              <>
                <div className="flex justify-center rounded-3xl border border-border bg-white p-4 shadow-sm">
                  {qrSvg ? (
                    <div
                      className="size-56 [&_svg]:size-full"
                      dangerouslySetInnerHTML={{ __html: qrSvg }}
                    />
                  ) : (
                    <div className="flex size-56 items-center justify-center text-sm text-muted-foreground">
                      {uz.connect.qrLoading}
                    </div>
                  )}
                </div>
                <div className="space-y-1 text-center text-sm text-muted-foreground">
                  <p>{uz.connect.qrStep1} <span className="font-medium text-foreground">{uz.connect.qrStep1Bold}</span> {uz.connect.qrStep1End}</p>
                  <p>{uz.connect.qrStep2}</p>
                  <p>{uz.connect.qrStep3}</p>
                  {qrTgUrl && (
                    <a
                      href={qrTgUrl}
                      className="inline-flex items-center justify-center text-sm text-primary underline-offset-4 hover:underline"
                    >
                      {uz.connect.openTelegram}
                    </a>
                  )}
                </div>
                {error && (
                  <p className="text-center text-sm text-destructive">{error}</p>
                )}
              </>
            )}

            <button
              type="button"
              onClick={handleBackToPhone}
              className="flex items-center justify-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
            >
              <ArrowLeft size={14} weight="thin" />
              {uz.connect.usePhone}
            </button>
          </motion.div>
        )}

        {step === 'success' && (
          <motion.div
            key="success"
            {...scalePulse}
            className="flex flex-col items-center gap-3"
          >
            <div className="flex size-16 items-center justify-center rounded-full bg-primary/10">
              <Check size={32} weight="thin" className="text-primary" />
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  )
}
