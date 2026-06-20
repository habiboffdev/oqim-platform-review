import { useQuery } from '@tanstack/react-query'
import { CheckCircle, Plug, Plugs } from '@phosphor-icons/react'
import { api } from '@/lib/api-client'
import { useAuth } from '@/lib/auth-context'
import { uz } from '@/lib/uz'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { InstagramCard } from '@/components/blocks/integrations/instagram-card'
import { AmoCrmCard } from '@/components/blocks/integrations/amocrm-card'
import { OwnerBotCard } from '@/components/blocks/integrations/owner-bot-card'

interface ToolGrantRow {
  id: number
  workspace_id: number
  agent_id: number
  scope: string
  connector?: string
  scope_label?: string
  scope_description?: string
  active: boolean
  use_count: number
  granted_at: string
  last_used_at: string | null
}

interface ToolGrantsResponse {
  schema_version: 'intelligence_tool_grants.v1'
  items: ToolGrantRow[]
}

const FUTURE_INTEGRATIONS = [
  { id: 'bitrix', label: 'Bitrix24', description: 'Lead/deal sync' },
  { id: 'calendar', label: 'Calendar', description: 'Meeting booking' },
  { id: 'pos', label: 'POS / ERP', description: 'Catalog + price sync' },
]

function useToolGrants() {
  return useQuery({
    queryKey: ['intelligence', 'tool-grants'] as const,
    queryFn: () => api.get<ToolGrantsResponse>('/api/intelligence/tool-grants'),
    staleTime: 30_000,
  })
}

export function IntegrationsPage() {
  const { session } = useAuth()
  const grants = useToolGrants()
  const telegram = session?.integrations.find((item) => item.provider === 'telegram_personal')
  const telegramHealthy = telegram?.durable_connected && !telegram.needs_reconnect
  const items = grants.data?.items ?? []
  const telegramGrants = items
    .filter((grant) => grant.scope.startsWith('telegram.'))
    .sort((a, b) => {
      if (a.active !== b.active) return a.active ? -1 : 1
      return telegramToolLabel(a).localeCompare(telegramToolLabel(b))
    })
  const activeGrantCount = telegramGrants.filter((grant) => grant.active).length

  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      <header className="border-b border-border/60 px-6 py-4">
        <div className="flex items-center gap-2.5">
          <Plugs className="size-4 opacity-70" weight="thin" />
          <h1 className="text-sm font-medium">{uz.workspaceUi.modules.integrations.label}</h1>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          {uz.workspaceUi.modules.integrations.description}
        </p>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
        <section className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
          <Card className="rounded-lg" size="sm">
            <CardHeader className="border-b border-border/70">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <CardTitle className="flex items-center gap-2">
                    Telegram
                    <Badge
                      variant={telegramHealthy ? 'success' : 'warning'}
                      size="sm"
                      className="gap-1"
                    >
                      {telegramHealthy && <CheckCircle className="size-3" weight="thin" />}
                      {telegramHealthy ? 'Ulangan' : 'Qayta ulash kerak'}
                    </Badge>
                  </CardTitle>
                  <CardDescription>
                    Agentlar Telegramni shu ruxsatlar orqali ishlatadi. Ruxsatni agent
                    sahifasida taklif qilib, Amallarda tasdiqlaysiz.
                  </CardDescription>
                </div>
                <Badge variant="outline" size="sm">
                  {activeGrantCount} faol
                </Badge>
              </div>
            </CardHeader>
            <CardContent className="pt-4">
              {grants.isLoading ? (
                <p className="text-xs text-muted-foreground">Ruxsatlar yuklanmoqda…</p>
              ) : telegramGrants.length === 0 ? (
                <div className="rounded-lg border border-dashed border-border px-4 py-6">
                  <p className="text-sm font-medium">Hali ruxsat yo‘q</p>
                  <p className="mt-1 max-w-md text-xs text-muted-foreground">
                    Agent yaratganingizda yoki trigger qo‘shganingizda OQIM kerakli
                    Telegram ruxsatini Amallarga taklif qiladi.
                  </p>
                </div>
              ) : (
                <ul className="divide-y divide-border/70">
                  {telegramGrants.map((grant) => (
                    <li
                      key={grant.id}
                      className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-4 py-3"
                    >
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium">
                          {telegramToolLabel(grant)}
                        </div>
                        <div className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                          {telegramToolDescription(grant)}
                        </div>
                        <dl className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
                          <div>
                            <dt className="sr-only">Agent</dt>
                            <dd>{agentLabel(grant.agent_id)}</dd>
                          </div>
                          <div>
                            <dt className="sr-only">Ishlatilgan</dt>
                            <dd>{grant.use_count} marta ishlatilgan</dd>
                          </div>
                        </dl>
                      </div>
                      <Badge
                        className="shrink-0"
                        variant={grant.active ? 'success' : 'outline'}
                        size="sm"
                      >
                        {grant.active ? 'Faol' : 'O‘chiq'}
                      </Badge>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          <aside>
            <div className="grid gap-3">
              <InstagramCard />
              <AmoCrmCard />
              <OwnerBotCard />
            </div>
            <h2 className="mt-5 text-sm font-medium">Keyingi ulanishlar</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              Bu yerda boshqa kanallar tayyor bo‘lganda yoqiladi.
            </p>
            <div className="mt-3 grid gap-1.5">
              {FUTURE_INTEGRATIONS.map((integration) => (
                <div
                  key={integration.id}
                  className="grid grid-cols-[28px_1fr_auto] items-center gap-3 rounded-lg border border-border bg-background px-3 py-2.5"
                >
                  <span className="grid size-7 place-items-center rounded-md bg-muted text-muted-foreground">
                    <Plug className="size-3.5" weight="thin" />
                  </span>
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium">{integration.label}</div>
                    <div className="mt-0.5 truncate text-xs text-muted-foreground">
                      {integration.description}
                    </div>
                  </div>
                  <Button variant="outline" size="xs" disabled>
                    Tez orada
                  </Button>
                </div>
              ))}
            </div>
          </aside>
        </section>
      </div>
    </div>
  )
}

const TELEGRAM_TOOL_COPY: Record<string, { label: string; description: string }> = {
  'telegram.read_messages': {
    label: 'Suhbatni o‘qish',
    description: 'Agent javob yozishdan oldin mijozning oxirgi kontekstini o‘qiydi.',
  },
  'telegram.send_message': {
    label: 'Javob yuborish',
    description: 'Agent faqat tasdiqlangan yoki ruxsat berilgan javobni Telegramga yuboradi.',
  },
  'telegram.edit_message': {
    label: 'Yuborilganni tahrirlash',
    description: 'Agent faqat OQIM yuborgan xabarni tuzatishi mumkin.',
  },
  'telegram.watch_channel': {
    label: 'Kanalni kuzatish',
    description: 'Agent kanal yoki manba yangilanganda ish boshlaydi.',
  },
  'telegram.fetch_media': {
    label: 'Media ochish',
    description: 'Agent rasm, chek va fayllarni dalil sifatida ochib tekshiradi.',
  },
  'telegram.sync_history': {
    label: 'Tarixni yangilash',
    description: 'Agent suhbat tarixini qayta sinxronlab, oxirgi kontekstni tiklaydi.',
  },
}

function telegramToolLabel(grant: ToolGrantRow): string {
  return grant.scope_label || TELEGRAM_TOOL_COPY[grant.scope]?.label || 'Telegram ruxsati'
}

function telegramToolDescription(grant: ToolGrantRow): string {
  return (
    grant.scope_description
    || TELEGRAM_TOOL_COPY[grant.scope]?.description
    || 'Agent ushbu Telegram ruxsati orqali ish bajaradi.'
  )
}

function agentLabel(agentId: number): string {
  return `Agent ${agentId}`
}
