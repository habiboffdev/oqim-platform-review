import { Children, useMemo, useState, type ReactNode } from 'react'
import { Link, useParams } from '@tanstack/react-router'
import {
  ArrowLeft,
  Brain,
  FileText,
  Lightning,
  PencilSimpleLine,
  Plugs,
  ShieldCheck,
  WarningCircle,
} from '@phosphor-icons/react'
import type { Icon } from '@phosphor-icons/react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from '@/components/ui/empty'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { uz } from '@/lib/uz'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import {
  type AgentToolCatalogItem,
  type AgentDocumentSection,
  useAgentDetail,
  useProposeAgentTrigger,
  useProposeAgentToolGrant,
  useToolCatalog,
  useUpdateAgentState,
  useUpsertAgentSection,
} from '@/hooks/use-agent-workbench'
import { agentTypeLabel, permissionLabel } from './agents'

export function AgentDetailPage() {
  const params = useParams({ strict: false }) as { agentId?: string }
  const agentId = params.agentId ? Number(params.agentId) : null
  const detail = useAgentDetail(Number.isFinite(agentId) ? agentId : null)
  const updateAgent = useUpdateAgentState(Number.isFinite(agentId) ? agentId : null)
  const upsertSection = useUpsertAgentSection(Number.isFinite(agentId) ? agentId : null)
  const proposeToolGrant = useProposeAgentToolGrant(Number.isFinite(agentId) ? agentId : null)
  const proposeTrigger = useProposeAgentTrigger(Number.isFinite(agentId) ? agentId : null)
  const toolCatalog = useToolCatalog('telegram')
  const [editingSection, setEditingSection] = useState<AgentDocumentSection | null>(null)
  const [draft, setDraft] = useState('')

  const data = detail.data
  const sortedSections = useMemo(
    () => [...(data?.sections ?? [])].sort((a, b) => a.order_index - b.order_index),
    [data?.sections],
  )
  const knownToolScopes = useMemo(
    () => new Set((data?.tool_grants ?? []).map((grant) => grant.scope)),
    [data?.tool_grants],
  )
  const toolOptions = useMemo(
    () => toolCatalogOptions(toolCatalog.data?.items),
    [toolCatalog.data?.items],
  )
  const toolOptionByScope = useMemo(
    () => new Map(toolOptions.map((option) => [option.value, option])),
    [toolOptions],
  )
  const availableToolScopes = useMemo(
    () => toolOptions.filter((option) => !knownToolScopes.has(option.value)),
    [knownToolScopes, toolOptions],
  )
  const knownTriggerKeys = useMemo(
    () => new Set((data?.triggers ?? []).map((trigger) => `${trigger.event_source}:${trigger.action_proposal_type}`)),
    [data?.triggers],
  )
  const availableTriggers = useMemo(
    () => AGENT_TRIGGER_OPTIONS.filter((option) => !knownTriggerKeys.has(`${option.event_source}:${option.action_proposal_type}`)),
    [knownTriggerKeys],
  )

  if (!agentId) {
    return <AgentDetailMessage title="Agent topilmadi" text="Agent havolasi noto‘g‘ri." />
  }

  if (detail.isLoading) {
    return <AgentDetailLoading />
  }

  if (detail.error || !data) {
    return <AgentDetailMessage title="Agent ochilmadi" text="Ma’lumotni yuklab bo‘lmadi. Qayta urinib ko‘ring." />
  }

  const hasFullAccess = data.enforced_config.permission_mode === 'full_access'

  return (
    <div className="grid h-full min-h-0 grid-cols-1 bg-background text-foreground xl:grid-cols-[minmax(0,1fr)_360px]">
      <section className="flex min-h-0 flex-col">
        <header className="border-b border-border/60 px-5 py-4 lg:px-8">
          <Link
            to="/agents"
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="size-3.5" weight="thin" />
            Agentlarga qaytish
          </Link>

          <div className="mt-3 flex flex-wrap items-center justify-between gap-4">
            <div className="flex min-w-0 items-center gap-3">
              <span className="flex size-11 shrink-0 items-center justify-center rounded-full border border-border bg-background text-base font-semibold">
                {data.agent.name.slice(0, 1).toUpperCase()}
              </span>
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <h1 className="truncate text-xl font-semibold tracking-tight">{data.agent.name}</h1>
                  <Badge variant={data.agent.is_active ? 'success' : 'outline'}>
                    {data.agent.is_active ? 'Faol' : 'To‘xtagan'}
                  </Badge>
                  <Badge variant={hasFullAccess ? 'warning' : 'outline'}>
                    {permissionLabel(data.enforced_config.permission_mode)}
                  </Badge>
                </div>
                <div className="mt-1 text-sm text-muted-foreground">
                  {agentTypeLabel(data.agent.agent_type)} · {data.skills.length} ta ko‘nikma · {data.tool_grants.length} ruxsat
                </div>
              </div>
            </div>

            <div className="flex items-center gap-3">
              <label className="flex items-center gap-2 text-sm">
                <Switch
                  checked={data.agent.trust_mode === 'autopilot'}
                  disabled={updateAgent.isPending}
                  onCheckedChange={(checked: boolean) =>
                    updateAgent.mutate({ trust_mode: checked ? 'autopilot' : 'disabled' })
                  }
                  aria-label="Avtopilotni yoqish yoki o‘chirish"
                />
                {uz.agents.trustModes.autopilot}
              </label>
              <label className="flex items-center gap-2 text-sm">
                <Switch
                  checked={data.agent.is_active}
                  disabled={updateAgent.isPending}
                  onCheckedChange={(checked: boolean) => updateAgent.mutate({ is_active: checked })}
                  aria-label="Agent holatini o‘zgartirish"
                />
                Faol
              </label>
              <Button variant="outline" size="sm" disabled>
                <Lightning data-icon="inline-start" />
                Sinovdan o‘tkazish
              </Button>
            </div>
          </div>
        </header>

        <ScrollArea className="min-h-0 flex-1">
          <div className="flex flex-col gap-4 px-5 py-5 lg:px-8">
            {data.drift_warnings.length > 0 ? (
              <Alert>
                <WarningCircle />
                <AlertTitle>{data.drift_warnings[0].title_uz}</AlertTitle>
                <AlertDescription>
                  {data.drift_warnings[0].detail_uz} Hujjat: {permissionLabel(data.drift_warnings[0].document_value ?? '')}. Hozir ishlayotgan sozlama: {permissionLabel(data.drift_warnings[0].enforced_value)}.
                </AlertDescription>
              </Alert>
            ) : null}

            {hasFullAccess ? (
              <Alert>
                <ShieldCheck />
                <AlertTitle>To‘liq ruxsat yoqilgan</AlertTitle>
                <AlertDescription>
                  Bu agent ayrim ishlarni egadan so‘ramay bajarishi mumkin. Xavfli yuborish, narx, to‘lov va permission o‘zgarishlari baribir auditdan o‘tishi kerak.
                </AlertDescription>
              </Alert>
            ) : null}

            <Tabs defaultValue="document" className="min-h-0">
              <TabsList variant="line">
                <TabsTrigger value="document">AGENT.md</TabsTrigger>
                <TabsTrigger value="config">Sozlamalar</TabsTrigger>
                <TabsTrigger value="history">Tarix</TabsTrigger>
              </TabsList>

              <TabsContent value="document" className="mt-3">
                <article className="rounded-lg border border-border bg-background">
                  <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-4">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 text-sm font-medium">
                        <FileText className="size-4" weight="thin" />
                        AGENT.md
                      </div>
                      <p className="mt-1 text-sm text-muted-foreground">
                        Egaga ko‘rinadigan agent hujjati. Har bo‘lim structured config bilan bog‘langan.
                      </p>
                    </div>
                    <Badge variant="outline">{data.rendered.sections_used} bo‘lim</Badge>
                  </div>

                  {sortedSections.length === 0 ? (
                    <Empty className="min-h-[320px]">
                      <EmptyHeader>
                        <EmptyTitle>AGENT.md hali to‘liq emas</EmptyTitle>
                        <EmptyDescription>
                          Rol, qachon ishlashi, nimani taxmin qilmasligi va ruxsat bo‘limlarini qo‘shing.
                        </EmptyDescription>
                      </EmptyHeader>
                    </Empty>
                  ) : (
                    sortedSections.map((section, index) => (
                      <section key={section.id} className="px-5 py-5">
                        {index > 0 ? <Separator className="-mt-5 mb-5" /> : null}
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <h2 className="text-sm font-semibold">{section.title}</h2>
                            <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-foreground">
                              {section.body || 'Bu bo‘lim hali bo‘sh.'}
                            </p>
                            <div className="mt-3 text-xs text-muted-foreground">
                              {section.generated_by === 'owner' ? 'Ega tahrirlagan' : 'OQIM yaratgan'}
                            </div>
                          </div>
                          <Button
                            variant="outline"
                            size="xs"
                            onClick={() => {
                              setEditingSection(section)
                              setDraft(section.body)
                            }}
                          >
                            <PencilSimpleLine data-icon="inline-start" />
                            Tahrirlash
                          </Button>
                        </div>
                      </section>
                    ))
                  )}
                </article>
              </TabsContent>

              <TabsContent value="config" className="mt-3">
                <div className="rounded-lg border border-border">
                  <ConfigRow label="Ruxsat rejimi" value={permissionLabel(data.enforced_config.permission_mode)} />
                  <ConfigRow label="Ish rejimi" value={trustModeLabel(data.enforced_config.trust_mode)} />
                  <ConfigRow label="Mijoz doirasi" value={scopeLabel(data.enforced_config.contact_scope)} />
                  <ConfigRow label="Brain doirasi" value={brainScopeListLabel(data.enforced_config.brain_scopes)} />
                  <ConfigRow label="Kanal rejimi" value={channelModeLabel(data.enforced_config.channel_mode)} />
                </div>
              </TabsContent>

              <TabsContent value="history" className="mt-3">
                <RecentActions rows={data.recent_actions} />
              </TabsContent>
            </Tabs>
          </div>
        </ScrollArea>
      </section>

      <aside className="hidden min-h-0 border-l border-border/60 bg-foreground/[0.015] xl:flex xl:flex-col">
        <Tabs defaultValue="skills" className="min-h-0 flex-1 gap-0">
          <div className="border-b border-border/60 px-4 py-3">
            <TabsList variant="line" className="w-full justify-start">
              <TabsTrigger value="skills">Ko‘nikmalar</TabsTrigger>
              <TabsTrigger value="tools">Ruxsatlar</TabsTrigger>
              <TabsTrigger value="triggers">Triggerlar</TabsTrigger>
            </TabsList>
          </div>
          <ScrollArea className="min-h-0 flex-1">
            <TabsContent value="skills" className="m-0">
              <SidePanel
                icon={Brain}
                title="Ko‘nikmalar"
                count={data.skills.length}
                empty="Bu agentga hali ko‘nikma biriktirilmagan."
              >
                {data.skills.map((skill) => (
                  <SideRow
                    key={skill.id}
                    title={skill.name}
                    description={skill.description || skill.slug}
                    badge={skill.enabled ? 'Yoqilgan' : 'O‘chirilgan'}
                    muted={!skill.enabled}
                  />
                ))}
              </SidePanel>
            </TabsContent>
            <TabsContent value="tools" className="m-0">
              <SidePanel
                icon={Plugs}
                title="Integratsiya ruxsatlari"
                count={data.tool_grants.length}
                empty="Bu agentga hali Telegram yoki boshqa integratsiya ruxsati berilmagan."
              >
                {data.tool_grants.map((grant) => (
                  <SideRow
                    key={grant.id}
                    title={toolScopeLabel(grant.scope, toolOptionByScope)}
                    description={grant.grant_reason || toolScopeDescription(grant.scope, toolOptionByScope)}
                    badge={grant.active ? 'Ruxsat bor' : 'O‘chirilgan'}
                    muted={!grant.active}
                    action={
                      <Button
                        variant="outline"
                        size="xs"
                        loading={proposeToolGrant.isPending}
                        onClick={() => {
                          proposeToolGrant.mutate({
                            action: grant.active ? 'revoke' : 'grant',
                            scope: grant.scope,
                            reason: `${data.agent.name} agenti uchun ${toolScopeLabel(grant.scope, toolOptionByScope).toLowerCase()} ruxsati`,
                            correlation_id: `ui:agent:${data.agent.id}:tool-grant`,
                          })
                        }}
                      >
                        {grant.active ? 'O‘chirish' : 'Qayta yoqish'}
                      </Button>
                    }
                  />
                ))}
                {availableToolScopes.length > 0 ? (
                  <div className="rounded-lg border border-dashed border-border bg-background px-3 py-3">
                    <div className="text-sm font-medium">Ruxsat qo‘shish</div>
                    <p className="mt-1 text-xs leading-5 text-muted-foreground">
                      Agentga yangi Telegram imkoniyati faqat tasdiqdan keyin beriladi.
                    </p>
                    <div className="mt-3 flex flex-col gap-2">
                      {availableToolScopes.map((option) => (
                        <Button
                          key={option.value}
                          variant="outline"
                          size="sm"
                          className="justify-between"
                          loading={proposeToolGrant.isPending}
                          onClick={() => {
                              proposeToolGrant.mutate({
                                action: 'grant',
                                scope: option.value,
                                reason: `${data.agent.name} agenti uchun ${option.label.toLowerCase()} ruxsati`,
                              correlation_id: `ui:agent:${data.agent.id}:tool-grant`,
                            })
                          }}
                        >
                          <span>{option.label}</span>
                          <span className="text-xs text-muted-foreground">{option.short}</span>
                        </Button>
                      ))}
                    </div>
                  </div>
                ) : null}
              </SidePanel>
            </TabsContent>
            <TabsContent value="triggers" className="m-0">
              <SidePanel
                icon={Lightning}
                title="Triggerlar"
                count={data.triggers.length}
                empty="Bu agentni ishga tushiradigan trigger hali yo‘q."
              >
                {data.triggers.map((trigger) => (
                  <SideRow
                    key={trigger.id}
                    title={triggerLabel(trigger.event_source)}
                    description={trigger.notes || triggerActionLabel(trigger.action_proposal_type)}
                    badge={trigger.active ? 'Faol' : 'To‘xtagan'}
                    muted={!trigger.active}
                    action={
                      <Button
                        variant="outline"
                        size="xs"
                        loading={proposeTrigger.isPending}
                        onClick={() => {
                          proposeTrigger.mutate({
                            operation: trigger.active ? 'deactivate' : 'create',
                            trigger_id: trigger.active ? trigger.id : undefined,
                            event_source: trigger.active ? undefined : trigger.event_source,
                            action_proposal_type: trigger.active ? undefined : trigger.action_proposal_type,
                            matching_scope: trigger.active ? undefined : trigger.matching_scope,
                            permission_mode: trigger.active ? undefined : trigger.permission_mode as 'ask_always' | 'auto_approve' | 'full_access',
                            retry_policy: trigger.active ? undefined : trigger.retry_policy,
                            notes: trigger.notes,
                            correlation_id: `ui:agent:${data.agent.id}:trigger`,
                          })
                        }}
                      >
                        {trigger.active ? 'To‘xtatish' : 'Qayta yoqish'}
                      </Button>
                    }
                  />
                ))}
                {availableTriggers.length > 0 ? (
                  <div className="rounded-lg border border-dashed border-border bg-background px-3 py-3">
                    <div className="text-sm font-medium">Trigger qo‘shish</div>
                    <p className="mt-1 text-xs leading-5 text-muted-foreground">
                      Agent qachon ish boshlashini tasdiq orqali yoqasiz.
                    </p>
                    <div className="mt-3 flex flex-col gap-2">
                      {availableTriggers.map((option) => (
                        <Button
                          key={`${option.event_source}:${option.action_proposal_type}`}
                          variant="outline"
                          size="sm"
                          className="justify-between"
                          loading={proposeTrigger.isPending}
                          onClick={() => {
                            proposeTrigger.mutate({
                              operation: 'create',
                              event_source: option.event_source,
                              action_proposal_type: option.action_proposal_type,
                              matching_scope: option.matching_scope,
                              permission_mode: option.permission_mode,
                              retry_policy: option.retry_policy,
                              notes: option.notes,
                              correlation_id: `ui:agent:${data.agent.id}:trigger`,
                            })
                          }}
                        >
                          <span>{option.label}</span>
                          <span className="text-xs text-muted-foreground">{option.short}</span>
                        </Button>
                      ))}
                    </div>
                  </div>
                ) : null}
              </SidePanel>
            </TabsContent>
          </ScrollArea>
        </Tabs>
      </aside>

      <Sheet open={editingSection !== null} onOpenChange={(open) => !open && setEditingSection(null)}>
        <SheetContent className="w-full sm:max-w-xl">
          <SheetHeader>
            <SheetTitle>{editingSection?.title ?? 'Bo‘limni tahrirlash'}</SheetTitle>
            <SheetDescription>
              Bu AGENT.md bo‘limi agent qanday ishlashini tushuntiradi. Xavfli ruxsatlar alohida sozlamada boshqariladi.
            </SheetDescription>
          </SheetHeader>
          <div className="px-6">
            <Textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              size="lg"
              rows={8}
              aria-label="AGENT.md bo‘limi"
            />
          </div>
          <SheetFooter>
            <Button variant="outline" onClick={() => setEditingSection(null)}>
              Bekor
            </Button>
            <Button
              loading={upsertSection.isPending}
              onClick={() => {
                if (!editingSection) return
                upsertSection.mutate(
                  {
                    section_key: editingSection.section_key,
                    title: editingSection.title,
                    body: draft.trim(),
                    order_index: editingSection.order_index,
                  },
                  { onSuccess: () => setEditingSection(null) },
                )
              }}
            >
              Saqlash
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>
    </div>
  )
}

function AgentDetailLoading() {
  return (
    <div className="flex h-full flex-col gap-4 px-6 py-6">
      <Skeleton className="h-10 w-80 rounded-lg" />
      <Skeleton className="h-20 w-full rounded-lg" />
      <Skeleton className="h-80 w-full rounded-lg" />
    </div>
  )
}

function AgentDetailMessage({ title, text }: { title: string; text: string }) {
  return (
    <Empty className="h-full">
      <EmptyHeader>
        <EmptyTitle>{title}</EmptyTitle>
        <EmptyDescription>{text}</EmptyDescription>
      </EmptyHeader>
      <EmptyContent>
        <Button render={<Link to="/agents" />}>Agentlarga qaytish</Button>
      </EmptyContent>
    </Empty>
  )
}

function ConfigRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid gap-1 border-b border-border px-4 py-3 last:border-b-0 md:grid-cols-[180px_minmax(0,1fr)]">
      <div className="text-sm text-muted-foreground">{label}</div>
      <div className="text-sm font-medium">{value}</div>
    </div>
  )
}

function SidePanel({
  icon: Icon,
  title,
  count,
  empty,
  children,
}: {
  icon: Icon
  title: string
  count: number
  empty: string
  children: ReactNode
}) {
  const hasChildren = Children.count(children) > 0
  return (
    <div className="px-4 py-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Icon className="size-4" weight="thin" />
          {title}
        </div>
        <Badge variant="outline">{count}</Badge>
      </div>
      <div className="mt-3 flex flex-col gap-2">
        {count === 0 && !hasChildren ? <p className="text-sm leading-6 text-muted-foreground">{empty}</p> : children}
      </div>
    </div>
  )
}

function SideRow({
  title,
  description,
  badge,
  muted,
  meta,
  action,
}: {
  title: string
  description: string
  badge: string
  muted?: boolean
  meta?: string
  action?: ReactNode
}) {
  return (
    <div className="rounded-lg border border-border bg-background px-3 py-2.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className={muted ? 'truncate text-sm text-muted-foreground' : 'truncate text-sm font-medium'}>
            {title}
          </div>
          {meta ? <div className="mt-0.5 truncate text-[11px] text-muted-foreground">{meta}</div> : null}
          <div className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">
            {description}
          </div>
          {action ? <div className="mt-2">{action}</div> : null}
        </div>
        <Badge variant={muted ? 'outline' : 'success'}>{badge}</Badge>
      </div>
    </div>
  )
}

const TELEGRAM_TOOL_SCOPE_OPTIONS = [
  { value: 'telegram.read_messages', label: 'Suhbatni o‘qish', short: 'read' },
  { value: 'telegram.send_message', label: 'Javob yuborish', short: 'send' },
  { value: 'telegram.edit_message', label: 'Yuborilganni tahrirlash', short: 'edit' },
  { value: 'telegram.watch_channel', label: 'Kanalni kuzatish', short: 'watch' },
  { value: 'telegram.fetch_media', label: 'Media ochish', short: 'media' },
  { value: 'telegram.sync_history', label: 'Tarixni yangilash', short: 'sync' },
] as const

type ToolScopeOption = {
  value: string
  label: string
  short: string
  description: string
  risk: string
  requiresActionProposal: boolean
}

function toolCatalogOptions(items: AgentToolCatalogItem[] | undefined): ToolScopeOption[] {
  if (!items?.length) {
    return TELEGRAM_TOOL_SCOPE_OPTIONS.map((item) => ({
      ...item,
      description: 'Agent bu ruxsatni faqat policy va audit orqali ishlatadi.',
      risk: 'medium',
      requiresActionProposal: item.value === 'telegram.send_message' || item.value === 'telegram.edit_message',
    }))
  }
  return items
    .filter((item) => item.owner_visible)
    .map((item) => ({
      value: item.scope,
      label: item.label_uz,
      short: item.short_label,
      description: item.description_uz,
      risk: item.risk_level,
      requiresActionProposal: item.requires_action_proposal,
    }))
}

const AGENT_TRIGGER_OPTIONS = [
  {
    event_source: 'channel_message_received',
    action_proposal_type: 'conversation.propose_reply',
    label: 'Yangi xabarda javob taklif qilish',
    short: 'suhbat',
    matching_scope: {},
    permission_mode: 'ask_always' as const,
    retry_policy: { max_attempts: 3 },
    notes: 'Mijoz yangi xabar yozganda agent javob taklif qiladi.',
  },
  {
    event_source: 'owner_bi_command',
    action_proposal_type: 'agent.handle_owner_command',
    label: 'BI buyrug‘idan ish boshlash',
    short: 'BI',
    matching_scope: {},
    permission_mode: 'ask_always' as const,
    retry_policy: { max_attempts: 2 },
    notes: 'Ega BI agentga topshiriq berganda bu agent ishga tushadi.',
  },
  {
    event_source: 'source_changed',
    action_proposal_type: 'source.review_change',
    label: 'Manba yangilanganda tekshirish',
    short: 'manba',
    matching_scope: {},
    permission_mode: 'ask_always' as const,
    retry_policy: { max_attempts: 2 },
    notes: 'Manba o‘zgarganda agent yangilikni tekshirib taklif beradi.',
  },
] as const

function RecentActions({ rows }: { rows: Array<{ proposal_id: string; summary_uz: string; lifecycle_state: string; risk_level: string; created_at: string }> }) {
  if (rows.length === 0) {
    return (
      <Empty className="min-h-[260px] rounded-lg border border-border">
        <EmptyHeader>
          <EmptyTitle>Hali ish tarixi yo‘q</EmptyTitle>
          <EmptyDescription>Agent taklif yoki amal bajarganda shu yerda ko‘rinadi.</EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  }
  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Vaqt</TableHead>
            <TableHead>Amal</TableHead>
            <TableHead>Xavf</TableHead>
            <TableHead>Holat</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.proposal_id}>
              <TableCell>{formatDate(row.created_at)}</TableCell>
              <TableCell>{row.summary_uz}</TableCell>
              <TableCell>{riskLabel(row.risk_level)}</TableCell>
              <TableCell>{lifecycleLabel(row.lifecycle_state)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function trustModeLabel(value: string) {
  // Two trust states only: autopilot (runs + sends) or disabled (off). Legacy
  // draft/autonomous render as disabled.
  if (value === 'autopilot') return uz.agents.trustModes.autopilot
  return uz.agents.trustModes.disabled
}

function scopeLabel(value: string) {
  if (value === 'all') return 'Hamma manbalar'
  return 'Biznes suhbatlari'
}

function brainScopeListLabel(values: string[]) {
  return values.length ? values.map(brainScopeLabel).join(', ') : 'Hali tanlanmagan'
}

function brainScopeLabel(value: string) {
  const labels: Record<string, string> = {
    catalog: 'Katalog',
    knowledge: 'Bilim bazasi',
    rules: 'Qoidalar',
    voice: 'Ovoz uslubi',
    examples: 'Suhbat namunalari',
    sources: 'Manbalar',
    conversation_state: 'Suhbat holati',
    tasks: 'Vazifalar',
    issues: 'Muammolar',
  }
  return labels[value] ?? value.replaceAll('_', ' ')
}

function channelModeLabel(value?: string | null) {
  if (value === 'workspace_events') return 'Workspace voqealari orqali'
  if (!value) return 'Hali tanlanmagan'
  return value.replaceAll('_', ' ')
}

function toolScopeLabel(value: string, catalog?: Map<string, ToolScopeOption>) {
  const option = catalog?.get(value)
  if (option) return option.label
  const labels: Record<string, string> = {
    'telegram.read_messages': 'Suhbatni o‘qish',
    'telegram.send_message': 'Javob yuborish',
    'telegram.edit_message': 'Yuborilgan javobni tahrirlash',
    'telegram.watch_channel': 'Telegram kanalni kuzatish',
    'telegram.fetch_media': 'Media ochish',
    'telegram.sync_history': 'Suhbat tarixini yangilash',
    'brain.search': 'Brain ichidan dalil qidirish',
    'conversation.get_context': 'Suhbat kontekstini ko‘rish',
    'conversation.propose_reply': 'Javob taklif qilish',
    'action.create_proposal': 'Tasdiqlanadigan amal taklif qilish',
    'source.ingest': 'Manbani qayta o‘qish',
    'catalog.search': 'Katalogdan qidirish',
    'catalog.propose_product_change': 'Katalog o‘zgarishini taklif qilish',
    'task.propose': 'Vazifa taklif qilish',
  }
  return labels[value] ?? value.replaceAll('.', ' ').replaceAll('_', ' ')
}

function toolScopeDescription(value: string, catalog?: Map<string, ToolScopeOption>) {
  return catalog?.get(value)?.description || 'Agent bu ruxsatni faqat policy va audit orqali ishlatadi.'
}

function triggerLabel(value: string) {
  const labels: Record<string, string> = {
    channel_message_received: 'Yangi Telegram xabar',
    source_added: 'Manba qo‘shildi',
    source_changed: 'Manba yangilandi',
    schedule: 'Jadval bo‘yicha',
    owner_bi_command: 'BI agent buyrug‘i',
    task_due: 'Vazifa muddati',
    catalog_conflict_detected: 'Katalog konflikti',
    customer_stage_changed: 'Mijoz bosqichi o‘zgardi',
  }
  return labels[value] ?? value.replaceAll('_', ' ')
}

function triggerActionLabel(value: string) {
  const labels: Record<string, string> = {
    'conversation.propose_reply': 'Javob taklif qiladi',
    'catalog.update_product': 'Katalog yangilashni taklif qiladi',
    'task.daily_review': 'Kunlik vazifalarni ko‘rib chiqadi',
    'agent.handle_owner_command': 'BI buyrug‘idan ish boshlaydi',
    'source.review_change': 'Manba o‘zgarishini tekshiradi',
  }
  return labels[value] ?? value.replaceAll('.', ' ').replaceAll('_', ' ')
}

function riskLabel(value: string) {
  if (value === 'high') return 'Yuqori'
  if (value === 'medium') return 'O‘rta'
  if (value === 'low') return 'Past'
  return value
}

function lifecycleLabel(value: string) {
  const labels: Record<string, string> = {
    proposed: 'Taklif',
    waiting_approval: 'Tasdiq kutmoqda',
    approved: 'Tasdiqlangan',
    executing: 'Bajarilmoqda',
    executed: 'Bajarildi',
    failed: 'Xato',
    blocked: 'To‘xtagan',
    rejected: 'Rad etilgan',
  }
  return labels[value] ?? value.replaceAll('_', ' ')
}

function formatDate(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('uz-UZ', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}
