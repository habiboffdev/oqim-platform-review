import { type ChangeEvent, type FormEvent, useMemo, useState } from 'react'
import { Link } from '@tanstack/react-router'
import {
  Check,
  ClockCounterClockwise,
  ListChecks,
  Sparkle,
  X,
} from '@phosphor-icons/react'
import { toast } from 'sonner'
import { useBICommandMutation } from '@/hooks/use-bi-promoter'
import {
  useAcceptOwnerTask,
  useCompleteOwnerTask,
  useDismissOwnerTask,
  useOwnerTasks,
  useSnoozeOwnerTask,
} from '@/hooks/use-owner-tasks'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
} from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { uz } from '@/lib/uz'
import type { BICommandRequest, OwnerTaskDueBucket, OwnerTaskItem, OwnerTaskKind } from '@/lib/types'

const BUCKETS: { id: OwnerTaskDueBucket; label: string }[] = [
  { id: 'today', label: 'Bugun' },
  { id: 'overdue', label: 'Kechikkan' },
  { id: 'upcoming', label: 'Keyingi' },
  { id: 'completed', label: 'Tugatilgan' },
]

const KIND_FILTERS: { id: OwnerTaskKind | 'all'; label: string }[] = [
  { id: 'all', label: 'Hammasi' },
  { id: 'meeting', label: 'Uchrashuv' },
  { id: 'delivery', label: 'Yetkazish' },
  { id: 'stock', label: 'Stok' },
  { id: 'call', label: "Qo'ng'iroq" },
  { id: 'payment', label: "To'lov" },
  { id: 'follow_up', label: 'Qayta yozish' },
]

const TASK_COMMAND_KINDS: { id: OwnerTaskKind; label: string }[] = [
  { id: 'business', label: 'Biznes ishi' },
  { id: 'meeting', label: 'Uchrashuv' },
  { id: 'delivery', label: 'Yetkazish' },
  { id: 'stock', label: 'Stok' },
  { id: 'call', label: "Qo'ng'iroq" },
  { id: 'payment', label: "To'lov" },
  { id: 'follow_up', label: 'Qayta yozish' },
]

export function TasksPage() {
  const tasks = useOwnerTasks()
  const accept = useAcceptOwnerTask()
  const complete = useCompleteOwnerTask()
  const dismiss = useDismissOwnerTask()
  const snooze = useSnoozeOwnerTask()
  const biCommand = useBICommandMutation()
  const [bucket, setBucket] = useState<OwnerTaskDueBucket>('today')
  const [kind, setKind] = useState<OwnerTaskKind | 'all'>('all')
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const items = tasks.data?.items ?? []
  const proposed = tasks.data?.proposed ?? []
  const counts = tasks.data?.counts ?? {}
  const visibleItems = useMemo(
    () => items.filter((item) => item.state !== 'proposed')
      .filter((item) => item.due_bucket === bucket)
      .filter((item) => kind === 'all' || item.kind === kind),
    [bucket, items, kind],
  )
  const selected = visibleItems.find((item) => item.task_id === selectedId)
    ?? visibleItems[0]
    ?? proposed[0]
    ?? null
  const busy = accept.isPending || complete.isPending || dismiss.isPending || snooze.isPending

  async function acceptTask(task: OwnerTaskItem) {
    try {
      await accept.mutateAsync(task.proposal_id)
      toast.success('Vazifa qabul qilindi.')
    } catch {
      toast.error('Qabul qilib bo‘lmadi. Qayta urinib ko‘ring.')
    }
  }

  async function completeTask(task: OwnerTaskItem) {
    try {
      await complete.mutateAsync(task.proposal_id)
      toast.success('Vazifa tugatildi.')
    } catch {
      toast.error('Tugatib bo‘lmadi. Qayta urinib ko‘ring.')
    }
  }

  async function dismissTask(task: OwnerTaskItem) {
    try {
      await dismiss.mutateAsync(task.proposal_id)
      toast.success('Vazifa rad etildi.')
    } catch {
      toast.error('Rad etib bo‘lmadi. Qayta urinib ko‘ring.')
    }
  }

  async function snoozeTask(task: OwnerTaskItem) {
    try {
      await snooze.mutateAsync({ proposalId: task.proposal_id })
      toast.success('Vazifa ertaga qaytariladi.')
    } catch {
      toast.error('Kechiktirib bo‘lmadi. Qayta urinib ko‘ring.')
    }
  }

  async function proposeReplyAction(task: OwnerTaskItem, replyText: string) {
    const cleanReply = replyText.trim().slice(0, 2000)
    if (!cleanReply || task.conversation_id <= 0 || task.customer_id <= 0) return
    await biCommand.mutateAsync({
      command_kind: 'create_reply_action',
      command_text: `Mijozga javob taklifi: ${cleanReply}`.slice(0, 2000),
      conversation_id: task.conversation_id,
      customer_id: task.customer_id,
      customer_label: task.customer_label,
      reply_text: cleanReply,
      source_proposal_id: task.proposal_id,
      correlation_id: `ui:tasks:bi-reply-action:${Date.now()}`,
    })
  }

  return (
    <div className="grid h-full min-h-0 bg-background lg:grid-cols-[minmax(0,1fr)_320px]">
      <section className="flex min-h-0 min-w-0 flex-col border-r border-border/60">
        <header className="border-b border-border/60 px-6 py-4">
          <div className="flex items-center justify-between gap-4">
            <div className="min-w-0">
              <div className="flex items-center gap-2.5">
                <ListChecks className="size-4 opacity-70" weight="thin" />
                <h1 className="truncate text-lg font-medium">{uz.workspaceUi.modules.tasks.label}</h1>
              </div>
              <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
                Egasi bajaradigan ishlar: uchrashuv, to‘lov, yetkazish, stok va qayta yozish.
              </p>
            </div>
            <Badge variant={proposed.length > 0 ? 'warning' : 'outline'}>
              {proposed.length > 0 ? `${proposed.length} ta taklif` : 'Taklif yo‘q'}
            </Badge>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Tabs value={bucket} onValueChange={(value) => setBucket(value as OwnerTaskDueBucket)}>
              <TabsList>
                {BUCKETS.map((item) => (
                  <TabsTrigger key={item.id} value={item.id}>
                    {item.label}
                    {Number(counts[item.id] ?? 0) > 0 ? (
                      <Badge variant="outline" size="sm">{counts[item.id]}</Badge>
                    ) : null}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>

            <div className="flex flex-wrap items-center gap-1.5">
              {KIND_FILTERS.map((item) => (
                <Button
                  key={item.id}
                  type="button"
                  variant={kind === item.id ? 'secondary' : 'ghost'}
                  size="sm"
                  onClick={() => setKind(item.id)}
                >
                  {item.label}
                </Button>
              ))}
            </div>
          </div>
        </header>

        {tasks.error ? (
          <div className="p-6">
            <Alert variant="destructive">
              <AlertTitle>Vazifalar yuklanmadi</AlertTitle>
              <AlertDescription>Aloqa tiklangach sahifani qayta oching.</AlertDescription>
            </Alert>
          </div>
        ) : (
          <ScrollArea className="min-h-0 flex-1">
            <div className="p-6">
              {tasks.isLoading ? (
                <TaskSkeleton />
              ) : visibleItems.length === 0 ? (
                <Empty className="py-20">
                  <EmptyHeader>
                    <EmptyMedia variant="icon">
                      <ListChecks />
                    </EmptyMedia>
                    <EmptyTitle>Bu bo‘limda vazifa yo‘q</EmptyTitle>
                    <EmptyDescription>
                      OQIM suhbatlardan ish topsa, avval o‘ng tomonda qabul qilish uchun ko‘rsatadi.
                    </EmptyDescription>
                  </EmptyHeader>
                </Empty>
              ) : (
                <div className="overflow-hidden rounded-lg border border-border/70">
                  <Table className="table-fixed">
                    <TableHeader>
                      <TableRow>
                        <TableHead>Vazifa</TableHead>
                        <TableHead className="w-[112px]">Muddat</TableHead>
                        <TableHead className="w-[108px] text-right">Amal</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {visibleItems.map((task) => (
                        <TaskTableRow
                          key={task.task_id}
                          task={task}
                          selected={selected?.task_id === task.task_id}
                          busy={busy}
                          onSelect={() => setSelectedId(task.task_id)}
                          onComplete={() => completeTask(task)}
                        />
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
            </div>
          </ScrollArea>
        )}
      </section>

      <aside className="hidden min-h-0 flex-col bg-foreground/[0.015] lg:flex">
        <div className="border-b border-border/60 px-4 py-4">
          <div className="flex items-center gap-2">
            <Sparkle className="size-4 opacity-70" weight="thin" />
            <h2 className="text-sm font-medium">BI yordamchi</h2>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            Egasi bajaradigan ishni taklifga aylantiradi.
          </p>
        </div>

        <ScrollArea className="min-h-0 flex-1">
          <div className="flex flex-col gap-5 p-4">
            <BITaskProposalForm
              pending={biCommand.isPending}
              onSubmit={(input) => biCommand.mutateAsync(input)}
            />
            <Separator />
            <TaskDetail
              task={selected}
              busy={busy}
              replyPending={biCommand.isPending}
              onAccept={acceptTask}
              onDismiss={dismissTask}
              onComplete={completeTask}
              onSnooze={snoozeTask}
              onMessage={proposeReplyAction}
            />
            <Separator />
            <ProposedTasks
              items={proposed}
              busy={busy}
              onAccept={acceptTask}
              onDismiss={dismissTask}
            />
          </div>
        </ScrollArea>
      </aside>
    </div>
  )
}

function BITaskProposalForm({
  pending,
  onSubmit,
}: {
  pending: boolean
  onSubmit: (input: BICommandRequest) => Promise<unknown>
}) {
  const [title, setTitle] = useState('')
  const [detail, setDetail] = useState('')
  const [customerLabel, setCustomerLabel] = useState('')
  const [dueAt, setDueAt] = useState('')
  const [taskKind, setTaskKind] = useState<OwnerTaskKind>('business')
  const selectedKind = TASK_COMMAND_KINDS.find((item) => item.id === taskKind)
  const canSubmit = title.trim().length >= 2 && detail.trim().length >= 2 && !pending

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canSubmit) return
    const cleanTitle = title.trim()
    const cleanDetail = detail.trim()
    const commandText = `${cleanTitle}. ${cleanDetail}`.slice(0, 2000)
    try {
      await onSubmit({
        command_kind: 'create_owner_task',
        command_text: commandText.length >= 8 ? commandText : `${cleanTitle} ${cleanDetail} vazifa`,
        task_title: cleanTitle,
        task_detail: cleanDetail,
        task_kind: taskKind,
        customer_label: customerLabel.trim() || undefined,
        due_at: dueAt ? new Date(dueAt).toISOString() : null,
        correlation_id: `ui:tasks:bi-owner-task:${Date.now()}`,
      })
      setTitle('')
      setDetail('')
      setCustomerLabel('')
      setDueAt('')
      setTaskKind('business')
    } catch {
      // The mutation hook owns the visible toast; keep this form focused on state.
    }
  }

  return (
    <form className="flex flex-col gap-3" onSubmit={submit}>
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-medium">Vazifa taklif qilish</h3>
        <Badge variant="outline">Taklif</Badge>
      </div>
      <div className="flex flex-col gap-2">
        <Label htmlFor="bi-task-kind">Turi</Label>
        <Select
          value={taskKind}
          onValueChange={(value) => {
            if (TASK_COMMAND_KINDS.some((item) => item.id === value)) {
              setTaskKind(value as OwnerTaskKind)
            }
          }}
        >
          <SelectTrigger id="bi-task-kind" className="w-full">
            <span className="truncate">{selectedKind?.label ?? 'Biznes ishi'}</span>
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {TASK_COMMAND_KINDS.map((item) => (
                <SelectItem key={item.id} value={item.id}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
      </div>
      <div className="flex flex-col gap-2">
        <Label htmlFor="bi-task-title">Nima qilish kerak?</Label>
        <Input
          id="bi-task-title"
          value={title}
          onChange={(event: ChangeEvent<HTMLInputElement>) => setTitle(event.currentTarget.value)}
          placeholder="Masalan, to‘lovni tekshirish"
        />
      </div>
      <div className="flex flex-col gap-2">
        <Label htmlFor="bi-task-detail">Tafsilot</Label>
        <Textarea
          id="bi-task-detail"
          value={detail}
          onChange={(event) => setDetail(event.currentTarget.value)}
          placeholder="Qaysi mijoz, nima sabab, keyingi qadam..."
          className="min-h-24"
        />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div className="flex min-w-0 flex-col gap-2">
          <Label htmlFor="bi-task-customer">Kimga oid?</Label>
          <Input
            id="bi-task-customer"
            value={customerLabel}
            onChange={(event: ChangeEvent<HTMLInputElement>) => setCustomerLabel(event.currentTarget.value)}
            placeholder="Mijoz"
          />
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <Label htmlFor="bi-task-due">Muddat</Label>
          <Input
            id="bi-task-due"
            type="datetime-local"
            value={dueAt}
            onChange={(event: ChangeEvent<HTMLInputElement>) => setDueAt(event.currentTarget.value)}
          />
        </div>
      </div>
      <Button type="submit" size="sm" disabled={!canSubmit}>
        <Check data-icon="inline-start" />
        {pending ? 'Qo‘shilmoqda' : 'Taklif yaratish'}
      </Button>
    </form>
  )
}

function TaskTableRow({
  task,
  selected,
  busy,
  onSelect,
  onComplete,
}: {
  task: OwnerTaskItem
  selected: boolean
  busy: boolean
  onSelect: () => void
  onComplete: () => void
}) {
  return (
    <TableRow
      data-state={selected ? 'selected' : undefined}
      className="cursor-pointer"
      onClick={onSelect}
    >
      <TableCell>
        <div className="flex min-w-0 flex-col gap-1">
          <div className="flex min-w-0 items-center gap-2">
            <Badge variant="outline">{kindLabel(task.kind)}</Badge>
            <span className="truncate font-medium">{task.title}</span>
          </div>
          <span className="line-clamp-1 text-sm text-muted-foreground">
            {task.customer_label} · {task.detail}
          </span>
        </div>
      </TableCell>
      <TableCell>
        <Badge variant={task.due_bucket === 'overdue' ? 'destructive' : 'secondary'}>
          {dueLabel(task)}
        </Badge>
      </TableCell>
      <TableCell>
        <div className="flex justify-end gap-1.5">
          {task.can_complete ? (
            <Button
              type="button"
              size="sm"
              disabled={busy}
              onClick={(event) => {
                event.stopPropagation()
                onComplete()
              }}
            >
              <Check data-icon="inline-start" />
              Bajarildi
            </Button>
          ) : null}
          {!task.can_complete ? <span className="text-sm text-muted-foreground">Tafsilotda</span> : null}
        </div>
      </TableCell>
    </TableRow>
  )
}

function TaskDetail({
  task,
  busy,
  replyPending,
  onAccept,
  onDismiss,
  onComplete,
  onSnooze,
  onMessage,
}: {
  task: OwnerTaskItem | null
  busy: boolean
  replyPending: boolean
  onAccept: (task: OwnerTaskItem) => void
  onDismiss: (task: OwnerTaskItem) => void
  onComplete: (task: OwnerTaskItem) => void
  onSnooze: (task: OwnerTaskItem) => void
  onMessage: (task: OwnerTaskItem, replyText: string) => Promise<void>
}) {
  if (!task) {
    return (
      <div className="flex flex-col gap-2">
        <h3 className="text-sm font-medium">Tanlangan vazifa</h3>
        <p className="text-sm text-muted-foreground">
          Vazifa tanlanganda sababi, dalili va keyingi amal shu yerda ko‘rinadi.
        </p>
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-medium">{task.title}</h3>
          <p className="mt-1 text-sm text-muted-foreground">{task.detail}</p>
        </div>
        <Badge variant={statusVariant(task)}>{task.status_label}</Badge>
      </div>
      <div className="grid gap-2 text-sm">
        <DetailLine label="Mijoz" value={task.customer_label} />
        <DetailLine label="Muddat" value={dueLabel(task)} />
        <DetailLine label="Manba" value={task.source_label} />
      </div>
      <div className="flex flex-wrap gap-1.5">
        {task.evidence_labels.map((label) => (
          <Badge key={label} variant="outline">{label}</Badge>
        ))}
      </div>
      <div className="flex flex-wrap gap-2 pt-1">
        {task.can_accept ? (
          <Button size="sm" disabled={busy} onClick={() => onAccept(task)}>
            <Check data-icon="inline-start" />
            Qabul qilish
          </Button>
        ) : null}
        {task.can_complete ? (
          <Button size="sm" disabled={busy} onClick={() => onComplete(task)}>
            <Check data-icon="inline-start" />
            Bajarildi
          </Button>
        ) : null}
        {task.can_snooze ? (
          <Button size="sm" variant="outline" disabled={busy} onClick={() => onSnooze(task)}>
            <ClockCounterClockwise data-icon="inline-start" />
            Keyin
          </Button>
        ) : null}
        {task.can_accept ? (
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => onDismiss(task)}>
            <X data-icon="inline-start" />
            Rad etish
          </Button>
        ) : null}
        {task.conversation_id > 0 ? (
          <Button size="sm" variant="ghost" render={<Link to="/conversations/$conversationId" params={{ conversationId: String(task.conversation_id) }} />}>
            Suhbatni ochish
          </Button>
        ) : null}
      </div>
      {task.can_message ? (
        <>
          <Separator />
          <TaskReplyProposalForm
            task={task}
            pending={replyPending}
            onSubmit={onMessage}
          />
        </>
      ) : null}
    </div>
  )
}

function TaskReplyProposalForm({
  task,
  pending,
  onSubmit,
}: {
  task: OwnerTaskItem
  pending: boolean
  onSubmit: (task: OwnerTaskItem, replyText: string) => Promise<void>
}) {
  const [replyText, setReplyText] = useState('')
  const cleanReply = replyText.trim()
  const canSubmit = cleanReply.length > 0 && !pending

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canSubmit) return
    try {
      await onSubmit(task, cleanReply)
      setReplyText('')
    } catch {
      // The mutation hook owns the visible toast; keep this form focused on state.
    }
  }

  return (
    <form className="flex flex-col gap-3" onSubmit={submit}>
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <h4 className="text-sm font-medium">Mijozga yozish</h4>
          <p className="mt-1 text-sm text-muted-foreground">
            Javob avval Amallarda tasdiqlanadi.
          </p>
        </div>
        <Badge variant="outline">Amal</Badge>
      </div>
      <div className="flex flex-col gap-2">
        <Label htmlFor={`task-reply-${task.task_id}`}>Javob matni</Label>
        <Textarea
          id={`task-reply-${task.task_id}`}
          value={replyText}
          onChange={(event) => setReplyText(event.currentTarget.value)}
          placeholder="Mijozga yuboriladigan javobni yozing..."
          className="min-h-24"
        />
      </div>
      <Button type="submit" size="sm" disabled={!canSubmit}>
        <Check data-icon="inline-start" />
        {pending ? 'Taklif qilinmoqda' : 'Javob taklif qilish'}
      </Button>
    </form>
  )
}

function ProposedTasks({
  items,
  busy,
  onAccept,
  onDismiss,
}: {
  items: OwnerTaskItem[]
  busy: boolean
  onAccept: (task: OwnerTaskItem) => void
  onDismiss: (task: OwnerTaskItem) => void
}) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-medium">Taklif qilingan vazifalar</h3>
        <Badge variant={items.length ? 'warning' : 'outline'}>{items.length}</Badge>
      </div>
      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          OQIM hozircha yangi vazifa taklif qilmadi.
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          {items.slice(0, 5).map((task) => (
            <div key={task.task_id} className="rounded-lg border border-border/70 bg-background px-3 py-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{task.title}</p>
                  <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{task.detail}</p>
                </div>
                <Badge variant="outline">{kindLabel(task.kind)}</Badge>
              </div>
              <div className="mt-3 flex gap-2">
                <Button size="sm" disabled={busy} onClick={() => onAccept(task)}>
                  <Check data-icon="inline-start" />
                  Qabul
                </Button>
                <Button size="sm" variant="ghost" disabled={busy} onClick={() => onDismiss(task)}>
                  Rad etish
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function DetailLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-muted-foreground">{label}</span>
      <span className="truncate text-right font-medium">{value}</span>
    </div>
  )
}

function TaskSkeleton() {
  return (
    <div className="flex flex-col gap-2">
      {Array.from({ length: 6 }).map((_, index) => (
        <Skeleton key={index} className="h-16 rounded-lg" />
      ))}
    </div>
  )
}

function kindLabel(kind: OwnerTaskKind) {
  const labels: Record<OwnerTaskKind, string> = {
    business: 'Biznes',
    meeting: 'Uchrashuv',
    delivery: 'Yetkazish',
    stock: 'Stok',
    call: "Qo'ng'iroq",
    payment: "To'lov",
    follow_up: 'Qayta yozish',
  }
  return labels[kind]
}

function dueLabel(task: OwnerTaskItem) {
  if (task.state === 'completed') return 'Tugatilgan'
  if (!task.due_at) {
    if (task.due_bucket === 'overdue') return 'Kechikkan'
    if (task.due_bucket === 'upcoming') return 'Keyingi'
    return 'Bugun'
  }
  try {
    return formatUzDate(new Date(task.due_at))
  } catch {
    return task.due_bucket === 'overdue' ? 'Kechikkan' : 'Muddat bor'
  }
}

function formatUzDate(date: Date) {
  const months = ['yan', 'fev', 'mar', 'apr', 'may', 'iyun', 'iyul', 'avg', 'sen', 'okt', 'noy', 'dek']
  const day = date.getDate()
  const month = months[date.getMonth()] ?? ''
  const hour = String(date.getHours()).padStart(2, '0')
  const minute = String(date.getMinutes()).padStart(2, '0')
  return `${day} ${month}, ${hour}:${minute}`
}

function statusVariant(task: OwnerTaskItem) {
  if (task.state === 'completed') return 'success'
  if (task.state === 'blocked' || task.due_bucket === 'overdue') return 'warning'
  if (task.state === 'proposed') return 'outline'
  return 'secondary'
}
