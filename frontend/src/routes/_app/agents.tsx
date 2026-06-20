import { Link } from '@tanstack/react-router'
import {
  Brain,
  FileText,
  Lightning,
  Plugs,
  Plus,
  Robot,
  ShieldCheck,
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
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useAgentWorkbenchAgents } from '@/hooks/use-agent-workbench'
import { uz } from '@/lib/uz'
import { cn } from '@/lib/utils'

export function AgentsPage() {
  const agents = useAgentWorkbenchAgents()
  const items = agents.data?.items ?? []
  const readyCount = items.filter((agent) => agent.is_active && agent.document_section_count > 0).length
  const needsWorkCount = items.filter(
    (agent) => !agent.is_active || agent.tool_grant_count === 0 || agent.trigger_count === 0,
  ).length

  return (
    <div className="grid h-full min-h-0 grid-cols-1 bg-background text-foreground xl:grid-cols-[minmax(0,1fr)_320px]">
      <section className="flex min-h-0 flex-col">
        <header className="border-b border-border/60 px-5 py-4 lg:px-8">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="text-xs text-muted-foreground">Ishchilar</div>
              <h1 className="mt-1 text-xl font-semibold tracking-tight">Agentlar</h1>
              <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
                Har bir agentning hujjati, ko‘nikmalari, Telegram ruxsatlari va ishga tushish holatlari shu yerda boshqariladi.
              </p>
            </div>
            <Button size="sm" render={<Link to="/agents/new" />}>
              <Plus data-icon="inline-start" />
              {uz.agents.create.title}
            </Button>
          </div>
        </header>

        <ScrollArea className="min-h-0 flex-1">
          <div className="flex flex-col gap-4 px-5 py-5 lg:px-8">
            <div className="grid gap-3 md:grid-cols-3">
              <StatusStrip
                icon={Robot}
                label="Agentlar"
                value={agents.isLoading ? '...' : String(items.length)}
                caption="Default va maxsus ishchilar"
              />
              <StatusStrip
                icon={ShieldCheck}
                label="Tayyor"
                value={agents.isLoading ? '...' : String(readyCount)}
                caption="Hujjat va sozlamasi bor"
              />
              <StatusStrip
                icon={Lightning}
                label="Tekshirish kerak"
                value={agents.isLoading ? '...' : String(needsWorkCount)}
                caption="Ruxsat yoki trigger yetishmaydi"
              />
            </div>

            <Alert>
              <Brain />
              <AlertTitle>Agent oddiy prompt emas</AlertTitle>
              <AlertDescription>
                AGENT.md egaga tushunarli hujjat. Haqiqiy ishda esa sozlama, ruxsat, trigger va audit birga ishlaydi.
              </AlertDescription>
            </Alert>

            {agents.isLoading ? (
              <div className="flex flex-col gap-2">
                {Array.from({ length: 5 }).map((_, index) => (
                  <Skeleton key={index} className="h-14 w-full rounded-lg" />
                ))}
              </div>
            ) : items.length === 0 ? (
              <Empty className="min-h-[360px] rounded-lg border border-border">
                <EmptyHeader>
                  <EmptyTitle>Agentlar hali yaratilmagan</EmptyTitle>
                  <EmptyDescription>
                    Onboarding tugaganda Seller, Support, Catalog Update, Follow-up va BI agentlar yaratiladi.
                  </EmptyDescription>
                </EmptyHeader>
                <EmptyContent>
                  <Button render={<Link to="/onboarding" />}>Onboardingga o‘tish</Button>
                </EmptyContent>
              </Empty>
            ) : (
              <div className="overflow-hidden rounded-lg border border-border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Agent</TableHead>
                      <TableHead>Hujjat</TableHead>
                      <TableHead>Ko‘nikma</TableHead>
                      <TableHead>Ruxsatlar</TableHead>
                      <TableHead>Trigger</TableHead>
                      <TableHead>Ruxsat</TableHead>
                      <TableHead className="text-right">Holat</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {items.map((agent) => (
                      <TableRow key={agent.id}>
                        <TableCell>
                          <Link
                            to="/agents/$agentId"
                            params={{ agentId: String(agent.id) }}
                            className="flex min-w-0 items-center gap-3"
                          >
                            <span className="flex size-8 shrink-0 items-center justify-center rounded-md border border-border bg-background">
                              <Robot className="size-4" weight="thin" />
                            </span>
                            <span className="min-w-0">
                              <span className="block truncate font-medium">{agent.name}</span>
                              <span className="block truncate text-xs text-muted-foreground">
                                {agentTypeLabel(agent.agent_type)}
                              </span>
                            </span>
                          </Link>
                        </TableCell>
                        <TableCell>{agent.document_section_count}</TableCell>
                        <TableCell>{agent.skill_count}</TableCell>
                        <TableCell>{agent.tool_grant_count}</TableCell>
                        <TableCell>{agent.trigger_count}</TableCell>
                        <TableCell>
                          <Badge variant={permissionVariant(agent.permission_mode)}>
                            {permissionLabel(agent.permission_mode)}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-right">
                          <Badge variant={agent.is_active ? 'success' : 'outline'}>
                            {agent.is_active ? 'Faol' : 'To‘xtagan'}
                          </Badge>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </div>
        </ScrollArea>
      </section>

      <aside className="hidden min-h-0 border-l border-border/60 bg-foreground/[0.015] xl:flex xl:flex-col">
        <div className="px-5 py-4">
          <div className="text-sm font-medium">Agent OS</div>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">
            Agentlar hujjat, ko‘nikma, ruxsat, trigger va tasdiqlanadigan ishlar bilan boshqariladi.
          </p>
        </div>
        <Separator />
        <div className="flex flex-col gap-0 px-5 py-3 text-sm">
          <RailRow icon={FileText} title="AGENT.md" text="Egaga ko‘rinadigan ish hujjati." />
          <RailRow icon={Brain} title="Ko‘nikmalar" text="Default va biznesga xos ko‘nikmalar." />
          <RailRow icon={Plugs} title="Ruxsatlar" text="Telegram va kelajak integratsiyalar." />
          <RailRow icon={Lightning} title="Triggerlar" text="Qachon va nima uchun ish boshlaydi." />
        </div>
      </aside>
    </div>
  )
}

function StatusStrip({
  icon: Icon,
  label,
  value,
  caption,
}: {
  icon: Icon
  label: string
  value: string
  caption: string
}) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-border bg-background px-3 py-3">
      <span className="flex size-8 items-center justify-center rounded-md bg-muted text-muted-foreground">
        <Icon className="size-4" weight="thin" />
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-medium">{label}</span>
        <span className="block truncate text-xs text-muted-foreground">{caption}</span>
      </span>
      <span className="ml-auto text-lg font-semibold tabular-nums">{value}</span>
    </div>
  )
}

function RailRow({
  icon: Icon,
  title,
  text,
}: {
  icon: Icon
  title: string
  text: string
}) {
  return (
    <div className="flex gap-3 border-b border-border/60 py-3 last:border-b-0">
      <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" weight="thin" />
      <div className="min-w-0">
        <div className="font-medium">{title}</div>
        <div className="mt-0.5 leading-5 text-muted-foreground">{text}</div>
      </div>
    </div>
  )
}

export function permissionLabel(value: string) {
  if (value === 'full_access') return 'To‘liq ruxsat'
  if (value === 'auto_approve') return 'Avto tasdiq'
  return 'Har safar so‘rash'
}

export function permissionVariant(value: string): 'warning' | 'success' | 'outline' {
  if (value === 'full_access') return 'warning'
  if (value === 'auto_approve') return 'success'
  return 'outline'
}

export function agentTypeLabel(value: string) {
  const labels: Record<string, string> = {
    seller: 'Sotuvchi',
    support: 'Support',
    catalog_update: 'Katalog yangilash',
    follow_up: 'Qayta yozish',
    bi: 'BI agent',
    custom: 'Maxsus agent',
  }
  return labels[value] ?? value.replaceAll('_', ' ')
}

export function activeTone(active: boolean, degraded: boolean) {
  return cn(active ? 'text-foreground' : 'text-muted-foreground', degraded && 'text-warning-foreground')
}
