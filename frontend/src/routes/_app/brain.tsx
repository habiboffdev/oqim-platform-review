import { useMemo, useState } from 'react'
import type { ChangeEvent } from 'react'
import { useNavigate, useSearch } from '@tanstack/react-router'
import {
  BookOpen,
  Boxes,
  Brain,
  CircleAlert,
  Database,
  FileText,
  MessageSquareText,
  PanelRightOpen,
  Search,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from '@/components/ui/empty'
import { Input } from '@/components/ui/input'
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import {
  useBusinessBrainObjects,
  useBusinessMdDocument,
  useUpsertBusinessMdSection,
  type BusinessMdSection,
} from '@/hooks/use-business-brain'
import {
  useAgentDetail,
  useAgentWorkbenchAgents,
  useUpsertAgentSection,
  type AgentDetailResponse,
  type AgentDocumentSection,
  type AgentWorkbenchRow,
} from '@/hooks/use-agent-workbench'
import type {
  BrainObjectDomain,
  BrainObjectEvidence,
  BrainObjectItem,
  BrainObjectProjection,
  BrainObjectState,
  BrainObjectSourceLifecycle,
} from '@/lib/types'
import { cn } from '@/lib/utils'

type BrainView = BrainObjectDomain | 'documents' | 'all'

const EMPTY_COUNTS: Record<BrainObjectDomain, number> = {
  catalog: 0,
  knowledge: 0,
  rules: 0,
  voice: 0,
  examples: 0,
  issues: 0,
  sources: 0,
}

const VIEW_OPTIONS: {
  id: BrainView
  label: string
  icon: LucideIcon
}[] = [
  { id: 'all', label: 'Hammasi', icon: Brain },
  { id: 'catalog', label: 'Katalog', icon: Boxes },
  { id: 'knowledge', label: 'Bilim', icon: BookOpen },
  { id: 'rules', label: 'Qoidalar', icon: ShieldCheck },
  { id: 'voice', label: 'Ovoz', icon: Sparkles },
  { id: 'examples', label: 'Namuna', icon: MessageSquareText },
  { id: 'issues', label: 'Muammo', icon: CircleAlert },
  { id: 'sources', label: 'Dalillar', icon: Database },
  { id: 'documents', label: 'Hujjatlar', icon: FileText },
]

const DOMAIN_VIEW_IDS = new Set<BrainObjectDomain>([
  'catalog',
  'knowledge',
  'rules',
  'voice',
  'examples',
  'issues',
  'sources',
])

function normalizeView(value: string | undefined): BrainView {
  if (!value) return 'all'
  if (VIEW_OPTIONS.some((item) => item.id === value)) return value as BrainView
  return 'all'
}

function tabFor(view: BrainView): Record<string, string> {
  return view === 'all' ? {} : { tab: view }
}

export function BrainPage() {
  const search = useSearch({ strict: false }) as { tab?: string }
  const navigate = useNavigate()
  const view = normalizeView(search.tab)
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [railOpen, setRailOpen] = useState(false)
  const [selectedDocumentId, setSelectedDocumentId] = useState('business')
  const [editingBusinessSection, setEditingBusinessSection] = useState<BusinessMdSection | null>(null)
  const [businessDraft, setBusinessDraft] = useState('')
  const [editingAgentSection, setEditingAgentSection] = useState<AgentDocumentSection | null>(null)
  const [agentDraft, setAgentDraft] = useState('')
  const objectsQuery = useBusinessBrainObjects()
  const businessMdQuery = useBusinessMdDocument()
  const upsertBusinessSection = useUpsertBusinessMdSection()
  const agentsQuery = useAgentWorkbenchAgents()
  const selectedAgentId = selectedDocumentId.startsWith('agent:')
    ? Number(selectedDocumentId.slice('agent:'.length))
    : null
  const agentDetailQuery = useAgentDetail(Number.isFinite(selectedAgentId) ? selectedAgentId : null)
  const upsertAgentSection = useUpsertAgentSection(Number.isFinite(selectedAgentId) ? selectedAgentId : null)
  const projection = objectsQuery.data
  const objects = projection?.objects ?? []
  const counts = projection?.counts ?? EMPTY_COUNTS
  const filteredObjects = useMemo(
    () => filterObjects(objects, view, query),
    [objects, query, view],
  )
  const selected =
    filteredObjects.find((item) => item.object_id === selectedId) ??
    filteredObjects[0] ??
    null

  function selectView(next: string) {
    navigate({
      to: '/brain',
      search: tabFor(next as BrainView),
      replace: true,
    })
    setSelectedId(null)
  }

  function selectObject(objectId: string) {
    setSelectedId(objectId)
    if (
      typeof window !== 'undefined'
      && typeof window.matchMedia === 'function'
      && !window.matchMedia('(min-width: 1024px)').matches
    ) {
      setRailOpen(true)
    }
  }

  return (
    <div className="grid h-full min-h-0 bg-background lg:grid-cols-[minmax(0,1fr)_320px]">
      <section className="flex min-h-0 flex-col border-r border-border/60">
        <header className="border-b border-border/60 px-6 py-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="text-xs text-muted-foreground">Brain</div>
              <h1 className="mt-1 text-xl font-semibold tracking-tight">Biznes haqiqati</h1>
              <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
                OQIM javob berishda ishlatadigan ma’lumotlar, dalillar va muammolar.
              </p>
            </div>
            <div className="grid min-w-[260px] grid-cols-3 gap-2 text-right">
              <SummaryMetric label="Tayyor" value={projection?.ready_count ?? 0} />
              <SummaryMetric label="Tekshiruv" value={projection?.review_count ?? 0} />
              <SummaryMetric label="Muammo" value={projection?.issues_count ?? 0} />
            </div>
          </div>

          <div className="mt-4 grid gap-3 xl:grid-cols-[minmax(0,1fr)_260px]">
            <div
              className="flex min-w-0 flex-wrap gap-1 rounded-lg bg-muted p-1"
              role="group"
              aria-label="Brain bo‘limlari"
            >
              {VIEW_OPTIONS.map((item) => {
                const selected = view === item.id
                return (
                  <Button
                    key={item.id}
                    type="button"
                    variant={selected ? 'secondary' : 'ghost'}
                    size="sm"
                    className={cn(
                      'h-7 gap-1.5 rounded-md px-2',
                      selected && 'bg-background text-foreground shadow-sm hover:bg-background',
                    )}
                    aria-pressed={selected}
                    onClick={() => selectView(item.id)}
                  >
                    <item.icon className="size-3.5" />
                    {item.label}
                    {DOMAIN_VIEW_IDS.has(item.id as BrainObjectDomain) ? (
                      <span className="ml-0.5 text-xs text-muted-foreground">
                        {counts[item.id as BrainObjectDomain] ?? 0}
                      </span>
                    ) : null}
                  </Button>
                )
              })}
            </div>

            <div className="flex min-w-0 items-center gap-2">
              <Search className="size-4 text-muted-foreground" />
              <Input
                value={query}
                onChange={(event: ChangeEvent<HTMLInputElement>) => setQuery(event.target.value)}
                placeholder="Nomi, dalili yoki holati"
                type="search"
                size="sm"
                aria-label="Brain qidirish"
              />
            </div>
          </div>
        </header>

        <ScrollArea className="min-h-0 flex-1">
          <div className="p-6">
            {view === 'documents' ? (
              <DocumentsPanel
                query={query}
                businessLoading={businessMdQuery.isLoading}
                businessError={Boolean(businessMdQuery.error)}
                businessSections={businessMdQuery.data?.sections ?? []}
                agentsLoading={agentsQuery.isLoading}
                agents={agentsQuery.data?.items ?? []}
                agentDetail={agentDetailQuery.data}
                agentLoading={agentDetailQuery.isLoading}
                agentError={Boolean(agentDetailQuery.error)}
                selectedDocumentId={selectedDocumentId}
                onSelectDocument={setSelectedDocumentId}
                onEditBusiness={(section) => {
                  setEditingBusinessSection(section)
                  setBusinessDraft(section.body)
                }}
                onEditAgent={(section) => {
                  setEditingAgentSection(section)
                  setAgentDraft(section.body)
                }}
              />
            ) : objectsQuery.isLoading ? (
              <BrainSkeleton />
            ) : objectsQuery.error ? (
              <BrainEmpty
                icon={TriangleAlert}
                title="Brain yuklanmadi"
                description="Sahifani yangilang. Muammo davom etsa, BI agentga yozing."
              />
            ) : filteredObjects.length === 0 ? (
              <BrainEmpty
                icon={FileText}
                title={objects.length ? 'Mos ma’lumot topilmadi' : 'Brain hali bo‘sh'}
                description={
                  objects.length
                    ? 'Qidiruvni o‘zgartiring yoki boshqa bo‘limni tanlang.'
                    : 'Fayl, sayt, Telegram kanal yoki qo‘lda yozilgan matn o‘qilganda natijalar dalili bilan shu yerda ko‘rinadi.'
                }
              />
            ) : (
              <BrainObjectTable
                objects={filteredObjects}
                selectedId={selected?.object_id ?? null}
                onSelect={selectObject}
              />
            )}
          </div>
        </ScrollArea>
      </section>

      <div className="fixed bottom-20 right-4 z-40 lg:hidden">
        <Button
          type="button"
          variant="default"
          size="sm"
          className="h-10 rounded-full px-3 shadow-lg"
          aria-label="Brain dalillarini ochish"
          onClick={() => setRailOpen(true)}
        >
          <PanelRightOpen className="size-4" />
          Dalil
        </Button>
      </div>

      <Sheet open={railOpen} onOpenChange={setRailOpen}>
        <SheetContent className="w-[min(100vw,24rem)] p-0 sm:max-w-sm" showCloseButton={false}>
          <SheetHeader className="sr-only">
            <SheetTitle>Brain dalillari</SheetTitle>
            <SheetDescription>
              Tanlangan Brain obyekti, dalillari, muammolari va hujjat holati.
            </SheetDescription>
          </SheetHeader>
          <BrainRightRail
            projection={projection}
            selected={selected}
            onSelect={selectObject}
            mode={view === 'documents' ? 'documents' : 'objects'}
            variant="sheet"
            documentSummary={{
              businessSections: businessMdQuery.data?.sections.length ?? 0,
              agentCount: agentsQuery.data?.items.length ?? 0,
              activeAgentCount: (agentsQuery.data?.items ?? []).filter((agent) => agent.is_active).length,
              skillCount: (agentsQuery.data?.items ?? []).reduce((total, agent) => total + agent.skill_count, 0),
            }}
          />
        </SheetContent>
      </Sheet>

      <BrainRightRail
        projection={projection}
        selected={selected}
        onSelect={selectObject}
        mode={view === 'documents' ? 'documents' : 'objects'}
        documentSummary={{
          businessSections: businessMdQuery.data?.sections.length ?? 0,
          agentCount: agentsQuery.data?.items.length ?? 0,
          activeAgentCount: (agentsQuery.data?.items ?? []).filter((agent) => agent.is_active).length,
          skillCount: (agentsQuery.data?.items ?? []).reduce((total, agent) => total + agent.skill_count, 0),
        }}
      />

      <Sheet
        open={editingBusinessSection !== null}
        onOpenChange={(open) => !open && setEditingBusinessSection(null)}
      >
        <SheetContent className="w-full sm:max-w-xl">
          <SheetHeader>
            <SheetTitle>{editingBusinessSection?.title ?? 'BUSINESS.md'}</SheetTitle>
            <SheetDescription>
              Bu bo‘lim agentlarga beriladigan biznes kontekstiga kiradi.
            </SheetDescription>
          </SheetHeader>
          <div className="px-6">
            <Textarea
              value={businessDraft}
              onChange={(event) => setBusinessDraft(event.target.value)}
              size="lg"
              rows={10}
              aria-label="BUSINESS.md bo‘limi"
            />
          </div>
          <SheetFooter>
            <Button variant="outline" onClick={() => setEditingBusinessSection(null)}>
              Bekor
            </Button>
            <Button
              loading={upsertBusinessSection.isPending}
              onClick={() => {
                if (!editingBusinessSection) return
                upsertBusinessSection.mutate(
                  {
                    section_key: editingBusinessSection.section_key,
                    title: editingBusinessSection.title,
                    body: businessDraft.trim(),
                    order_index: editingBusinessSection.order_index,
                    generated_by: 'owner',
                  },
                  { onSuccess: () => setEditingBusinessSection(null) },
                )
              }}
            >
              Saqlash
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>

      <Sheet
        open={editingAgentSection !== null}
        onOpenChange={(open) => !open && setEditingAgentSection(null)}
      >
        <SheetContent className="w-full sm:max-w-xl">
          <SheetHeader>
            <SheetTitle>{editingAgentSection?.title ?? 'AGENT.md'}</SheetTitle>
            <SheetDescription>
              Bu bo‘lim agentning egaga ko‘rinadigan ish hujjatiga kiradi. Ruxsat va triggerlar alohida policy orqali ishlaydi.
            </SheetDescription>
          </SheetHeader>
          <div className="px-6">
            <Textarea
              value={agentDraft}
              onChange={(event) => setAgentDraft(event.target.value)}
              size="lg"
              rows={10}
              aria-label="AGENT.md bo‘limi"
            />
          </div>
          <SheetFooter>
            <Button variant="outline" onClick={() => setEditingAgentSection(null)}>
              Bekor
            </Button>
            <Button
              loading={upsertAgentSection.isPending}
              onClick={() => {
                if (!editingAgentSection) return
                upsertAgentSection.mutate(
                  {
                    section_key: editingAgentSection.section_key,
                    title: editingAgentSection.title,
                    body: agentDraft.trim(),
                    order_index: editingAgentSection.order_index,
                  },
                  { onSuccess: () => setEditingAgentSection(null) },
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

type BrainRailVariant = 'desktop' | 'sheet'

function BrainObjectTable({
  objects,
  selectedId,
  onSelect,
}: {
  objects: BrainObjectItem[]
  selectedId: string | null
  onSelect: (objectId: string) => void
}) {
  return (
    <Table variant="card" aria-label="Brain obyektlari">
      <TableHeader>
        <TableRow>
          <TableHead>Obyekt</TableHead>
          <TableHead className="w-[150px]">Bo‘lim</TableHead>
          <TableHead className="w-[160px]">Holat</TableHead>
          <TableHead className="w-[150px]">Dalil</TableHead>
          <TableHead className="w-[96px] text-right">Ishonch</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {objects.map((item) => (
          <TableRow
            key={item.object_id}
            data-state={selectedId === item.object_id ? 'selected' : undefined}
            className="cursor-pointer"
            onClick={() => onSelect(item.object_id)}
          >
            <TableCell className="whitespace-normal py-4">
              <div className="flex min-w-0 items-start gap-3">
                <DomainIcon domain={item.domain} />
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{item.title}</div>
                  <div className="mt-1 line-clamp-2 max-w-2xl text-sm leading-5 text-muted-foreground">
                    {item.summary}
                  </div>
                </div>
              </div>
            </TableCell>
            <TableCell>
              <Badge variant="outline">{domainLabel(item.domain)}</Badge>
            </TableCell>
            <TableCell>
              <Badge variant={statusVariant(item.status)}>
                {item.status_label}
              </Badge>
            </TableCell>
            <TableCell>
              <EvidencePreview evidence={item.evidence} count={item.evidence_count} />
            </TableCell>
            <TableCell className="text-right font-medium">
              {Math.round(item.confidence * 100)}%
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function BrainRightRail({
  projection,
  selected,
  onSelect,
  mode = 'objects',
  variant = 'desktop',
  documentSummary,
}: {
  projection: BrainObjectProjection | undefined
  selected: BrainObjectItem | null
  onSelect: (objectId: string) => void
  mode?: 'objects' | 'documents'
  variant?: BrainRailVariant
  documentSummary?: {
    businessSections: number
    agentCount: number
    activeAgentCount: number
    skillCount: number
  }
}) {
  const issues = (projection?.objects ?? [])
    .filter((item) => item.needs_review || item.status === 'conflict' || item.status === 'degraded')
    .slice(0, 4)

  return (
    <aside
      className={cn(
        'min-h-0 flex-col bg-foreground/[0.015]',
        variant === 'desktop'
          ? 'hidden lg:flex'
          : 'flex h-full w-full',
      )}
    >
      <div className="border-b border-border/60 px-5 py-4">
        <div className="text-sm font-medium">BI yordamchi</div>
        <p className="mt-1 text-sm leading-5 text-muted-foreground">
          Brainni tartiblash, muammoni tushuntirish va keyingi amalni taklif qilish uchun.
        </p>
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-5 p-5">
          {mode === 'documents' ? (
            <section>
              <div className="text-xs font-medium text-muted-foreground">Hujjatlar holati</div>
              <div className="mt-3 overflow-hidden rounded-lg border border-border/70 bg-background">
                <RailStatRow label="BUSINESS.md" value={`${documentSummary?.businessSections ?? 0} bo‘lim`} />
                <RailStatRow label="AGENT.md" value={`${documentSummary?.activeAgentCount ?? 0}/${documentSummary?.agentCount ?? 0} faol`} />
                <RailStatRow label="SKILL.md" value={`${documentSummary?.skillCount ?? 0} ko‘nikma`} />
              </div>
              <p className="mt-3 text-sm leading-6 text-muted-foreground">
                Bu hujjatlar agent promptining o‘qiladigan qismi. Haqiqiy bajarish ruxsat, trigger va audit bilan cheklanadi.
              </p>
            </section>
          ) : (
            <section>
              <div className="text-xs font-medium text-muted-foreground">Tanlangan yozuv</div>
              {selected ? (
                <div className="mt-3 space-y-4">
                  <div>
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="text-sm font-semibold">{selected.title}</div>
                        <div className="mt-1 text-sm leading-5 text-muted-foreground">
                          {selected.summary}
                        </div>
                      </div>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <Badge variant={statusVariant(selected.status)}>
                        {selected.status_label}
                      </Badge>
                      <Badge variant="outline">{lifecycleLabel(selected.source_lifecycle)}</Badge>
                    </div>
                  </div>

                  <Separator />

                  <div>
                    <div className="text-xs font-medium text-muted-foreground">Dalillar</div>
                    <div className="mt-2 space-y-2">
                      {selected.evidence.length ? (
                        selected.evidence.map((item, index) => (
                          <EvidenceLine key={`${item.label}:${index}`} evidence={item} />
                        ))
                      ) : (
                        <div className="rounded-lg border border-border/70 bg-background px-3 py-3 text-sm leading-5 text-muted-foreground">
                          Dalil hali bog‘lanmagan. OQIM buni agentga tayyor bilim sifatida bermaydi.
                        </div>
                      )}
                    </div>
                  </div>

                  <Separator />

                  <div className="text-sm leading-5 text-muted-foreground">
                    O‘zgartirishlar BI agent yoki review navbati orqali taklif sifatida
                    kiradi. Riskli ma’lumotlar egasi tasdig‘isiz agent javobiga kirmaydi.
                  </div>
                </div>
              ) : (
                <div className="mt-3 text-sm text-muted-foreground">
                  Jadvaldan obyekt tanlang.
                </div>
              )}
            </section>
          )}

          <Separator />

          <section>
            <div className="flex items-center justify-between gap-3">
              <div className="text-xs font-medium text-muted-foreground">E’tibor kerak</div>
              <Badge variant={issues.length ? 'warning' : 'success'}>{issues.length}</Badge>
            </div>
            <div className="mt-3 space-y-2">
              {issues.length ? (
                issues.map((item) => (
                  <button
                    key={item.object_id}
                    type="button"
                    onClick={() => onSelect(item.object_id)}
                    className="block w-full rounded-lg border border-border/70 bg-background px-3 py-2 text-left transition-colors hover:bg-foreground/[0.03]"
                  >
                    <div className="truncate text-sm font-medium">{item.title}</div>
                    <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                      <span>{domainLabel(item.domain)}</span>
                      <span>·</span>
                      <span>{item.status_label}</span>
                    </div>
                  </button>
                ))
              ) : (
                <div className="rounded-lg border border-border/70 bg-background px-3 py-3 text-sm text-muted-foreground">
                  Hozircha ochiq muammo yo‘q.
                </div>
              )}
            </div>
          </section>
        </div>
      </ScrollArea>
    </aside>
  )
}

function RailStatRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border/70 px-3 py-2.5 last:border-b-0">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="text-sm font-medium">{value}</span>
    </div>
  )
}

function BusinessMdPanel({
  loading,
  error,
  sections,
  onEdit,
}: {
  loading: boolean
  error: boolean
  sections: BusinessMdSection[]
  onEdit: (section: BusinessMdSection) => void
}) {
  if (loading) return <BrainSkeleton />
  if (error) {
    return (
      <BrainEmpty
        icon={TriangleAlert}
        title="BUSINESS.md ochilmadi"
        description="Biznes hujjatini yuklab bo‘lmadi. Qayta urinib ko‘ring."
      />
    )
  }
  if (!sections.length) {
    return (
      <BrainEmpty
        icon={FileText}
        title="BUSINESS.md hali bo‘sh"
        description="Onboarding yoki BI agent biznes kontekstini yaratganda bo‘limlar shu yerda ko‘rinadi."
      />
    )
  }
  const ordered = [...sections].sort((a, b) => a.order_index - b.order_index)
  return (
    <article className="overflow-hidden rounded-lg border border-border bg-background">
      <div className="border-b border-border px-5 py-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold">BUSINESS.md</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Workspace haqidagi kontekst. Agentlar buni statik biznes xotirasi sifatida ishlatadi.
            </p>
          </div>
          <Badge variant="outline">{ordered.length} bo‘lim</Badge>
        </div>
      </div>
      {ordered.map((section, index) => (
        <section key={section.id} className="px-5 py-5">
          {index > 0 ? <Separator className="-mt-5 mb-5" /> : null}
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <h3 className="text-sm font-semibold">{section.title}</h3>
              <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-foreground">
                {section.body || 'Bu bo‘lim hali to‘ldirilmagan.'}
              </p>
              <div className="mt-3 text-xs text-muted-foreground">
                {section.generated_by === 'owner' ? 'Ega tahrirlagan' : 'OQIM yaratgan'}
              </div>
            </div>
            <Button variant="outline" size="xs" onClick={() => onEdit(section)}>
              Tahrirlash
            </Button>
          </div>
        </section>
      ))}
    </article>
  )
}

function DocumentsPanel({
  query,
  businessLoading,
  businessError,
  businessSections,
  agentsLoading,
  agents,
  agentDetail,
  agentLoading,
  agentError,
  selectedDocumentId,
  onSelectDocument,
  onEditBusiness,
  onEditAgent,
}: {
  query: string
  businessLoading: boolean
  businessError: boolean
  businessSections: BusinessMdSection[]
  agentsLoading: boolean
  agents: AgentWorkbenchRow[]
  agentDetail: AgentDetailResponse | undefined
  agentLoading: boolean
  agentError: boolean
  selectedDocumentId: string
  onSelectDocument: (documentId: string) => void
  onEditBusiness: (section: BusinessMdSection) => void
  onEditAgent: (section: AgentDocumentSection) => void
}) {
  const filteredAgents = filterAgentsForDocuments(agents, query)
  const selectedAgent = selectedDocumentId.startsWith('agent:') ? agentDetail : undefined

  return (
    <div className="grid min-h-[620px] overflow-hidden rounded-lg border border-border bg-background xl:grid-cols-[280px_minmax(0,1fr)]">
      <aside className="border-b border-border bg-foreground/[0.015] xl:border-b-0 xl:border-r">
        <div className="border-b border-border px-4 py-4">
          <div className="text-sm font-semibold">Hujjatlar</div>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            OQIM agentlarga beradigan formatted hujjatlar va ko‘nikmalar.
          </p>
        </div>
        <div className="space-y-1 p-2">
          <DocumentNavButton
            active={selectedDocumentId === 'business'}
            title="BUSINESS.md"
            subtitle={`${businessSections.length} bo‘lim`}
            badge={businessLoading ? 'Yuklanmoqda' : businessSections.length ? 'Tayyor' : 'Bo‘sh'}
            onClick={() => onSelectDocument('business')}
          />
          <Separator className="my-2" />
          <div className="px-2 pb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            AGENT.md
          </div>
          {agentsLoading ? (
            <div className="space-y-2 px-2">
              {Array.from({ length: 4 }).map((_, index) => (
                <Skeleton key={index} className="h-12 rounded-md" />
              ))}
            </div>
          ) : filteredAgents.length ? (
            filteredAgents.map((agent) => (
              <DocumentNavButton
                key={agent.id}
                active={selectedDocumentId === `agent:${agent.id}`}
                title={agent.name}
                subtitle={`${agent.document_section_count} bo‘lim · ${agent.skill_count} ko‘nikma`}
                badge={agent.is_active ? 'Faol' : 'To‘xtagan'}
                onClick={() => onSelectDocument(`agent:${agent.id}`)}
              />
            ))
          ) : (
            <div className="px-3 py-4 text-sm leading-6 text-muted-foreground">
              Agent hujjati topilmadi.
            </div>
          )}
        </div>
      </aside>

      <section className="min-w-0">
        {selectedDocumentId === 'business' ? (
          <BusinessMdPanel
            loading={businessLoading}
            error={businessError}
            sections={filterBusinessSections(businessSections, query)}
            onEdit={onEditBusiness}
          />
        ) : agentLoading ? (
          <div className="p-5">
            <BrainSkeleton />
          </div>
        ) : agentError || !selectedAgent ? (
          <BrainEmpty
            icon={TriangleAlert}
            title="AGENT.md ochilmadi"
            description="Agent hujjatini yuklab bo‘lmadi. Qayta urinib ko‘ring."
          />
        ) : (
          <AgentDocumentPanel detail={selectedAgent} query={query} onEdit={onEditAgent} />
        )}
      </section>
    </div>
  )
}

function DocumentNavButton({
  active,
  title,
  subtitle,
  badge,
  onClick,
}: {
  active: boolean
  title: string
  subtitle: string
  badge: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      className={cn(
        'flex w-full items-center justify-between gap-3 rounded-md px-3 py-2.5 text-left transition-colors',
        active ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:bg-background/70',
      )}
      onClick={onClick}
    >
      <span className="min-w-0">
        <span className="block truncate text-sm font-medium">{title}</span>
        <span className="mt-0.5 block truncate text-xs text-muted-foreground">{subtitle}</span>
      </span>
      <Badge variant={active ? 'secondary' : 'outline'}>{badge}</Badge>
    </button>
  )
}

function AgentDocumentPanel({
  detail,
  query,
  onEdit,
}: {
  detail: AgentDetailResponse
  query: string
  onEdit: (section: AgentDocumentSection) => void
}) {
  const sections = filterAgentSections(detail.sections, query)
  const skills = filterAgentSkills(detail.skills, query)
  const hasQuery = query.trim().length > 0

  return (
    <article className="min-h-full bg-background">
      <div className="border-b border-border px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-sm font-semibold">{detail.agent.name}</div>
            <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
              AGENT.md, ko‘nikmalar, ruxsat va triggerlar bir joyda. Runtime structured sozlamani ishlatadi, bu esa egaga o‘qiladigan ko‘rinish.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant={detail.agent.is_active ? 'success' : 'outline'}>
              {detail.agent.is_active ? 'Faol' : 'To‘xtagan'}
            </Badge>
            <Badge variant="outline">{detail.rendered.sections_used} bo‘lim</Badge>
            <Badge variant="outline">{detail.skills.length} ko‘nikma</Badge>
          </div>
        </div>
      </div>

      <div className="grid gap-4 p-5 2xl:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0 overflow-hidden rounded-lg border border-border">
          <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
            <div className="flex items-center gap-2 text-sm font-medium">
              <FileText className="size-4" />
              AGENT.md
            </div>
            <Badge variant="outline">{sections.length} ko‘rinmoqda</Badge>
          </div>
          {sections.length ? (
            sections
              .sort((a, b) => a.order_index - b.order_index)
              .map((section, index) => (
                <section key={section.id} className="px-4 py-4">
                  {index > 0 ? <Separator className="-mt-4 mb-4" /> : null}
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                      <h3 className="text-sm font-semibold">{section.title}</h3>
                      <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-foreground">
                        {section.body || 'Bu bo‘lim hali to‘ldirilmagan.'}
                      </p>
                      <div className="mt-3 text-xs text-muted-foreground">
                        {section.generated_by === 'owner' ? 'Ega tahrirlagan' : 'OQIM yaratgan'}
                      </div>
                    </div>
                    <Button variant="outline" size="xs" onClick={() => onEdit(section)}>
                      Tahrirlash
                    </Button>
                  </div>
                </section>
              ))
          ) : (
            <div className="px-4 py-8 text-sm text-muted-foreground">
              {hasQuery ? 'Qidiruvga mos AGENT.md bo‘limi topilmadi.' : 'AGENT.md hali bo‘sh.'}
            </div>
          )}
        </div>

        <aside className="min-w-0 rounded-lg border border-border">
          <div className="border-b border-border px-4 py-3">
            <div className="text-sm font-medium">SKILL.md</div>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              Agent ishda chaqiradigan ko‘nikmalar. Har biri structured skill yozuvidan keladi.
            </p>
          </div>
          <div className="divide-y divide-border">
            {skills.length ? (
              skills.map((skill) => (
                <div key={skill.id} className="px-4 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium">{skill.name}</div>
                      <div className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">
                        {skill.description || skill.slug}
                      </div>
                    </div>
                    <Badge variant={skill.enabled ? 'success' : 'outline'}>
                      {skill.enabled ? 'Yoqilgan' : 'O‘chiq'}
                    </Badge>
                  </div>
                  {skill.when_to_use ? (
                    <div className="mt-3 rounded-md bg-muted px-3 py-2 text-xs leading-5">
                      {skill.when_to_use}
                    </div>
                  ) : null}
                </div>
              ))
            ) : (
              <div className="px-4 py-8 text-sm text-muted-foreground">
                {hasQuery ? 'Qidiruvga mos ko‘nikma topilmadi.' : 'Bu agentga ko‘nikma biriktirilmagan.'}
              </div>
            )}
          </div>
        </aside>
      </div>
    </article>
  )
}

function BrainSkeleton() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 6 }).map((_, index) => (
        <div key={index} className="rounded-xl border border-border bg-background p-4">
          <div className="flex items-center gap-3">
            <Skeleton className="size-8 rounded-lg" />
            <div className="flex-1 space-y-2">
              <Skeleton className="h-4 w-48" />
              <Skeleton className="h-3 w-2/3" />
            </div>
            <Skeleton className="h-5 w-20" />
          </div>
        </div>
      ))}
    </div>
  )
}

function BrainEmpty({
  icon: Icon,
  title,
  description,
}: {
  icon: LucideIcon
  title: string
  description: string
}) {
  return (
    <Empty className="min-h-[420px] rounded-xl border border-dashed border-border">
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <Icon className="size-5" />
        </EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        <EmptyDescription>{description}</EmptyDescription>
      </EmptyHeader>
      <EmptyContent>
        <Badge variant="outline">OQIM o‘rganishni davom ettiradi</Badge>
      </EmptyContent>
    </Empty>
  )
}

function SummaryMetric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-border/70 bg-background px-3 py-2">
      <div className="text-lg font-semibold leading-none">{value}</div>
      <div className="mt-1 text-xs text-muted-foreground">{label}</div>
    </div>
  )
}

function DomainIcon({ domain }: { domain: BrainObjectDomain }) {
  const Icon = domainIcon(domain)
  return (
    <span className="flex size-8 shrink-0 items-center justify-center rounded-lg border border-border/70 bg-background">
      <Icon className="size-4 text-muted-foreground" />
    </span>
  )
}

function EvidencePreview({
  evidence,
  count,
}: {
  evidence: BrainObjectEvidence[]
  count: number
}) {
  if (!evidence.length) {
    return <span className="text-sm text-muted-foreground">Dalil yo‘q</span>
  }
  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <Badge variant="outline" className="max-w-[220px] truncate">
        {evidenceTitle(evidence[0])}
      </Badge>
      {count > 1 ? (
        <span className="text-xs text-muted-foreground">+{count - 1}</span>
      ) : null}
    </div>
  )
}

function EvidenceLine({ evidence }: { evidence: BrainObjectEvidence }) {
  const detail = evidenceSubtitle(evidence)
  return (
    <div className="group border-b border-border/60 py-3 last:border-b-0">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium">{evidenceTitle(evidence)}</div>
          {detail ? (
            <div className="mt-1 line-clamp-2 text-sm leading-5 text-muted-foreground">
              {detail}
            </div>
          ) : null}
        </div>
        <span className="shrink-0 rounded-md border border-border/70 px-2 py-0.5 text-xs text-muted-foreground">
          {evidence.freshness_label}
        </span>
      </div>
    </div>
  )
}

function evidenceTitle(evidence: BrainObjectEvidence) {
  const unit = friendlyEvidenceUnit(evidence.unit_label)
  return unit ? `${evidence.label} · ${unit}` : evidence.label
}

function evidenceSubtitle(evidence: BrainObjectEvidence) {
  const detail = evidence.detail?.trim()
  if (detail && detail !== evidence.label) return detail
  return null
}

function friendlyEvidenceUnit(value: string | null | undefined) {
  const text = String(value || '').trim()
  if (!text) return null
  if (/^bo‘lak\s+0*\d+$/i.test(text)) return 'matn bo‘lagi'
  if (/^chunk\s+0*\d+$/i.test(text)) return 'matn bo‘lagi'
  if (/^unit\s+0*\d+$/i.test(text)) return 'matn bo‘lagi'
  return text
}

function filterObjects(objects: BrainObjectItem[], view: BrainView, query: string) {
  const needle = query.trim().toLowerCase()
  return objects.filter((item) => {
    if (view === 'documents') return false
    if (view !== 'all' && item.domain !== view) return false
    if (!needle) return true
    const haystack = [
      item.title,
      item.summary,
      item.status_label,
      domainLabel(item.domain),
      ...item.evidence.map((evidence) =>
        [evidence.label, evidence.detail, evidence.unit_label].join(' '),
      ),
    ].join(' ').toLowerCase()
    return haystack.includes(needle)
  })
}

function filterBusinessSections(sections: BusinessMdSection[], query: string) {
  const needle = query.trim().toLowerCase()
  const ordered = [...sections].sort((a, b) => a.order_index - b.order_index)
  if (!needle) return ordered
  return ordered.filter((section) =>
    [section.title, section.body, section.generated_by, section.section_key]
      .join(' ')
      .toLowerCase()
      .includes(needle),
  )
}

function filterAgentsForDocuments(agents: AgentWorkbenchRow[], query: string) {
  const needle = query.trim().toLowerCase()
  if (!needle) return agents
  return agents.filter((agent) =>
    [
      agent.name,
      agent.agent_type,
      agent.package_key,
      agent.permission_mode,
      agentTypeLabelForBrain(agent.agent_type),
    ]
      .join(' ')
      .toLowerCase()
      .includes(needle),
  )
}

function filterAgentSections(sections: AgentDocumentSection[], query: string) {
  const needle = query.trim().toLowerCase()
  if (!needle) return [...sections]
  return sections.filter((section) =>
    [section.title, section.body, section.generated_by, section.section_key]
      .join(' ')
      .toLowerCase()
      .includes(needle),
  )
}

function filterAgentSkills(skills: AgentDetailResponse['skills'], query: string) {
  const needle = query.trim().toLowerCase()
  if (!needle) return skills
  return skills.filter((skill) =>
    [
      skill.name,
      skill.slug,
      skill.description,
      skill.instructions,
      skill.when_to_use,
      skill.when_not_to_use,
      ...skill.tools,
    ]
      .join(' ')
      .toLowerCase()
      .includes(needle),
  )
}

function domainIcon(domain: BrainObjectDomain): LucideIcon {
  return {
    catalog: Boxes,
    knowledge: BookOpen,
    rules: ShieldCheck,
    voice: Sparkles,
    examples: MessageSquareText,
    issues: CircleAlert,
    sources: Database,
  }[domain]
}

function domainLabel(domain: BrainObjectDomain): string {
  return {
    catalog: 'Katalog',
    knowledge: 'Bilim',
    rules: 'Qoida',
    voice: 'Ovoz',
    examples: 'Namuna',
    issues: 'Muammo',
    sources: 'Dalillar',
  }[domain]
}

function agentTypeLabelForBrain(value: string) {
  const labels: Record<string, string> = {
    seller: 'Sotuvchi',
    support: 'Support',
    catalog_update: 'Katalog yangilash',
    follow_up: 'Qayta aloqa',
    bi: 'BI yordamchi',
    custom: 'Maxsus agent',
  }
  return labels[value] ?? value.replaceAll('_', ' ')
}

function statusVariant(status: BrainObjectState): 'success' | 'warning' | 'error' | 'outline' {
  if (status === 'ready') return 'success'
  if (status === 'needs_review') return 'warning'
  if (status === 'conflict' || status === 'degraded') return 'error'
  return 'outline'
}

function lifecycleLabel(lifecycle: BrainObjectSourceLifecycle): string {
  return {
    live: 'Jonli',
    snapshot: 'Nusxa',
    expired: 'Eskirgan',
    archived: 'Arxiv',
    conflicting: 'Zid ma’lumot',
    failed: 'O‘qilmadi',
    retrying: 'Qayta urinadi',
  }[lifecycle]
}
