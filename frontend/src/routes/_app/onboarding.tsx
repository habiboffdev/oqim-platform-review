import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { toast } from 'sonner'
import { useAuth } from '@/lib/auth-context'
import { api } from '@/lib/api-client'
import type {
  OnboardingFileSource,
} from '@/lib/file-source'
import { uz } from '@/lib/uz'
import { useTelegramConnectionStatus } from '@/hooks/use-telegram-connection-status'
import { useOnboardingRuntime } from '@/hooks/use-onboarding-runtime'
import { useProvisionWorkspaceOS, useWorkspaceOS } from '@/hooks/use-workspace-os'
import { DEFAULT_CATEGORY } from '@/components/blocks/onboarding/constants'
import {
  LearnedOutputPanel,
  type OnboardingActivityEvent,
  type OnboardingActivityStatus,
} from '@/components/blocks/onboarding/learned-panels'
import {
  OnboardingFrame,
  OnboardingWorkbench,
} from '@/components/blocks/onboarding/onboarding-frame'
import { BusinessBasicsStep } from '@/components/blocks/onboarding/phase-basics'
import { ChoiceStep } from '@/components/blocks/onboarding/phase-choice'
import { CredentialsStep } from '@/components/blocks/onboarding/phase-credentials'
import { PhaseDocuments } from '@/components/blocks/onboarding/phase-documents'
import { PhaseLaunch } from '@/components/blocks/onboarding/phase-launch'
import { PreferencesStep } from '@/components/blocks/onboarding/phase-preferences'
import { SourcesStep } from '@/components/blocks/onboarding/phase-sources'
import { TelegramAuthStep } from '@/components/blocks/onboarding/phase-telegram'
import {
  buildOnboardingSourceItems,
  toBusinessBrainSourcePayload,
} from '@/components/blocks/onboarding/source-items'
import { OnboardingStatusBar, WorkspaceOSRail } from '@/components/blocks/onboarding/workspace-os-rail'
import type {
  DefaultAgentKey,
  LearnedReviewActionInput,
  LaunchStep,
  MessageVolumeKey,
  Phase,
  PermissionModeKey,
  ReplyModeKey,
  ReplyTeamKey,
  RevenueBandKey,
  ToneKey,
} from '@/components/blocks/onboarding/types'

export { buildOnboardingSourceItems }

interface BridgeLoginResponse {
  id: number
  name: string
  phone_number: string
  telegram_connected: boolean
  onboarding_completed: boolean
  is_new: boolean
}

export function OnboardingPage() {
  const isTelegramReconnectRoute =
    typeof window !== 'undefined'
    && new URLSearchParams(window.location.search).get('reconnect') === 'telegram'
  const [phase, setPhase] = useState<Phase>(isTelegramReconnectRoute ? 'telegram' : 'choice')
  const [launchStep, setLaunchStep] = useState<LaunchStep>('agents')
  const [businessName, setBusinessName] = useState('')
  const [category, setCategory] = useState<string>(DEFAULT_CATEGORY)
  const [revenueBand, setRevenueBand] = useState<RevenueBandKey>('from_10m_to_50m')
  const [offerSummary, setOfferSummary] = useState('')
  const [region, setRegion] = useState('')
  const [messageVolume, setMessageVolume] = useState<MessageVolumeKey>('10_50')
  const [replyTeamSize, setReplyTeamSize] = useState<ReplyTeamKey>('owner_only')
  const [tone, setTone] = useState<ToneKey>('short_warm')
  const [replyMode, setReplyMode] = useState<ReplyModeKey>('draft')
  const [permissionMode, setPermissionMode] = useState<PermissionModeKey>('ask_always')
  const [enabledDefaultAgents, setEnabledDefaultAgents] = useState<DefaultAgentKey[]>(['seller', 'support', 'follow_up', 'catalog_update', 'bi'])
  const [sourceNotes, setSourceNotes] = useState('')
  const [ownerRules, setOwnerRules] = useState('')
  const [websiteSource, setWebsiteSource] = useState('')
  const [telegramChannelSource, setTelegramChannelSource] = useState('')
  const [telegramStartDate, setTelegramStartDate] = useState('')
  const [telegramEndDate, setTelegramEndDate] = useState('')
  const [fileSource, setFileSource] = useState<OnboardingFileSource | null>(null)
  const [agentWebsiteSource, setAgentWebsiteSource] = useState('')
  const [agentFileSource, setAgentFileSource] = useState<OnboardingFileSource | null>(null)
  const [voiceSource, setVoiceSource] = useState('')
  const [voiceFileSource, setVoiceFileSource] = useState<OnboardingFileSource | null>(null)
  const [telegramPhone, setTelegramPhone] = useState('')
  const [loginPhone, setLoginPhone] = useState('+998')
  const [useTelegramPhone, setUseTelegramPhone] = useState(true)
  const [password, setPassword] = useState('')
  const [isCompleting, setIsCompleting] = useState(false)
  // Set once the launch summary completes onboarding, so the blanket
  // "completed → /brain" redirect below doesn't override handleLaunch's own
  // navigation (start → /conversations).
  const hasLaunchedRef = useRef(false)
  const [ingestionStartFailed, setIngestionStartFailed] = useState(false)
  const [activityEvents, setActivityEvents] = useState<OnboardingActivityEvent[]>([])
  const [isSourceLearning, setIsSourceLearning] = useState(false)
  const [reviewActionPending, setReviewActionPending] = useState<string | null>(null)
  const [showDesktopRail, setShowDesktopRail] = useState(() => (
    typeof window !== 'undefined'
      && typeof window.matchMedia === 'function'
      && window.matchMedia('(min-width: 1280px)').matches
  ))
  const { user, refreshUser } = useAuth()
  const { data: telegramStatus, isLoading: isTelegramStatusLoading } = useTelegramConnectionStatus()
  const onboardingRuntime = useOnboardingRuntime(Boolean(user))
  const workspaceOS = useWorkspaceOS(Boolean(user))
  const provisionWorkspaceOS = useProvisionWorkspaceOS()
  const navigate = useNavigate()
  const runtimeProgress = onboardingRuntime.data?.progress ?? null
  const hasLearnedHistory = Boolean(
    (runtimeProgress?.contacts_found ?? 0) > 0
      || (runtimeProgress?.customers_identified ?? 0) > 0
      || onboardingRuntime.data?.state === 'completed',
  )
  const canContinueWithLinkedHistory = Boolean(
    telegramStatus?.identityLinked && hasLearnedHistory,
  )
  const telegramConnected = Boolean(
    telegramStatus?.state === 'connected'
      || (
        telegramStatus?.state === 'degraded'
        && telegramStatus?.identityLinked
        && !telegramStatus?.needsReconnect
      ),
  )

  const toggleDefaultAgent = useCallback((agent: DefaultAgentKey) => {
    setEnabledDefaultAgents((current) => {
      if (current.includes(agent)) return current.filter((item) => item !== agent)
      return [...current, agent]
    })
  }, [])

  const upsertActivity = useCallback((event: {
    id: string
    title: string
    detail: string
    status: OnboardingActivityStatus
  }) => {
    setActivityEvents((current) => [
      ...current.filter((item) => item.id !== event.id),
      event,
    ].slice(-6))
  }, [])

  useEffect(() => {
    if (hasLaunchedRef.current) return
    if (user?.onboarding_completed && !isTelegramReconnectRoute) {
      void navigate({ to: '/brain', search: { tab: 'sources' }, replace: true })
    }
  }, [isTelegramReconnectRoute, navigate, user?.onboarding_completed])

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    const media = window.matchMedia('(min-width: 1280px)')
    const sync = () => setShowDesktopRail(media.matches)
    sync()
    media.addEventListener?.('change', sync)
    return () => media.removeEventListener?.('change', sync)
  }, [])

  useEffect(() => {
    if (!user) return
    setBusinessName((prev) => prev || user.name || '')
    setCategory((prev) => prev || user.type || DEFAULT_CATEGORY)
    setRevenueBand((prev) => prev || (user.monthly_revenue_band as RevenueBandKey) || 'from_10m_to_50m')
    const phone = user.phone_number || '+998'
    setTelegramPhone((prev) => prev || phone)
    setLoginPhone((prev) => (prev === '+998' || !prev ? phone : prev))
  }, [user])

  const effectivePhase = useMemo<Phase>(() => {
    if (!user) return phase
    if (isTelegramStatusLoading && (phase === 'choice' || phase === 'telegram')) return phase
    if (isTelegramReconnectRoute && (phase === 'choice' || phase === 'telegram')) return 'telegram'
    if (phase === 'choice') return telegramConnected || canContinueWithLinkedHistory ? 'sources' : 'telegram'
    if (phase === 'telegram' && (telegramConnected || canContinueWithLinkedHistory)) return 'sources'
    return phase
  }, [canContinueWithLinkedHistory, isTelegramReconnectRoute, isTelegramStatusLoading, phase, telegramConnected, user])

  useEffect(() => {
    const scrollRoot = document.querySelector<HTMLElement>('[data-onboarding-scroll-root="true"]')
    if (typeof scrollRoot?.scrollTo === 'function') {
      scrollRoot.scrollTo({ top: 0, behavior: 'smooth' })
    }
  }, [effectivePhase])

  const retryIngestion = useCallback(async () => {
    if (!telegramConnected) return
    upsertActivity({
      id: 'telegram-history',
      title: 'Suhbat tarixi o‘qilmoqda',
      detail: 'OQIM oxirgi 50 mijoz oynasini qayta tekshiradi.',
      status: 'running',
    })
    try {
      await api.post('/api/telegram/start-ingestion')
      setIngestionStartFailed(false)
      upsertActivity({
        id: 'telegram-history',
        title: 'Suhbat tarixi qayta boshlandi',
        detail: 'Natijalar tayyor bo‘lganda katalog, ovoz va qoidalarda ko‘rinadi.',
        status: 'done',
      })
      toast.success(uz.onboarding.learningRetryStarted)
      await onboardingRuntime.refetch()
    } catch {
      setIngestionStartFailed(true)
      upsertActivity({
        id: 'telegram-history',
        title: 'Suhbat tarixi to‘xtadi',
        detail: 'Telegram ulanishini tekshiring yoki keyin qayta urining.',
        status: 'failed',
      })
      toast.error(uz.onboarding.learningRetryFailed)
    }
  }, [onboardingRuntime, telegramConnected, upsertActivity])

  const retryLearning = useCallback(async () => {
    const sourceLearning = onboardingRuntime.data?.source_learning
    const hasRetryableSourceLearning = Boolean(
      sourceLearning
        && (
          sourceLearning.sources.some((source) => source.retryable)
          || sourceLearning.status === 'failed'
          || sourceLearning.status === 'retrying'
          || sourceLearning.summary.failed > 0
          || sourceLearning.summary.retrying > 0
        ),
    )
    if (!hasRetryableSourceLearning) {
      await retryIngestion()
      return
    }

    upsertActivity({
      id: 'source-learning-retry',
      title: 'Dalillar qayta o‘qishga qo‘yildi',
      detail: 'OQIM faqat xato yoki tugallanmagan dalil manbalarini qayta tekshiradi.',
      status: 'running',
    })
    setIsSourceLearning(true)
    try {
      await api.post('/api/business-brain/sources/retry', {
        limit: 10,
        max_attempts: 3,
        background: true,
      })
      upsertActivity({
        id: 'source-learning-retry',
        title: 'Dalil o‘qish qayta boshlandi',
        detail: 'Natija shu jarayon satrida katalog, bilim, qoida va dalil sifatida yangilanadi.',
        status: 'done',
      })
      toast.success(uz.onboarding.learningRetryStarted)
      await Promise.allSettled([
        onboardingRuntime.refetch(),
        workspaceOS.refetch(),
      ])
    } catch {
      upsertActivity({
        id: 'source-learning-retry',
        title: 'Dalil o‘qish qayta boshlanmadi',
        detail: 'Saqlangan dalillar joyida qoladi. Keyinroq qayta urinish mumkin.',
        status: 'failed',
      })
      toast.error(uz.onboarding.learningRetryFailed)
    } finally {
      setIsSourceLearning(false)
    }
  }, [onboardingRuntime, retryIngestion, upsertActivity, workspaceOS])

  const applyLearnedReviewAction = useCallback(
    async ({
      action,
      targetType,
      targetRef,
      valuePatch,
      mergeIntoRef,
    }: LearnedReviewActionInput) => {
      const pendingKey = `${action}:${targetType}:${targetRef}`
      setReviewActionPending(pendingKey)
      try {
        await api.post('/api/onboarding/learned-review/actions', {
          action,
          target_type: targetType,
          target_ref: targetRef,
          ...(valuePatch ? { value_patch: valuePatch } : {}),
          ...(mergeIntoRef ? { merge_into_ref: mergeIntoRef } : {}),
        })
        await onboardingRuntime.refetch()
        const successLabel = ({
          approve: 'Tasdiqlandi',
          reject: 'Rad etildi',
          edit: 'Tuzatildi',
          merge: 'Birlashtirildi',
        } as const)[action]
        toast.success(successLabel)
      } catch {
        toast.error('Amal bajarilmadi')
      } finally {
        setReviewActionPending(null)
      }
    },
    [onboardingRuntime],
  )

  const handlePhoneAuthSuccess = useCallback(
    async (telegramUser: { userId: string; phone: string; firstName: string; lastName: string; authMethod?: 'phone' | 'qr' }) => {
      try {
        const authResult = await api.post<BridgeLoginResponse>('/api/auth/bridge-login', {
          userId: telegramUser.userId,
          phone: telegramUser.phone,
          firstName: telegramUser.firstName,
          lastName: telegramUser.lastName,
          authMethod: telegramUser.authMethod ?? 'phone',
        })
        setTelegramPhone(telegramUser.phone)
        setLoginPhone(telegramUser.phone)
        setUseTelegramPhone(true)
        setBusinessName((prev) => prev || `${telegramUser.firstName} ${telegramUser.lastName}`.trim() || telegramUser.firstName)
        await refreshUser()
        if (!authResult.is_new && authResult.onboarding_completed) {
          toast.success(uz.onboarding.existingAccountReconnected)
          navigate({ to: '/conversations' })
          return
        }
        toast.success(authResult.is_new ? uz.onboarding.telegramConnected : uz.onboarding.existingAccountFound)
        setPhase('sources')
      } catch {
        toast.error(uz.connect.serviceDown)
        throw new Error('Telegram bridge login failed')
      }
    },
    [navigate, refreshUser],
  )

  const buildCurrentSourceItems = useCallback(() => buildOnboardingSourceItems({
    sourceNotes,
    websiteSource,
    telegramChannelSource,
    telegramStartDate,
    telegramEndDate,
    fileSource,
    agentWebsiteSource,
    agentFileSource,
    voiceSource,
    voiceFileSource,
  }), [
    agentFileSource,
    agentWebsiteSource,
    fileSource,
    sourceNotes,
    telegramChannelSource,
    telegramEndDate,
    telegramStartDate,
    voiceFileSource,
    voiceSource,
    websiteSource,
  ])
  const draftSourceCount = useMemo(
    () => buildCurrentSourceItems().length,
    [buildCurrentSourceItems],
  )

  const handleSourcesNext = useCallback(() => {
    const sourceItems = buildCurrentSourceItems()
    const hasSourceItems = sourceItems.length > 0
    setPhase('documents')
    setActivityEvents([
      ...(telegramConnected ? [{
        id: 'telegram-history',
        title: 'Suhbat tarixi tayyorlanmoqda',
        detail: 'OQIM faqat oxirgi 50 mijoz oynasidan signal oladi.',
        status: 'running' as const,
      }] : []),
      ...(hasSourceItems ? [{
        id: 'source-registration',
        title: `${sourceItems.length} ta dalil navbatga qo‘yildi`,
        detail: 'Fayl, sayt, kanal yoki qo‘lda yozilgan maʼlumot alohida dalil sifatida saqlanadi.',
        status: 'running' as const,
      }] : [{
        id: 'source-empty',
        title: 'Dalil hali yo‘q',
        detail: 'Agent sozlamalarini yozishingiz mumkin, lekin Brain uchun kamida bitta dalil manbasi kerak bo‘ladi.',
        status: 'queued' as const,
      }]),
    ])

    void (async () => {
      setIsSourceLearning(true)

      const runTelegramHistory = async () => {
        if (!telegramConnected) return
        try {
          await api.post('/api/telegram/start-ingestion')
          setIngestionStartFailed(false)
          upsertActivity({
            id: 'telegram-history',
            title: 'Suhbat tarixi boshlandi',
            detail: 'Kontaktlar emas, foydali savdo signallari va yozish uslubi ajratiladi.',
            status: 'done',
          })
        } catch {
          setIngestionStartFailed(true)
          upsertActivity({
            id: 'telegram-history',
            title: 'Suhbat tarixi ulanmayapti',
            detail: 'Telegram sessiyasi saqlangan, lekin o‘qish qayta urinishga muhtoj.',
            status: 'failed',
          })
        }
      }

      const runSourceLearning = async () => {
        if (!hasSourceItems) return
        try {
          for (const [index, item] of sourceItems.entries()) {
            const title = sourceItemTitle(item)
            upsertActivity({
              id: `source-registration:${index}`,
              title: `Dalil saqlanmoqda: ${title}`,
              detail: sourceItemDetail(item),
              status: 'running',
            })
            await api.post('/api/business-brain/sources', toBusinessBrainSourcePayload(item))
            upsertActivity({
              id: `source-registration:${index}`,
              title: `Navbatga qo‘yildi: ${title}`,
              detail: 'OQIM buni alohida dalil sifatida saqladi. Endi undan katalog, bilim va qoida takliflari ajratiladi.',
              status: 'done',
            })
          }
          upsertActivity({
            id: 'source-registration',
            title: `${sourceItems.length} ta dalil saqlandi`,
            detail: 'Endi OQIM ularni katalog, fakt, qoida va bilimlarga ajratadi.',
            status: 'done',
          })
          upsertActivity({
            id: 'source-learning',
            title: 'Dalillar o‘qilmoqda',
            detail: 'Har bir dalil bo‘laklarga bo‘linadi, takliflar manbasi bilan chiqadi va xavfli narsa tasdiq kutadi.',
            status: 'running',
          })
          await api.post('/api/business-brain/sources/learn', {
            limit: Math.min(Math.max(sourceItems.length, 1), 10),
            max_attempts: 3,
            background: true,
          })
          await onboardingRuntime.refetch()
          upsertActivity({
            id: 'source-learning',
            title: 'Dalil o‘qish boshlandi',
            detail: 'OQIM ishni fonda davom ettiradi. Jarayon satri qaysi manba o‘qilayotgani, cache ishlatilgani va nechta taklif chiqqanini yangilab boradi.',
            status: 'running',
          })
        } catch {
          upsertActivity({
            id: 'source-learning',
            title: 'Dalil o‘qish to‘xtadi',
            detail: 'Dalil saqlanmadi yoki o‘qilmadi. Shu oynadan qayta urinish mumkin.',
            status: 'failed',
          })
          toast.error('Dalilni o‘qish boshlanmadi')
        }
      }

      await Promise.allSettled([
        runTelegramHistory(),
        runSourceLearning(),
      ])
      await Promise.allSettled([
        onboardingRuntime.refetch(),
        workspaceOS.refetch(),
      ])
      setIsSourceLearning(false)
    })()
  }, [
    buildCurrentSourceItems,
    onboardingRuntime,
    telegramConnected,
    upsertActivity,
    workspaceOS,
  ])

  const handleFinish = useCallback(async () => {
    const finalPhone = useTelegramPhone ? telegramPhone : loginPhone
    if (!finalPhone || password.trim().length < 8) {
      toast.error(uz.onboarding.finishError)
      return
    }

    setIsCompleting(true)
    try {
      await api.post('/api/auth/complete-onboarding', {
        name: businessName.trim(),
        category,
        monthly_revenue_band: revenueBand,
        phone_number: finalPhone,
        password: password.trim(),
        business_profile: {
          offer_summary: offerSummary.trim(),
          message_volume: messageVolume,
          reply_team_size: replyTeamSize,
          region: region.trim(),
          preferred_language: 'uzbek_latin',
          tone,
        },
        preferences: {
          reply_mode: replyMode,
          permission_mode: permissionMode,
          safe_autopilot: replyMode === 'safe_autopilot' || permissionMode !== 'ask_always',
          default_agents: enabledDefaultAgents,
          escalation_destination: 'in_app',
          quiet_hours: {
            enabled: false,
            start: '22:00',
            end: '09:00',
          },
          add_phone_later: true,
          invite_team_later: true,
        },
        sources: {
          notes: sourceNotes.trim(),
          items: buildCurrentSourceItems(),
        },
        owner_rules: {
          notes: ownerRules.trim(),
        },
      })
      await refreshUser()
      void workspaceOS.refetch()
      navigate({ to: '/brain', search: { tab: 'sources' } })
    } catch {
      toast.error(uz.onboarding.finishError)
    } finally {
      setIsCompleting(false)
    }
  }, [
    businessName,
    category,
    enabledDefaultAgents,
    messageVolume,
    loginPhone,
    navigate,
    offerSummary,
    ownerRules,
    password,
    region,
    permissionMode,
    refreshUser,
    revenueBand,
    replyMode,
    replyTeamSize,
    buildCurrentSourceItems,
    sourceNotes,
    telegramPhone,
    tone,
    workspaceOS,
    useTelegramPhone,
  ])

  const handleLaunch = useCallback(async (mode: 'start' | 'later') => {
    // The launch summary is the final onboarding step. The owner is already
    // authenticated via the Telegram bridge, so no password is required here —
    // we send the profile state collected so far plus the first-launch choice.
    // Backend treats every field as optional and uses `launch_mode` to decide
    // whether to activate the 5 default agents now or leave them off.
    setIsCompleting(true)
    try {
      await api.post('/api/auth/complete-onboarding', {
        name: businessName.trim() || undefined,
        category,
        monthly_revenue_band: revenueBand,
        business_profile: {
          offer_summary: offerSummary.trim(),
          message_volume: messageVolume,
          reply_team_size: replyTeamSize,
          region: region.trim(),
          preferred_language: 'uzbek_latin',
          tone,
        },
        preferences: {
          reply_mode: replyMode,
          permission_mode: permissionMode,
          safe_autopilot: replyMode === 'safe_autopilot' || permissionMode !== 'ask_always',
          default_agents: enabledDefaultAgents,
          escalation_destination: 'in_app',
          quiet_hours: {
            enabled: false,
            start: '22:00',
            end: '09:00',
          },
          add_phone_later: true,
          invite_team_later: true,
        },
        sources: {
          notes: sourceNotes.trim(),
          items: buildCurrentSourceItems(),
        },
        owner_rules: {
          notes: ownerRules.trim(),
        },
        launch_mode: mode,
      })
      hasLaunchedRef.current = true
      await refreshUser()
      void workspaceOS.refetch()
      navigate(mode === 'start'
        ? { to: '/conversations' }
        : { to: '/brain', search: { tab: 'sources' } })
    } catch {
      toast.error(uz.onboarding.launch.error)
    } finally {
      setIsCompleting(false)
    }
  }, [
    businessName,
    buildCurrentSourceItems,
    category,
    enabledDefaultAgents,
    messageVolume,
    navigate,
    offerSummary,
    ownerRules,
    permissionMode,
    refreshUser,
    region,
    replyMode,
    replyTeamSize,
    revenueBand,
    sourceNotes,
    tone,
    workspaceOS,
  ])

  const learnedPanel = (
    <LearnedOutputPanel
      phase={effectivePhase}
      runtime={onboardingRuntime.data}
      workspaceOS={workspaceOS.data}
      isLoading={onboardingRuntime.isLoading}
      error={onboardingRuntime.error}
      startFailed={ingestionStartFailed}
      activityEvents={activityEvents}
      isSourceLearning={isSourceLearning}
      draftSourceCount={draftSourceCount}
      enabledDefaultAgents={enabledDefaultAgents}
      permissionMode={permissionMode}
      launchStep={launchStep}
      reviewActionPending={reviewActionPending}
      onRetryLearning={retryLearning}
      onReviewAction={applyLearnedReviewAction}
    />
  )
  const profileFocusStep = effectivePhase === 'credentials' && launchStep === 'profile'
  const credentialsStep = (
    <CredentialsStep
      businessName={businessName}
      category={category}
      offerSummary={offerSummary}
      region={region}
      telegramPhone={telegramPhone || user?.phone_number || '+998'}
      loginPhone={loginPhone}
      password={password}
      useTelegramPhone={useTelegramPhone}
      enabledDefaultAgents={enabledDefaultAgents}
      permissionMode={permissionMode}
      launchStep={launchStep}
      isSubmitting={isCompleting}
      onBusinessNameChange={setBusinessName}
      onCategoryChange={setCategory}
      onOfferSummaryChange={setOfferSummary}
      onRegionChange={setRegion}
      onBack={() => setPhase('preferences')}
      onPhoneChange={setLoginPhone}
      onPasswordChange={setPassword}
      onTogglePhoneMode={setUseTelegramPhone}
      onToggleDefaultAgent={toggleDefaultAgent}
      onPermissionModeChange={setPermissionMode}
      onLaunchStepChange={setLaunchStep}
      onSubmit={handleFinish}
    />
  )

  return (
    <OnboardingFrame phase={effectivePhase}>
      {(effectivePhase === 'choice' || effectivePhase === 'telegram') ? (
        <div className="mx-auto w-full max-w-lg">
          {effectivePhase === 'choice' && (
            <ChoiceStep
              onExisting={() => navigate({ to: '/login' })}
              onNew={() => setPhase('telegram')}
            />
          )}

          {effectivePhase === 'telegram' && (
            <TelegramAuthStep
              isReconnect={!!user}
              isSessionRevoked={telegramStatus?.state === 'revoked'}
              isIdentityMismatch={telegramStatus?.identityMismatch === true}
              isAlreadyConnected={telegramConnected && !telegramStatus?.needsReconnect}
              onSkip={user ? () => setPhase('sources') : undefined}
              onSuccess={handlePhoneAuthSuccess}
            />
          )}
        </div>
      ) : profileFocusStep ? (
        <div className="mx-auto flex min-h-0 w-full max-w-3xl lg:h-[calc(100dvh-7.25rem)]">
          {credentialsStep}
        </div>
      ) : effectivePhase === 'documents' ? (
        <PhaseDocuments
          enabled={Boolean(user)}
          onNext={() => setPhase('launch')}
        />
      ) : effectivePhase === 'launch' ? (
        <div className="mx-auto w-full">
          <PhaseLaunch
            enabled={Boolean(user)}
            permissionMode={permissionMode}
            enabledDefaultAgents={enabledDefaultAgents}
            isSubmitting={isCompleting}
            onLaunch={handleLaunch}
          />
        </div>
      ) : (
        <OnboardingWorkbench
          phase={effectivePhase}
          learnedPanel={learnedPanel}
          statusBar={(
            <OnboardingStatusBar
              workspaceOS={workspaceOS.data}
              runtime={onboardingRuntime.data}
              telegramConnected={telegramConnected}
              activityEvents={activityEvents}
              phase={effectivePhase}
              draftSourceCount={draftSourceCount}
              isSourceLearning={isSourceLearning}
              isRebuilding={provisionWorkspaceOS.isPending}
              onRetryLearning={retryLearning}
            />
          )}
          rightRail={showDesktopRail ? (
            <WorkspaceOSRail
              workspaceOS={workspaceOS.data}
              runtime={onboardingRuntime.data}
              telegramConnected={telegramConnected}
              activityEvents={activityEvents}
              phase={effectivePhase}
              draftSourceCount={draftSourceCount}
              isSourceLearning={isSourceLearning}
              isRebuilding={provisionWorkspaceOS.isPending}
              onRetryLearning={retryLearning}
              onRebuild={() => provisionWorkspaceOS.mutate()}
            />
          ) : undefined}
        >
          {effectivePhase === 'basics' && (
            <BusinessBasicsStep
              businessName={businessName}
              category={category}
              revenueBand={revenueBand}
              offerSummary={offerSummary}
              region={region}
              onBusinessNameChange={setBusinessName}
              onCategoryChange={setCategory}
              onRevenueBandChange={setRevenueBand}
              onOfferSummaryChange={setOfferSummary}
              onRegionChange={setRegion}
              onNext={() => setPhase('sources')}
            />
          )}

          {effectivePhase === 'preferences' && (
            <PreferencesStep
              messageVolume={messageVolume}
              replyTeamSize={replyTeamSize}
              tone={tone}
              replyMode={replyMode}
              ownerRules={ownerRules}
              agentWebsiteSource={agentWebsiteSource}
              agentFileSource={agentFileSource}
              voiceSource={voiceSource}
              voiceFileSource={voiceFileSource}
              onMessageVolumeChange={setMessageVolume}
              onReplyTeamSizeChange={setReplyTeamSize}
              onToneChange={setTone}
              onReplyModeChange={setReplyMode}
              onOwnerRulesChange={setOwnerRules}
              onAgentWebsiteSourceChange={setAgentWebsiteSource}
              onAgentFileSourceChange={setAgentFileSource}
              onVoiceSourceChange={setVoiceSource}
              onVoiceFileSourceChange={setVoiceFileSource}
              onBack={() => setPhase('sources')}
              onNext={() => setPhase('credentials')}
            />
          )}

          {effectivePhase === 'sources' && (
            <SourcesStep
              sourceNotes={sourceNotes}
              websiteSource={websiteSource}
              telegramChannelSource={telegramChannelSource}
              telegramStartDate={telegramStartDate}
              telegramEndDate={telegramEndDate}
              fileSource={fileSource}
              onSourceNotesChange={setSourceNotes}
              onWebsiteSourceChange={setWebsiteSource}
              onTelegramChannelSourceChange={setTelegramChannelSource}
              onTelegramStartDateChange={setTelegramStartDate}
              onTelegramEndDateChange={setTelegramEndDate}
              onFileSourceChange={setFileSource}
              onBack={() => setPhase('telegram')}
              onNext={handleSourcesNext}
            />
          )}

          {effectivePhase === 'credentials' && (
            credentialsStep
          )}
        </OnboardingWorkbench>
      )}
    </OnboardingFrame>
  )
}

function sourceItemTitle(item: Record<string, unknown>) {
  const label = String(item.label ?? '').trim()
  if (label && label.toLowerCase() !== 'manba') return label
  const kind = String(item.kind ?? '')
  if (kind === 'telegram_channel') return String(item.handle ?? 'Telegram kanal')
  if (kind === 'website') return String(item.url ?? 'Sayt')
  if (kind === 'file' || kind === 'screenshot') return String(item.file_name ?? 'Fayl')
  if (kind === 'text') return 'Qo‘lda yozilgan matn'
  return 'Biznes manbasi'
}

function sourceItemDetail(item: Record<string, unknown>) {
  const purpose = item.purpose === 'agent_data'
    ? 'Agent qoidalari va yozish uslubi uchun saqlanadi.'
    : 'Business Brain uchun dalil sifatida saqlanadi.'
  const kind = String(item.kind ?? '')
  if (kind === 'telegram_channel') {
    const range = [
      item.date_from ? `boshlanish ${String(item.date_from)}` : null,
      item.date_to ? `tugash ${String(item.date_to)}` : null,
    ].filter(Boolean).join(', ')
    return range ? `${purpose} Telegram postlari: ${range}.` : `${purpose} Telegram postlari va media o‘qiladi.`
  }
  if (kind === 'website') return `${purpose} Sahifa matni va rasm dalillari o‘qiladi.`
  if (kind === 'file' || kind === 'screenshot') return `${purpose} Fayldan matn, jadval va media dalillar olinadi.`
  if (kind === 'text') return `${purpose} Matn tahrirlangan ko‘rinishda o‘qiladi.`
  return purpose
}
