// eslint-disable-next-line no-restricted-imports
import { useEffect, useRef } from 'react'
import {
  useQueryClient,
  useQuery,
} from '@tanstack/react-query'
import { wsManager } from '@/lib/websocket'
import { queryKeys } from '@/lib/query-keys'
import { useAuth } from '@/lib/auth-context'
import { applyWebSocketSyncSession } from '@/lib/websocket-sync-session'
import { applySellerAgentWebSocketEvent } from '@/lib/websocket-seller-agent-events'
import { applyMessageWebSocketEvent } from '@/lib/websocket-message-events'
import { applyProjectionWebSocketEvent } from '@/lib/websocket-projection-events'
import {
  registerActiveConversationLifecycle,
  registerReconnectLifecycle,
} from '@/lib/websocket-connection-lifecycle'
import { dispatchActivityEvent } from '@/hooks/use-activity-stream'

/**
 * Single consolidated WebSocket event router.
 *
 * Handles ALL WS events in one place, eliminating duplicate invalidation.
 * Accepts an optional activeConversationId for D-18 chat_opened re-send on reconnect.
 */
export function useWebSocket(
  activeConversationId?: number,
  options: { enabled?: boolean } = {},
) {
  const queryClient = useQueryClient()
  const { isAuthenticated } = useAuth()
  const enabled = options.enabled ?? true

  // Track active conversation in a ref so reconnect handler always has latest value
  const activeConvRef = useRef<number | undefined>(activeConversationId)
  activeConvRef.current = activeConversationId

  // Shimmer timeout timers — clear thinking shimmer after 15s
  const shimmerTimers = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map())

  // Typing auto-dismiss timers — clear typing indicator after 5s
  const typingTimers = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map())

  // Track last received WS sequence for gap detection
  const lastSeqRef = useRef<number>(0)

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!isAuthenticated || !enabled) return

    wsManager.connect()

    const applySyncSession = (
      rawSyncData: Record<string, unknown>,
      sequenceId?: number,
    ) => {
      applyWebSocketSyncSession({
        queryClient,
        rawSyncData,
        activeConversationId: activeConvRef.current,
        sequenceId,
        dispatchActivityEvent,
        recordSequence: (nextSequenceId) => {
          lastSeqRef.current = nextSequenceId
        },
      })
    }

    const clearShimmer = (conversationId: number) => {
      clearTimeout(shimmerTimers.current.get(conversationId))
      shimmerTimers.current.delete(conversationId)
      queryClient.setQueryData<Set<number>>(queryKeys.shimmer, (old) => {
        const set = new Set(old)
        set.delete(conversationId)
        return set
      })
    }

    // Single wildcard listener for ALL events
    const unsubEvents = wsManager.on('*', (data) => {
      const type = data.type as string

      // Dispatch to unified activity stream (Issue #103)
      dispatchActivityEvent(data)

      // Track sequence for gap detection
      const seq = data.sequence_id as number | undefined
      if (seq) lastSeqRef.current = seq

      if (applySellerAgentWebSocketEvent({
        queryClient,
        data,
        shimmerTimers: shimmerTimers.current,
        clearShimmer,
      })) {
        return
      }

      if (applyMessageWebSocketEvent({
        queryClient,
        data,
        activeConversationId: activeConvRef.current,
        typingTimers: typingTimers.current,
      })) {
        return
      }

      if (applyProjectionWebSocketEvent({
        queryClient,
        data,
        activeConversationId: activeConvRef.current,
        typingTimers: typingTimers.current,
      })) {
        return
      }

      switch (type) {
        case 'sync_response': {
          applySyncSession(data, data.sequence_id as number | undefined)
          break
        }

        case 'session.noop':
        case 'session.delta':
        case 'session.reset_required':
        case 'projection.changed': {
          applySyncSession(data, data.sequence_id as number | undefined)
          break
        }

        default:
          break

      }
    })

    const unsubReconnect = registerReconnectLifecycle({
      wsManager,
      activeConversationRef: activeConvRef,
      lastSequenceRef: lastSeqRef,
      dispatchActivityEvent,
    })

    return () => {
      unsubEvents()
      unsubReconnect()
      shimmerTimers.current.forEach(clearTimeout)
      typingTimers.current.forEach(clearTimeout)
    }
  }, [enabled, isAuthenticated, queryClient])

  // Keep server-side active-chat awareness aligned with route-driven conversation authority.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!isAuthenticated || !enabled || !activeConversationId) return

    return registerActiveConversationLifecycle({
      wsManager,
      queryClient,
      activeConversationId,
    })
  }, [enabled, isAuthenticated, activeConversationId, queryClient])
}

/** Reactive shimmer state — re-renders components when AI thinking starts/stops */
export function useShimmerState(): Set<number> {
  const { data } = useQuery({
    queryKey: queryKeys.shimmer,
    queryFn: () => new Set<number>(),
    initialData: () => new Set<number>(),
    staleTime: Infinity,
  })
  return data ?? new Set()
}
