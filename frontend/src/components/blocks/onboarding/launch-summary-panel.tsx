import type { ReactNode } from 'react'
import { BookOpen, FileText, ShieldCheck, Sparkle } from '@phosphor-icons/react'
import { Badge } from '@/components/ui/badge'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import type {
  OnboardingRuntimeProjection,
  WorkspaceOSAgentStatus,
  WorkspaceOSDocumentSectionPreview,
  WorkspaceOSProjection,
} from '@/lib/types'
import { DEFAULT_AGENT_OPTIONS, PERMISSION_MODE_OPTIONS } from './constants'
import type { DefaultAgentKey, LaunchStep, PermissionModeKey } from './types'

export function LaunchSummaryPanel({
  runtime,
  workspaceOS,
  enabledDefaultAgents,
  permissionMode,
  launchStep,
}: {
  runtime: OnboardingRuntimeProjection | undefined
  workspaceOS: WorkspaceOSProjection | undefined
  enabledDefaultAgents: DefaultAgentKey[]
  permissionMode: PermissionModeKey
  launchStep: LaunchStep
}) {
  const review = runtime?.learned_review
  const sourceLearning = runtime?.source_learning
  const readyFacts = (review?.summary.products ?? 0) + (review?.summary.knowledge ?? 0) + (review?.summary.rules ?? 0)
  const pendingReview = review?.summary.total_review_items ?? 0
  const conflicts = sourceLearning?.summary.conflict ?? 0
  const permission = PERMISSION_MODE_OPTIONS.find((option) => option.value === permissionMode) ?? PERMISSION_MODE_OPTIONS[0]
  const enabledAgents = DEFAULT_AGENT_OPTIONS.filter((agent) => enabledDefaultAgents.includes(agent.value))
  const osAgents = workspaceOS?.agents ?? []
  const documentStatus = workspaceOS?.documents
  const previewBeforeFinish = workspaceOS?.onboarding_completed === false
  const stepCopy = launchStepCopy(launchStep)

  return (
    <Card className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg py-0">
      <CardHeader className="shrink-0 px-6 py-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardTitle className="font-sans text-2xl font-semibold tracking-tight">{stepCopy.title}</CardTitle>
            <CardDescription className="mt-1 max-w-[60ch]">{stepCopy.description}</CardDescription>
          </div>
          <Badge variant="outline">{stepCopy.badge}</Badge>
        </div>
      </CardHeader>
      <CardContent className="grid min-h-0 flex-1 gap-5 overflow-y-auto px-6 pb-6">
        {launchStep === 'agents' ? (
          <section className="grid gap-4">
            <div className="grid gap-3">
              {enabledAgents.map((agent) => {
                const osAgent = osAgents.find((candidate) => candidate.package_key === agent.value)
                return (
                <div
                  key={agent.value}
                  className="grid grid-cols-[32px_minmax(0,1fr)] gap-3 border-b border-border pb-3 last:border-b-0"
                >
                  <span className="grid size-8 place-items-center rounded-md bg-muted text-foreground">
                    <Sparkle className="size-4" />
                  </span>
                  <span className="min-w-0">
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{agent.label}</span>
                      <AgentHealthBadge agent={osAgent} previewBeforeFinish={previewBeforeFinish} />
                    </span>
                    <span className="mt-1 block text-sm leading-5 text-muted-foreground">
                      {osAgent?.document_preview[0]?.body_preview || agent.description}
                    </span>
                    <span className="mt-1 block text-xs text-muted-foreground/80">
                      {agentPackageMeta(agent.tools, osAgent, previewBeforeFinish)}
                    </span>
                  </span>
                </div>
                )
              })}
            </div>
            {enabledAgents.length === 0 ? (
              <div className="rounded-lg border border-border px-4 py-4 text-sm text-muted-foreground">
                Kamida bitta agent tanlang. Aks holda OQIM faqat Brain yaratadi, ishlaydigan agent ochilmaydi.
              </div>
            ) : null}
            <DocumentPreviewStrip
              title="OS fayllari"
              description="OQIM ish boshlaganda shu hujjatlar agentlarga kontekst bo‘ladi."
              businessPreview={documentStatus?.sections_preview ?? []}
              agents={osAgents.filter((agent) => enabledDefaultAgents.includes(agent.package_key as DefaultAgentKey))}
              previewBeforeFinish={previewBeforeFinish}
              businessReady={Boolean(documentStatus?.business_md_ready)}
              businessSectionCount={documentStatus?.business_section_count ?? 0}
              agentSectionCount={documentStatus?.agent_section_count ?? 0}
              skillSectionCount={documentStatus?.skill_section_count ?? 0}
            />
          </section>
        ) : null}

        {launchStep === 'permission' ? (
          <section className="grid gap-4">
            <div className="grid grid-cols-[32px_minmax(0,1fr)] gap-3 rounded-lg border border-border px-4 py-4">
              <span className="grid size-8 place-items-center rounded-md bg-muted text-foreground">
                <ShieldCheck className="size-4" />
              </span>
              <span>
                <span className="block font-medium">{permission.label}</span>
                <span className="mt-1 block text-sm leading-6 text-muted-foreground">{permission.description}</span>
              </span>
            </div>
            <div className="grid gap-2 text-sm leading-6 text-muted-foreground">
              <p><span className="font-medium text-foreground">Past xavf:</span> javob taklifi, holat xabari, bitta mijozga follow-up.</p>
              <p><span className="font-medium text-foreground">Tasdiq kerak:</span> ommaviy yuborish, katalog o‘chirish, pul yoki yangi integratsiya.</p>
            </div>
            <DocumentPreviewStrip
              title="Policy nimaga tayanadi"
              description="Ruxsat faqat hujjat, skill va dalil bor joyda ishlaydi."
              businessPreview={documentStatus?.sections_preview ?? []}
              agents={osAgents.filter((agent) => enabledDefaultAgents.includes(agent.package_key as DefaultAgentKey))}
              previewBeforeFinish={previewBeforeFinish}
              businessReady={Boolean(documentStatus?.business_md_ready)}
              businessSectionCount={documentStatus?.business_section_count ?? 0}
              agentSectionCount={documentStatus?.agent_section_count ?? 0}
              skillSectionCount={documentStatus?.skill_section_count ?? 0}
            />
          </section>
        ) : null}

        {launchStep === 'profile' ? (
          <>
            <div className="grid gap-3">
              <div className="flex items-center justify-between gap-3 border-b border-border pb-3">
                <span className="text-sm text-muted-foreground">Bilim holati</span>
                <span className="font-medium">{readyFacts > 0 ? `${readyFacts} obyekt topildi` : 'Manbalardan o‘qiladi'}</span>
              </div>
              <div className="flex items-center justify-between gap-3 border-b border-border pb-3">
                <span className="text-sm text-muted-foreground">Ko‘rib chiqish</span>
                <span className="font-medium">{pendingReview > 0 ? `${pendingReview} ta tasdiq` : 'Shoshilinch tasdiq yo‘q'}</span>
              </div>
              <div className="flex items-center justify-between gap-3 border-b border-border pb-3">
                <span className="text-sm text-muted-foreground">Konflikt</span>
                <span className="font-medium">{conflicts > 0 ? `${conflicts} ta` : 'Yo‘q'}</span>
              </div>
            </div>

            <section>
              <div>
                <h3 className="font-sans text-base font-semibold">Yoqiladigan agentlar</h3>
                <p className="mt-1 text-sm text-muted-foreground">
                  {enabledAgents.length} ta agent bir xil boshlang‘ich ruxsat bilan ochiladi.
                </p>
              </div>
              <div className="mt-4 overflow-hidden rounded-lg border border-border">
                {enabledAgents.length > 0 ? enabledAgents.map((agent) => (
                  <div
                    key={agent.value}
                    className="grid grid-cols-[28px_minmax(0,1fr)_auto] items-center gap-3 border-b border-border px-4 py-3 last:border-b-0"
                  >
                    <span className="grid size-7 place-items-center rounded-lg bg-muted text-muted-foreground">
                      <Sparkle className="size-4" />
                    </span>
                    <span className="min-w-0">
                      <span className="block truncate font-medium leading-5">{agent.label}</span>
                      <span className="mt-0.5 block truncate text-xs leading-4 text-muted-foreground">
                        {agent.description}
                      </span>
                    </span>
                    <Badge variant="outline">{permission.label}</Badge>
                  </div>
                )) : (
                  <p className="px-4 py-3 text-sm text-muted-foreground">Hozircha agent tanlanmagan.</p>
                )}
              </div>
            </section>

            <DocumentPreviewStrip
              title="OS fayllari"
              description="Yakunlashdan keyin BUSINESS.md, AGENT.md va SKILL.md agentlarga kontekst bo‘ladi."
              businessPreview={documentStatus?.sections_preview ?? []}
              agents={osAgents.filter((agent) => enabledDefaultAgents.includes(agent.package_key as DefaultAgentKey))}
              previewBeforeFinish={previewBeforeFinish}
              businessReady={Boolean(documentStatus?.business_md_ready)}
              businessSectionCount={documentStatus?.business_section_count ?? 0}
              agentSectionCount={documentStatus?.agent_section_count ?? 0}
              skillSectionCount={documentStatus?.skill_section_count ?? 0}
            />

            <section className="border-t border-border pt-5">
              <div>
                <h3 className="font-sans text-base font-semibold">Ruxsat rejimi</h3>
                <p className="mt-1 text-sm text-muted-foreground">{permission.label}</p>
              </div>
              <p className="mt-4 text-sm leading-6 text-muted-foreground">
                {permission.description}
              </p>
            </section>

            <div className="border-t border-border pt-5">
              <p className="font-medium">Birinchi ishlar</p>
              <ol className="mt-4 grid list-decimal gap-2 pl-5 text-sm leading-6 text-muted-foreground">
                <li>Mijoz xabarlariga javob takliflari tayyorlaydi.</li>
                <li>Noaniq narx, SKU yoki qoida bo‘lsa, tasdiq so‘raydi.</li>
                <li>Manbalar o‘zgarsa, Brain va agent qoidalariga taklif yaratadi.</li>
              </ol>
            </div>
          </>
        ) : null}
      </CardContent>
    </Card>
  )
}

function AgentHealthBadge({
  agent,
  previewBeforeFinish,
}: {
  agent: WorkspaceOSAgentStatus | undefined
  previewBeforeFinish: boolean
}) {
  if (!agent) return <Badge variant="outline">Yakunlashdan keyin</Badge>
  if (previewBeforeFinish && agent.health !== 'ready') return <Badge variant="outline">Yakunlashdan keyin</Badge>
  if (agent.health === 'ready') return <Badge variant="success">Tayyor</Badge>
  if (agent.health === 'degraded') return <Badge variant="warning">Tekshirish kerak</Badge>
  return <Badge variant="outline">Yig‘ilmoqda</Badge>
}

function agentPackageMeta(tools: string, agent: WorkspaceOSAgentStatus | undefined, previewBeforeFinish: boolean) {
  if (!agent) return `${tools} · AGENT.md/SKILL.md yakunlashdan keyin yaratiladi`
  if (previewBeforeFinish && (agent.document_section_count === 0 || agent.skill_count === 0)) {
    return 'AGENT.md va SKILL.md yakunlashdan keyin to‘ldiriladi'
  }
  const skillText = agent.skill_count > 0 ? `${agent.skill_count} skill` : 'skill hali yo‘q'
  const sectionText = agent.document_section_count > 0 ? `${agent.document_section_count} bo‘lim` : 'AGENT.md hali yig‘ilmagan'
  return `${sectionText} · ${skillText}`
}

function DocumentPreviewStrip({
  title,
  description,
  businessPreview,
  agents,
  previewBeforeFinish,
  businessReady,
  businessSectionCount,
  agentSectionCount,
  skillSectionCount,
}: {
  title: string
  description: string
  businessPreview: WorkspaceOSDocumentSectionPreview[]
  agents: WorkspaceOSAgentStatus[]
  previewBeforeFinish: boolean
  businessReady: boolean
  businessSectionCount: number
  agentSectionCount: number
  skillSectionCount: number
}) {
  const agentPreviews = agents.flatMap((agent) => (
    agent.document_preview.map((section) => ({
      ...section,
      title: `${agent.name}: ${section.title}`,
    }))
  ))
  const skillPreviews = agents.flatMap((agent) => (
    agent.skill_names.map((skillName) => ({
      section_key: `${agent.package_key}:${skillName}`,
      title: skillName,
      body_preview: `${agent.name} shu ko‘nikma orqali dalil, ruxsat va trigger chegarasida ishlaydi.`,
      generated_by: 'workspace_os',
      source_evidence_count: 0,
    }))
  )).slice(0, 6)
  const hasAnyDocument = businessPreview.length > 0 || agentPreviews.length > 0 || skillPreviews.length > 0
  const businessLabel = businessSectionCount > 0 ? `${businessSectionCount} bo‘lim` : previewBeforeFinish ? 'yakunlashdan keyin' : 'hali yig‘ilmagan'
  const agentLabel = agentSectionCount > 0 ? `${agentSectionCount} bo‘lim` : previewBeforeFinish ? 'yakunlashdan keyin' : 'hali yig‘ilmagan'
  const skillLabel = skillSectionCount > 0 ? `${skillSectionCount} skill` : previewBeforeFinish ? 'yakunlashdan keyin' : 'hali yig‘ilmagan'

  return (
    <div className="grid gap-4 border-t border-border pt-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <span>
          <span className="block font-sans text-base font-semibold">{title}</span>
          <span className="mt-1 block text-sm leading-5 text-muted-foreground">{description}</span>
        </span>
        <Badge variant={businessReady ? 'success' : hasAnyDocument ? 'warning' : 'outline'}>
          {businessReady ? 'BUSINESS.md tayyor' : hasAnyDocument ? 'Yig‘ilmoqda' : previewBeforeFinish ? 'Yakunlashdan keyin' : 'Hali yo‘q'}
        </Badge>
      </div>

      <Tabs defaultValue="business" className="min-h-0 gap-3">
        <TabsList variant="line" className="w-full justify-start">
          <TabsTrigger value="business">BUSINESS.md</TabsTrigger>
          <TabsTrigger value="agent">AGENT.md</TabsTrigger>
          <TabsTrigger value="skill">SKILL.md</TabsTrigger>
        </TabsList>
        <TabsContent value="business" className="m-0">
          <DocumentArtifactPanel
            icon={<FileText />}
            label="BUSINESS.md"
            meta={businessLabel}
            emptyText={previewBeforeFinish
              ? 'Yakunlash tugmasidan keyin OQIM biznes kontekstini manba, suhbat va tanlovlardan yaratadi.'
              : 'OQIM biznes kontekstini manba, suhbat va tanlovlardan yig‘adi.'}
            sections={businessPreview}
          />
        </TabsContent>
        <TabsContent value="agent" className="m-0">
          <DocumentArtifactPanel
            icon={<BookOpen />}
            label="AGENT.md"
            meta={agentLabel}
            emptyText={previewBeforeFinish
              ? 'Yakunlashdan keyin tanlangan agentlar uchun rol, ruxsat va ish qoidalari yoziladi.'
              : 'Agent hujjatlari hali yig‘ilmagan.'}
            sections={agentPreviews}
          />
        </TabsContent>
        <TabsContent value="skill" className="m-0">
          <DocumentArtifactPanel
            icon={<Sparkle />}
            label="SKILL.md"
            meta={skillLabel}
            emptyText={previewBeforeFinish
              ? 'Yakunlashdan keyin agentlar ishlatadigan biznes ko‘nikmalari alohida skill sifatida saqlanadi.'
              : 'Agent skillari hali yig‘ilmagan.'}
            sections={skillPreviews}
          />
        </TabsContent>
      </Tabs>
    </div>
  )
}

function DocumentArtifactPanel({
  icon,
  label,
  meta,
  emptyText,
  sections,
}: {
  icon: ReactNode
  label: string
  meta: string
  emptyText: string
  sections: WorkspaceOSDocumentSectionPreview[]
}) {
  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <div className="grid grid-cols-[32px_minmax(0,1fr)] gap-3 border-b border-border bg-muted/20 px-4 py-3">
        <span className="mt-0.5 grid size-8 place-items-center rounded-md bg-background text-muted-foreground [&_svg]:size-4">
          {icon}
        </span>
        <span className="min-w-0">
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{label}</span>
            <span className="text-xs text-muted-foreground">{meta}</span>
          </span>
          <span className="mt-0.5 block text-sm leading-5 text-muted-foreground">
            {sections.length > 0 ? 'Formatlangan bo‘limlar. To‘liq tahrirlash keyin Brain yoki Agentlar sahifasida ochiladi.' : emptyText}
          </span>
        </span>
      </div>
      {sections.length > 0 ? (
        <div className="divide-y divide-border">
          {sections.slice(0, 4).map((section) => (
            <div key={section.section_key} className="px-4 py-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="font-medium leading-5">{section.title}</p>
                <Badge variant={section.source_evidence_count > 0 ? 'success' : 'outline'}>
                  {section.source_evidence_count > 0 ? `${section.source_evidence_count} dalil` : 'shablon'}
                </Badge>
              </div>
              <p className="mt-1 text-sm leading-5 text-muted-foreground">{section.body_preview}</p>
            </div>
          ))}
        </div>
      ) : (
        <p className="px-4 py-4 text-sm leading-6 text-muted-foreground">{emptyText}</p>
      )}
    </div>
  )
}

function launchStepCopy(step: LaunchStep) {
  if (step === 'agents') {
    return {
      title: 'Agentlar qanday ish boshlaydi',
      description: 'Tanlangan rollar shu yerda oddiy tilda ko‘rinadi. Bu hali yakuniy ruxsat emas.',
      badge: '1-bosqich',
    }
  }
  if (step === 'permission') {
    return {
      title: 'Ruxsat chegarasi',
      description: 'Agent nima qila olishi policy orqali boshqariladi. Prompt bilan xavfli ish bajarilmaydi.',
      badge: '2-bosqich',
    }
  }
  return {
    title: 'Ishga tushirish xulosasi',
    description: 'Oxirgi tekshiruv: agentlar, ruxsat va navbatdagi birinchi ishlar.',
    badge: '3-bosqich',
  }
}
