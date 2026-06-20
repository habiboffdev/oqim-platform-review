import { describe, expect, it } from 'vitest'
import { recentAgentRunsRefetchInterval } from './use-action-runtime'
import type { AgentRunFeed, AgentRunState } from '@/lib/types'

function feedWithState(state: AgentRunState): AgentRunFeed {
  return {
    schema_version: 'agent_run_feed.v1',
    workspace_id: 1,
    timelines: [{
      schema_version: 'agent_run_timeline.v1',
      workspace_id: 1,
      run_id: `run:${state}`,
      run: {
        schema_version: 'agent_run.v1',
        run_id: `run:${state}`,
        workspace_id: 1,
        agent_id: 7,
        agent_kind: 'seller',
        trigger_ref: 'conversation',
        conversation_id: 11,
        customer_id: 21,
        state,
        permission_mode: 'ask_always',
        cache_key: null,
        correlation_id: `corr:${state}`,
        idempotency_key: `idem:${state}`,
        source_refs: [],
        started_at: '2026-05-18T09:00:00Z',
        completed_at: null,
      },
      events: [],
    }],
  }
}

describe('recentAgentRunsRefetchInterval', () => {
  it('polls quickly before the first feed arrives', () => {
    expect(recentAgentRunsRefetchInterval(undefined)).toBe(5_000)
  })

  it.each<AgentRunState>(['queued', 'running', 'waiting_approval', 'waiting_tool'])(
    'polls live progress while an agent run is %s',
    (state) => {
      expect(recentAgentRunsRefetchInterval(feedWithState(state))).toBe(1_500)
    },
  )

  it.each<AgentRunState>(['completed', 'failed', 'cancelled'])(
    'stops polling when all agent runs are %s',
    (state) => {
      expect(recentAgentRunsRefetchInterval(feedWithState(state))).toBe(false)
    },
  )
})
