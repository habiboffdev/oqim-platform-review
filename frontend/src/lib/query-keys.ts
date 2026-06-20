import type { ConversationFilters } from './types'

export const queryKeys = {
  admin: {
    sellerAgentRuntime: ['admin', 'seller-agent-runtime'] as const,
    llmPolicies: ['admin', 'llm-policies'] as const,
    runtimeSignals: ['admin', 'runtime-signals'] as const,
    telegramAuthAttempts: ['admin', 'telegram-auth-attempts'] as const,
  },
  conversations: {
    all: ['conversations'] as const,
    list: (filters?: ConversationFilters) =>
      ['conversations', 'list', filters ?? {}] as const,
    detail: (id: number) => ['conversations', id] as const,
  },
  liveChats: ['live-chats'] as const,
  messages: {
    list: (conversationId: number) => ['messages', conversationId] as const,
  },
  sellerAgentReplies: {
    all: ['ai-replies'] as const,
    byConversation: (id: number) => ['ai-replies', id] as const,
  },
  sellerAgentReplyInbox: ['seller-agent-reply-inbox'] as const,
  telegram: {
    status: ['telegram', 'status'] as const,
    progress: ['telegram', 'progress'] as const,
  },
  onboarding: {
    runtime: ['onboarding', 'runtime'] as const,
  },
  onboardingDocuments: ['onboarding', 'documents'] as const,
  skillCandidates: ['brain', 'skills', 'candidates'] as const,
  workspaceOS: {
    state: ['workspace-os', 'state'] as const,
  },
  shimmer: ['conversation-shimmer'] as const,
  customers: ['customers'] as const,
  businessBrain: {
    catalog: ['business-brain', 'catalog'] as const,
    facts: ['business-brain', 'facts'] as const,
    objects: (domain?: string) => ['business-brain', 'objects', domain ?? 'all'] as const,
    sources: ['business-brain', 'sources'] as const,
    sourceIntake: ['business-brain', 'source-intake'] as const,
    businessMd: ['business-brain', 'business-md'] as const,
  },
  agents: {
    all: ['agents'] as const,
    detail: (id: number) => ['agents', 'detail', id] as const,
    toolCatalog: (connector?: string) => ['agents', 'tool-catalog', connector ?? 'all'] as const,
  },
  bi: {
    history: ['bi-history'] as const,
    dashboard: ['bi-promoter', 'dashboard'] as const,
    policy: ['bi-promoter', 'policy'] as const,
    commands: ['bi-promoter', 'commands'] as const,
  },
  actionRuntime: {
    inbox: ['action-runtime', 'inbox'] as const,
    policy: ['action-runtime', 'policy'] as const,
    tasks: ['action-runtime', 'tasks'] as const,
    recentRuns: ['action-runtime', 'agent-runs', 'recent'] as const,
    timeline: (proposalId?: string | null) =>
      ['action-runtime', 'timeline', proposalId ?? 'none'] as const,
  },
} as const
