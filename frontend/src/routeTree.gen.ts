import {
  createRootRouteWithContext,
  createRoute,
  redirect,
} from '@tanstack/react-router'

import type { useAuth } from '@/lib/auth-context'
import { RootLayout } from '@/routes/__root'
import { AppLayout } from '@/routes/_app'
import { LoginPage } from '@/routes/_auth/login'
import { RegisterPage } from '@/routes/_auth/register'
import { ConversationsPage } from '@/routes/_app/conversations'
import { BrainPage } from '@/routes/_app/brain'
import { SourcesPage } from '@/routes/_app/sources'
import { SettingsPage } from '@/routes/_app/settings'
import { OQIMIntelligencePage } from '@/routes/_app/intelligence'
import { AgentsPage } from '@/routes/_app/agents'
import { AgentsNewPage } from '@/routes/_app/agents-new'
import { AgentDetailPage } from '@/routes/_app/agent-detail'
import { ActionsPage } from '@/routes/_app/actions'
import { TasksPage } from '@/routes/_app/tasks'
import { IntegrationsPage } from '@/routes/_app/integrations'
import { CrmPage } from '@/routes/_app/crm'
import { OnboardingPage } from '@/routes/_app/onboarding'

type AuthContext = ReturnType<typeof useAuth>

interface RouterContext {
  auth: AuthContext
}

type ConversationsSearch = {
  mode?: string
}

type BrainSearch = {
  tab?: string
  mode?: string
}

type SourcesSearch = {
  lifecycle?: string
  kind?: string
}

// Root
const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: RootLayout,
})

// Auth routes (public)
const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/login',
  component: LoginPage,
})

const registerRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/register',
  component: RegisterPage,
})

// App layout (protected)
const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: '_app',
  component: AppLayout,
  // Auth guard is in AppLayout component (waits for fetchMe to resolve)
  // Do NOT use beforeLoad — it runs before async auth check completes
})

// App child routes
const dashboardRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/',
  beforeLoad: () => { throw redirect({ to: '/conversations' }) },
})

const conversationsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/conversations',
  component: ConversationsPage,
  validateSearch: (search: Record<string, unknown>): ConversationsSearch => {
    return typeof search.mode === 'string' && search.mode ? { mode: search.mode } : {}
  },
})

// Child of conversationsRoute — keeps ConversationsPage mounted on chat switch
const conversationDetailRoute = createRoute({
  getParentRoute: () => conversationsRoute,
  path: '$conversationId',
})

const brainRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/brain',
  component: BrainPage,
  validateSearch: (search: Record<string, unknown>): BrainSearch => {
    const next: BrainSearch = {}
    if (typeof search.tab === 'string' && search.tab) next.tab = search.tab
    if (typeof search.mode === 'string' && search.mode) next.mode = search.mode
    return next
  },
})

// Catch /brain/knowledge and /brain/rules — redirect to /brain?tab=<segment>
const brainCatchAllRoute = createRoute({
  getParentRoute: () => brainRoute,
  path: '$tab',
  beforeLoad: ({ params }) => {
    const tab = params.tab
    if (tab === 'knowledge' || tab === 'rules' || tab === 'catalog' || tab === 'voice' || tab === 'pairs' || tab === 'company') {
      throw redirect({ to: '/brain', search: { tab } })
    }
    throw redirect({ to: '/brain' })
  },
})

const sourcesRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/sources',
  component: SourcesPage,
  validateSearch: (search: Record<string, unknown>): SourcesSearch => {
    const next: SourcesSearch = {}
    if (typeof search.lifecycle === 'string' && search.lifecycle) next.lifecycle = search.lifecycle
    if (typeof search.kind === 'string' && search.kind) next.kind = search.kind
    return next
  },
})

type IntelligenceSearch = { tab?: string }

const intelligenceRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/intelligence',
  component: OQIMIntelligencePage,
  validateSearch: (search: Record<string, unknown>): IntelligenceSearch => {
    return typeof search.tab === 'string' && search.tab ? { tab: search.tab } : {}
  },
})

const agentsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/agents',
  component: AgentsPage,
})

const agentsNewRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/agents/new',
  component: AgentsNewPage,
})

const agentDetailRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/agents/$agentId',
  component: AgentDetailPage,
})

const tasksRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/tasks',
  component: TasksPage,
})

const actionsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/actions',
  component: ActionsPage,
})

const integrationsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/integrations',
  component: IntegrationsPage,
})

const crmRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/crm',
  component: CrmPage,
})

const settingsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/settings',
  component: SettingsPage,
})

const onboardingRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/onboarding',
  component: OnboardingPage,
})

// Build tree
export const routeTree = rootRoute.addChildren([
  loginRoute,
  registerRoute,
  onboardingRoute,
  appRoute.addChildren([
    dashboardRoute,
    conversationsRoute.addChildren([conversationDetailRoute]),
    brainRoute.addChildren([brainCatchAllRoute]),
    sourcesRoute,
    intelligenceRoute,
    agentsRoute,
    agentsNewRoute,
    agentDetailRoute,
    actionsRoute,
    tasksRoute,
    integrationsRoute,
    crmRoute,
    settingsRoute,
  ]),
])
