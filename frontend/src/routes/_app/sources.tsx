import { useMemo, useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import { useNavigate, useSearch } from '@tanstack/react-router'
import {
  Archive,
  CheckCircle2,
  Database,
  FileText,
  Globe2,
  Image,
  MessageCircle,
  Mic,
  PauseCircle,
  PlayCircle,
  Plus,
  RefreshCcw,
  Search,
  ShieldAlert,
  Sparkles,
  TriangleAlert,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from '@/components/ui/empty'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
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
  useBusinessBrainFacts,
  useBusinessBrainSourceIntake,
  useBusinessBrainSourceControl,
  useCreateBusinessBrainSource,
  useRetryBusinessBrainSourceLearning,
  useRunBusinessBrainSourceLearning,
} from '@/hooks/use-business-brain'
import type { BusinessBrainSourceCreateInput } from '@/lib/types'
import type {
  BusinessBrainFactReadModel,
  SourceIntakeItem,
  SourceIntakeLifecycle,
  SourceIntakeProjection,
} from '@/lib/types'
import { cn } from '@/lib/utils'

type SourceView = SourceIntakeLifecycle | 'all'

const SOURCE_KIND_OPTIONS = [
  { value: 'telegram_channel', label: 'Telegram kanal' },
  { value: 'website', label: 'Sayt' },
  { value: 'file', label: 'Fayl yoki rasm' },
  { value: 'text', label: 'Qo‘lda ma’lumot' },
  { value: 'voice_note', label: 'Ovoz matni' },
] as const

const VIEW_OPTIONS: {
  id: SourceView
  label: string
  icon: LucideIcon
}[] = [
  { id: 'all', label: 'Hammasi', icon: Database },
  { id: 'live', label: 'Jonli', icon: CheckCircle2 },
  { id: 'snapshot', label: 'Nusxa', icon: FileText },
  { id: 'learning', label: 'O‘qilmoqda', icon: Sparkles },
  { id: 'needs_review', label: 'Tekshiruv', icon: ShieldAlert },
  { id: 'failed', label: 'Yordam', icon: TriangleAlert },
  { id: 'conflicting', label: 'Zid', icon: ShieldAlert },
  { id: 'archived', label: 'Arxiv', icon: Archive },
]

const KIND_ORDER = [
  'telegram_channel',
  'telegram_history',
  'website',
  'file',
  'image',
  'voice',
  'manual',
  'integration',
  'source',
]

const KNOWN_VIEWS = new Set(VIEW_OPTIONS.map((item) => item.id))

function normalizeView(value: string | undefined): SourceView {
  return value && KNOWN_VIEWS.has(value as SourceView) ? (value as SourceView) : 'all'
}

function sourceSearch(view: SourceView, kind?: string): Record<string, string> {
  const next: Record<string, string> = {}
  if (view !== 'all') next.lifecycle = view
  if (kind) next.kind = kind
  return next
}

export function SourcesPage() {
  const search = useSearch({ strict: false }) as { lifecycle?: string; kind?: string }
  const navigate = useNavigate()
  const view = normalizeView(search.lifecycle)
  const kind = typeof search.kind === 'string' && search.kind ? search.kind : ''
  const [query, setQuery] = useState('')
  const [selectedRef, setSelectedRef] = useState<string | null>(null)
  const sourcesQuery = useBusinessBrainSourceIntake()
  const createMutation = useCreateBusinessBrainSource()
  const learnMutation = useRunBusinessBrainSourceLearning()
  const retryMutation = useRetryBusinessBrainSourceLearning()
  const controlMutation = useBusinessBrainSourceControl()
  const factsQuery = useBusinessBrainFacts()
  const projection = sourcesQuery.data
  const sources = projection?.sources ?? []
  const filteredSources = useMemo(
    () => filterSources(sources, view, kind, query),
    [kind, query, sources, view],
  )
  const selected =
    filteredSources.find((item) => item.source_ref === selectedRef) ??
    filteredSources[0] ??
    null
  const kindOptions = useMemo(() => sourceKindOptions(projection), [projection])

  function selectView(next: SourceView) {
    navigate({ to: '/sources', search: sourceSearch(next, kind), replace: true })
    setSelectedRef(null)
  }

  function selectKind(next: string) {
    navigate({ to: '/sources', search: sourceSearch(view, next === kind ? '' : next), replace: true })
    setSelectedRef(null)
  }

  function retrySelected(source: SourceIntakeItem) {
    retryMutation.mutate({ source_ref: source.source_ref, limit: 1, max_attempts: 1 })
  }

  async function createSource(payload: BusinessBrainSourceCreateInput) {
    const result = await createMutation.mutateAsync(payload)
    setSelectedRef(result.source_ref)
    await learnMutation.mutateAsync({ limit: 1, max_attempts: 2 })
  }

  function controlSource(source: SourceIntakeItem, action: 'archive' | 'pause' | 'resume') {
    controlMutation.mutate({
      source_ref: source.source_ref,
      action,
      idempotency_key: `source-control:${action}:${source.source_ref}`,
    })
  }

  return (
    <div className="grid h-full min-h-0 bg-background lg:grid-cols-[minmax(0,1fr)_320px]">
      <section className="flex min-h-0 flex-col border-r border-border/60">
        <header className="border-b border-border/60 px-6 py-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="text-xs text-muted-foreground">Sources</div>
              <h1 className="mt-1 text-xl font-semibold tracking-tight">Manbalar</h1>
              <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
                Fayl, sayt, Telegram, rasm va ovoz qayerdan o‘qilganini ko‘ring.
              </p>
            </div>
            <div className="flex min-w-[260px] items-start justify-end gap-2">
              <div className="grid grid-cols-3 gap-2 text-right">
                <SummaryMetric label="Jonli" value={projection?.live_count ?? 0} />
                <SummaryMetric label="Tekshiruv" value={projection?.review_count ?? 0} />
                <SummaryMetric label="Yordam" value={projection?.failed_count ?? 0} />
              </div>
              <AddSourceDialog
                onCreate={createSource}
                submitting={createMutation.isPending || learnMutation.isPending}
              />
            </div>
          </div>

          <div className="mt-4 grid gap-3 xl:grid-cols-[minmax(0,1fr)_260px]">
            <div
              className="flex min-w-0 flex-wrap gap-1 rounded-lg bg-muted p-1"
              role="group"
              aria-label="Manba holatlari"
            >
              {VIEW_OPTIONS.map((item) => {
                const selected = view === item.id
                const count = item.id === 'all'
                  ? sources.length
                  : projection?.counts[item.id as SourceIntakeLifecycle] ?? 0
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
                    <span className="ml-0.5 text-xs text-muted-foreground">{count}</span>
                  </Button>
                )
              })}
            </div>

            <div className="flex min-w-0 items-center gap-2">
              <Search className="size-4 text-muted-foreground" />
              <Input
                value={query}
                onChange={(event: ChangeEvent<HTMLInputElement>) => setQuery(event.target.value)}
                placeholder="Manba, natija yoki holat"
                type="search"
                size="sm"
                aria-label="Manbalarni qidirish"
              />
            </div>
          </div>

          {kindOptions.length ? (
            <div className="mt-3 flex min-w-0 flex-wrap gap-1.5" aria-label="Manba turlari">
              {kindOptions.map((item) => {
                const active = kind === item.kind
                return (
                  <Button
                    key={item.kind}
                    type="button"
                    variant={active ? 'secondary' : 'outline'}
                    size="xs"
                    className={cn('gap-1.5 rounded-md', active && 'bg-foreground text-background hover:bg-foreground/90')}
                    aria-pressed={active}
                    onClick={() => selectKind(item.kind)}
                  >
                    <SourceKindIcon kind={item.kind} className="size-3.5" />
                    {item.label}
                    <span className="text-xs opacity-70">{item.count}</span>
                  </Button>
                )
              })}
            </div>
          ) : null}
        </header>

        <ScrollArea className="min-h-0 flex-1">
          <div className="p-6">
            {sourcesQuery.isLoading ? (
              <SourcesSkeleton />
            ) : sourcesQuery.error ? (
              <SourcesEmpty
                icon={TriangleAlert}
                title="Manbalar yuklanmadi"
                description="Sahifani yangilang. Muammo davom etsa, BI agentga yozing."
              />
            ) : filteredSources.length === 0 ? (
              <SourcesEmpty
                icon={FileText}
                title={sources.length ? 'Mos manba topilmadi' : 'Hali manba yo‘q'}
                description={
                  sources.length
                    ? 'Qidiruv yoki filtrni o‘zgartiring.'
                    : 'Fayl, sayt, Telegram yoki ovoz qo‘shilganda o‘qish holati shu yerda ko‘rinadi.'
                }
              />
            ) : (
              <SourceTable
                sources={filteredSources}
                selectedRef={selected?.source_ref ?? null}
                onSelect={setSelectedRef}
              />
            )}
          </div>
        </ScrollArea>
      </section>

      <SourcesRightRail
        selected={selected}
        projection={projection}
        facts={factsQuery.data?.items ?? []}
        retrying={retryMutation.isPending}
        controlling={controlMutation.isPending}
        onRetry={retrySelected}
        onControl={controlSource}
      />
    </div>
  )
}

function SourceTable({
  sources,
  selectedRef,
  onSelect,
}: {
  sources: SourceIntakeItem[]
  selectedRef: string | null
  onSelect: (sourceRef: string) => void
}) {
  return (
    <Table variant="card" aria-label="Manbalar jadvali">
      <TableHeader>
        <TableRow>
          <TableHead>Manba</TableHead>
          <TableHead className="w-[150px]">Turi</TableHead>
          <TableHead className="w-[150px]">Holat</TableHead>
          <TableHead className="w-[190px]">Natija</TableHead>
          <TableHead className="w-[120px] text-right">Dalil</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {sources.map((source) => (
          <TableRow
            key={source.source_ref}
            data-state={selectedRef === source.source_ref ? 'selected' : undefined}
            className="cursor-pointer"
            onClick={() => onSelect(source.source_ref)}
          >
            <TableCell className="whitespace-normal py-4">
              <div className="flex min-w-0 items-start gap-3">
                <span className="flex size-8 shrink-0 items-center justify-center rounded-lg border border-border/70 bg-background">
                  <SourceKindIcon kind={source.kind} className="size-4 text-muted-foreground" />
                </span>
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{source.title}</div>
                  <div className="mt-1 line-clamp-2 max-w-2xl text-sm leading-5 text-muted-foreground">
                    {source.summary}
                  </div>
                </div>
              </div>
            </TableCell>
            <TableCell>
              <Badge variant="outline">{source.kind_label}</Badge>
            </TableCell>
            <TableCell>
              <Badge variant={lifecycleVariant(source.lifecycle)}>{source.status_label}</Badge>
            </TableCell>
            <TableCell>
              <LearnedLabels source={source} />
            </TableCell>
            <TableCell className="text-right text-sm text-muted-foreground">
              {source.source_unit_count} matn · {source.media_count} media
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function SourcesRightRail({
  selected,
  projection,
  facts,
  retrying,
  controlling,
  onRetry,
  onControl,
}: {
  selected: SourceIntakeItem | null
  projection: SourceIntakeProjection | undefined
  facts: BusinessBrainFactReadModel[]
  retrying: boolean
  controlling: boolean
  onRetry: (source: SourceIntakeItem) => void
  onControl: (source: SourceIntakeItem, action: 'archive' | 'pause' | 'resume') => void
}) {
  const needsHelp = (projection?.sources ?? [])
    .filter((item) => item.lifecycle === 'failed' || item.lifecycle === 'conflicting' || item.lifecycle === 'needs_review')
    .slice(0, 4)
  const learnedObjects = selected ? learnedFactsForSource(selected, facts) : []
  const mediaItems = selected ? mediaFactsForSource(selected, facts) : []

  return (
    <aside className="hidden min-h-0 flex-col bg-foreground/[0.015] lg:flex">
      <div className="border-b border-border/60 px-5 py-4">
        <div className="text-sm font-medium">Manba nazorati</div>
        <p className="mt-1 text-sm leading-5 text-muted-foreground">
          OQIM nimani o‘qiganini, nimadan foydalanganini va qayerda yordam kerakligini ko‘rsating.
        </p>
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-5 p-5">
          <section>
            <div className="text-xs font-medium text-muted-foreground">Tanlangan manba</div>
            {selected ? (
              <div className="mt-3 space-y-4">
                <div>
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="text-sm font-semibold">{selected.title}</div>
                      <div className="mt-1 text-sm leading-5 text-muted-foreground">
                        {selected.preview}
                      </div>
                    </div>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Badge variant={lifecycleVariant(selected.lifecycle)}>{selected.status_label}</Badge>
                    <Badge variant="outline">{selected.purpose_label}</Badge>
                  </div>
                </div>

                <Separator />

                <div>
                  <div className="text-xs font-medium text-muted-foreground">O‘qilgan natija</div>
                  <div className="mt-2">
                    <LearnedLabels source={selected} expanded />
                  </div>
                </div>

                <div>
                  <div className="text-xs font-medium text-muted-foreground">Nimani o‘rgandi</div>
                  <div className="mt-2 space-y-2">
                    {learnedObjects.length ? (
                      learnedObjects.slice(0, 5).map((fact) => (
                        <div key={fact.fact_id} className="rounded-lg border border-border/70 bg-background px-3 py-2">
                          <div className="flex items-center justify-between gap-2">
                            <div className="truncate text-sm font-medium">{learnedFactTitle(fact)}</div>
                            <Badge variant={fact.status === 'proposed' ? 'warning' : 'outline'}>
                              {learnedFactKindLabel(fact.fact_type)}
                            </Badge>
                          </div>
                          <div className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">
                            {learnedFactSummary(fact)}
                          </div>
                        </div>
                      ))
                    ) : (
                      <div className="rounded-lg border border-border/70 bg-background px-3 py-3 text-sm text-muted-foreground">
                        Bu manbadan hali ochiladigan bilim topilmadi.
                      </div>
                    )}
                    {learnedObjects.length > 5 ? (
                      <div className="text-xs text-muted-foreground">
                        Yana {learnedObjects.length - 5} ta obyekt Brain sahifasida ko‘rinadi.
                      </div>
                    ) : null}
                  </div>
                </div>

                {mediaItems.length ? (
                  <div>
                    <div className="text-xs font-medium text-muted-foreground">Topilgan media</div>
                    <div className="mt-2 grid grid-cols-3 gap-2">
                      {mediaItems.slice(0, 6).map((media) => (
                        <figure key={media.fact_id} className="overflow-hidden rounded-lg border border-border bg-background">
                          <img
                            src={String(media.value.url ?? '')}
                            alt={String(media.value.alt_text || media.value.caption || 'Manba rasmi')}
                            className="aspect-square w-full object-cover"
                            loading="lazy"
                          />
                        </figure>
                      ))}
                    </div>
                    {mediaItems.length > 6 ? (
                      <div className="mt-2 text-xs text-muted-foreground">
                        Yana {mediaItems.length - 6} ta rasm yoki media topilgan.
                      </div>
                    ) : null}
                  </div>
                ) : null}

                <Separator />

                <div>
                  <div className="text-xs font-medium text-muted-foreground">Dalil</div>
                  <div className="mt-2 rounded-lg border border-border/70 bg-background px-3 py-3 text-sm text-muted-foreground">
                    {selected.source_unit_count} ta matn bo‘lagi va {selected.media_count} ta media topildi.
                  </div>
                </div>

                {selected.issue_label ? (
                  <>
                    <Separator />
                    <div className="rounded-lg border border-warning/30 bg-warning/5 px-3 py-3 text-sm leading-5 text-warning-foreground">
                      {selected.issue_label}
                    </div>
                  </>
                ) : null}

                <div className="grid gap-2">
                  {selected.can_retry ? (
                    <Button
                      variant="outline"
                      size="sm"
                      className="w-full justify-center"
                      loading={retrying}
                      onClick={() => onRetry(selected)}
                    >
                      <RefreshCcw className="size-4" />
                      Qayta o‘qish
                    </Button>
                  ) : null}
                  {selected.can_pause ? (
                    <Button
                      variant="outline"
                      size="sm"
                      className="w-full justify-center"
                      loading={controlling}
                      onClick={() => onControl(selected, 'pause')}
                    >
                      <PauseCircle className="size-4" />
                      Kuzatishni to‘xtatish
                    </Button>
                  ) : null}
                  {selected.can_resume ? (
                    <Button
                      variant="outline"
                      size="sm"
                      className="w-full justify-center"
                      loading={controlling}
                      onClick={() => onControl(selected, 'resume')}
                    >
                      <PlayCircle className="size-4" />
                      Qayta kuzatish
                    </Button>
                  ) : null}
                  {selected.can_archive ? (
                    <Button
                      variant="destructive-outline"
                      size="sm"
                      className="w-full justify-center"
                      loading={controlling}
                      onClick={() => onControl(selected, 'archive')}
                    >
                      <Archive className="size-4" />
                      Arxivlash
                    </Button>
                  ) : null}
                </div>
              </div>
            ) : (
              <div className="mt-3 text-sm text-muted-foreground">Jadvaldan manba tanlang.</div>
            )}
          </section>

          <Separator />

          <section>
            <div className="flex items-center justify-between gap-3">
              <div className="text-xs font-medium text-muted-foreground">E’tibor kerak</div>
              <Badge variant={needsHelp.length ? 'warning' : 'success'}>{needsHelp.length}</Badge>
            </div>
            <div className="mt-3 space-y-2">
              {needsHelp.length ? (
                needsHelp.map((item) => (
                  <div
                    key={item.source_ref}
                    className="rounded-lg border border-border/70 bg-background px-3 py-2"
                  >
                    <div className="truncate text-sm font-medium">{item.title}</div>
                    <div className="mt-1 text-xs text-muted-foreground">{item.status_label}</div>
                  </div>
                ))
              ) : (
                <div className="rounded-lg border border-border/70 bg-background px-3 py-3 text-sm text-muted-foreground">
                  Ochiq muammo yo‘q.
                </div>
              )}
            </div>
          </section>
        </div>
      </ScrollArea>
    </aside>
  )
}

function AddSourceDialog({
  onCreate,
  submitting,
}: {
  onCreate: (payload: BusinessBrainSourceCreateInput) => Promise<void>
  submitting: boolean
}) {
  const [open, setOpen] = useState(false)
  const [kind, setKind] = useState('telegram_channel')
  const [value, setValue] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState<string | null>(null)
  const selectedKindLabel = SOURCE_KIND_OPTIONS.find((item) => item.value === kind)?.label ?? kind

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    try {
      const payload = await sourcePayload({ kind, value, file })
      await onCreate(payload)
      setValue('')
      setFile(null)
      setKind('telegram_channel')
      setOpen(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Manbani qo‘shib bo‘lmadi.')
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button size="sm" />}>
        <Plus className="size-4" />
        Manba
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Manba qo‘shish</DialogTitle>
          <DialogDescription>
            OQIM o‘qiydigan fayl, sayt, Telegram yoki qo‘lda yozilgan ma’lumotni qo‘shing.
          </DialogDescription>
        </DialogHeader>
        <form className="space-y-4" onSubmit={submit}>
          <div className="space-y-2">
            <Label htmlFor="source-kind">Turi</Label>
            <Select value={kind} onValueChange={(next) => next && setKind(next)}>
              <SelectTrigger id="source-kind" className="w-full">
                <span className="truncate">{selectedKindLabel}</span>
              </SelectTrigger>
              <SelectContent>
                {SOURCE_KIND_OPTIONS.map((item) => (
                  <SelectItem key={item.value} value={item.value}>
                    {item.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {kind === 'file' ? (
            <div className="space-y-2">
              <Label htmlFor="source-file">Fayl</Label>
              <Input
                id="source-file"
                type="file"
                nativeInput
                onChange={(event: ChangeEvent<HTMLInputElement>) => setFile(event.target.files?.[0] ?? null)}
              />
              <p className="text-xs text-muted-foreground">PDF, jadval, rasm, audio yoki hujjat.</p>
            </div>
          ) : kind === 'text' || kind === 'voice_note' ? (
            <div className="space-y-2">
              <Label htmlFor="source-text">
                {kind === 'voice_note' ? 'Ovozdan olingan matn' : 'Ma’lumot'}
              </Label>
              <Textarea
                id="source-text"
                value={value}
                onChange={(event: ChangeEvent<HTMLTextAreaElement>) => setValue(event.target.value)}
                placeholder={
                  kind === 'voice_note'
                    ? 'Sotuvchi ohangi, qoida yoki izohni yozing'
                    : 'Narx, xizmat, qoida yoki kompaniya haqida ma’lumot'
                }
              />
            </div>
          ) : (
            <div className="space-y-2">
              <Label htmlFor="source-value">{kind === 'website' ? 'URL' : 'Kanal nomi'}</Label>
              <Input
                id="source-value"
                value={value}
                onChange={(event: ChangeEvent<HTMLInputElement>) => setValue(event.target.value)}
                placeholder={kind === 'website' ? 'https://example.uz' : '@kanal_nomi'}
              />
            </div>
          )}

          {error ? (
            <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
              {error}
            </div>
          ) : null}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Bekor qilish
            </Button>
            <Button type="submit" loading={submitting}>
              Qo‘shish
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function LearnedLabels({ source, expanded = false }: { source: SourceIntakeItem; expanded?: boolean }) {
  if (!source.learned_object_labels.length) {
    return <span className="text-sm text-muted-foreground">Hali natija yo‘q</span>
  }
  const labels = expanded ? source.learned_object_labels : source.learned_object_labels.slice(0, 2)
  return (
    <div className="flex min-w-0 flex-wrap gap-1.5">
      {labels.map((label) => (
        <Badge key={label} variant="outline">{label}</Badge>
      ))}
      {!expanded && source.learned_object_labels.length > labels.length ? (
        <span className="text-xs text-muted-foreground">+{source.learned_object_labels.length - labels.length}</span>
      ) : null}
    </div>
  )
}

function SourcesSkeleton() {
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

function SourcesEmpty({
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
        <Badge variant="outline">OQIM manbalarni uzluksiz o‘qiydi</Badge>
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

function sourceKindOptions(projection: SourceIntakeProjection | undefined) {
  const sources = projection?.sources ?? []
  return Object.entries(projection?.kind_counts ?? {})
    .map(([kind, count]) => ({
      kind,
      count,
      label: sources.find((item) => item.kind === kind)?.kind_label ?? kind,
    }))
    .sort((a, b) => {
      const rankA = KIND_ORDER.indexOf(a.kind)
      const rankB = KIND_ORDER.indexOf(b.kind)
      return (rankA === -1 ? 99 : rankA) - (rankB === -1 ? 99 : rankB)
    })
}

function learnedFactsForSource(source: SourceIntakeItem, facts: BusinessBrainFactReadModel[]) {
  return facts
    .filter((fact) => fact.fact_type !== 'business_source_fact')
    .filter((fact) => fact.fact_type !== 'business_source_media_fact')
    .filter((fact) => fact.source_refs.some((ref) => refsMatchSource(ref, source.source_ref)))
    .slice(0, 40)
}

function mediaFactsForSource(source: SourceIntakeItem, facts: BusinessBrainFactReadModel[]) {
  return facts
    .filter((fact) => fact.fact_type === 'business_source_media_fact')
    .filter((fact) => typeof fact.value.url === 'string' && fact.value.url.length > 0)
    .filter((fact) => fact.source_refs.some((ref) => refsMatchSource(ref, source.source_ref)))
    .slice(0, 24)
}

function refsMatchSource(ref: string, sourceRef: string) {
  return ref === sourceRef || ref.includes(sourceRef) || sourceRef.includes(ref)
}

function learnedFactTitle(fact: BusinessBrainFactReadModel) {
  return compactText(
    fact.value.title ??
      fact.value.name ??
      fact.value.topic ??
      fact.value.question ??
      fact.value.summary ??
      fact.value.answer ??
      fact.value.description ??
      fact.value.content,
    learnedFactKindLabel(fact.fact_type),
  )
}

function learnedFactSummary(fact: BusinessBrainFactReadModel) {
  const value = fact.value
  const text = compactText(
    value.answer ?? value.summary ?? value.description ?? value.content ?? value.text ?? value.rule,
    '',
  )
  if (text) return text
  const observations = Array.isArray(value.observations) ? value.observations : []
  if (observations.length) return observations.map((item) => String(item)).join(' · ')
  return `${Math.round(fact.confidence * 100)}% ishonch bilan topilgan.`
}

function learnedFactKindLabel(type: string) {
  if (type.startsWith('catalog_')) return 'Katalog'
  if (type.includes('voice')) return 'Ovoz'
  if (type.includes('rule') || type.includes('policy')) return 'Qoida'
  if (type.includes('pair') || type.includes('example')) return 'Namuna'
  if (type.includes('media')) return 'Media'
  return 'Bilim'
}

function compactText(value: unknown, fallback: string) {
  if (value === null || value === undefined) return fallback
  const text = String(value).replace(/\s+/g, ' ').trim()
  return text || fallback
}

function filterSources(
  sources: SourceIntakeItem[],
  view: SourceView,
  kind: string,
  query: string,
) {
  const needle = query.trim().toLowerCase()
  return sources.filter((source) => {
    if (view !== 'all' && source.lifecycle !== view) return false
    if (kind && source.kind !== kind) return false
    if (!needle) return true
    const haystack = [
      source.title,
      source.kind_label,
      source.status_label,
      source.summary,
      source.preview,
      source.purpose_label,
      ...source.learned_object_labels,
    ].join(' ').toLowerCase()
    return haystack.includes(needle)
  })
}

async function sourcePayload({
  kind,
  value,
  file,
}: {
  kind: string
  value: string
  file: File | null
}): Promise<BusinessBrainSourceCreateInput> {
  const trimmed = value.trim()
  if (kind === 'website') {
    if (!trimmed) throw new Error('Sayt manzilini kiriting.')
    return { kind, url: trimmed, label: trimmed }
  }
  if (kind === 'telegram_channel') {
    if (!trimmed) throw new Error('Telegram kanal nomini kiriting.')
    return { kind, handle: trimmed, label: trimmed }
  }
  if (kind === 'text') {
    if (!trimmed) throw new Error('Ma’lumot matnini yozing.')
    return { kind, text: trimmed, label: trimmed.slice(0, 80) }
  }
  if (kind === 'voice_note') {
    if (!trimmed) throw new Error('Ovozdan olingan matnni yozing.')
    return { kind, transcript: trimmed, label: 'Sotuvchi ovozi' }
  }
  if (!file) throw new Error('Fayl tanlang.')
  const contentBase64 = await readFileBase64(file)
  return {
    kind,
    label: file.name,
    file_name: file.name,
    content_type: file.type || 'application/octet-stream',
    content_base64: contentBase64,
    byte_size: file.size,
  }
}

function readFileBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error('Faylni o‘qib bo‘lmadi.'))
    reader.onload = () => {
      const result = String(reader.result || '')
      resolve(result.includes(',') ? result.split(',')[1] : result)
    }
    reader.readAsDataURL(file)
  })
}

function SourceKindIcon({ kind, className }: { kind: string; className?: string }) {
  const Icon = sourceKindIcon(kind)
  return <Icon className={className} />
}

function sourceKindIcon(kind: string): LucideIcon {
  if (kind === 'website') return Globe2
  if (kind === 'telegram_channel' || kind === 'telegram_history') return MessageCircle
  if (kind === 'voice') return Mic
  if (kind === 'image') return Image
  if (kind === 'file' || kind === 'manual') return FileText
  return Database
}

function lifecycleVariant(lifecycle: SourceIntakeLifecycle) {
  if (lifecycle === 'live' || lifecycle === 'snapshot') return 'success'
  if (lifecycle === 'needs_review' || lifecycle === 'retrying' || lifecycle === 'learning') return 'warning'
  if (lifecycle === 'failed' || lifecycle === 'conflicting') return 'error'
  return 'outline'
}
