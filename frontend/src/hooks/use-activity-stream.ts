/**
 * Activity stream — consumes unified WS events.
 *
 * Provides `latestEvent` (for status bar) and `events[]` (for activity log).
 * Replaces the ad-hoc shimmer set pattern.
 *
 * Issue #103 — unified event protocol.
 */

import { useCallback, useMemo, useRef, useState } from 'react'
import { useMountEffect } from '@/hooks/use-mount-effect'

export interface ActivityEvent {
  type: string
  scope: 'system' | 'conversation'
  message: string
  data: Record<string, unknown>
  ts: number
}

const UNIFIED_EVENT_TYPES = new Set([
  'sync:checking', 'sync:progress', 'sync:complete', 'sync:error',
  'seller_agent_reply:thinking', 'seller_agent_reply:ready',
  'seller_agent_reply:auto_sent', 'seller_agent_reply:failed',
  'sales_followup:due',
  'message:new', 'message:read',
])

const MAX_EVENTS = 200
const FRESHNESS_WINDOW_MS = 5 * 60 * 1000

export function useActivityStream() {
  const [latestEvent, setLatestEvent] = useState<ActivityEvent | null>(null)
  const [eventCount, setEventCount] = useState(0)
  const [nowMs, setNowMs] = useState(() => Date.now())
  const eventsRef = useRef<ActivityEvent[]>([])

  const pushEvent = useCallback((event: ActivityEvent) => {
    eventsRef.current = [...eventsRef.current.slice(-(MAX_EVENTS - 1)), event]
    setLatestEvent(event)
    setEventCount(eventsRef.current.length)
  }, [])

  // Listen for unified WS events via the existing wsManager message handler
  useMountEffect(() => {
    function handleWsEvent(event: CustomEvent<ActivityEvent>) {
      pushEvent(event.detail)
    }
    window.addEventListener('oqim:activity' as any, handleWsEvent)
    return () => window.removeEventListener('oqim:activity' as any, handleWsEvent)
  })

  useMountEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), 30_000)
    return () => window.clearInterval(timer)
  })

  const latestFreshEvent = useMemo(() => {
    if (!latestEvent) return null
    const ageMs = nowMs - latestEvent.ts * 1000
    if (ageMs > FRESHNESS_WINDOW_MS) return null
    return latestEvent
  }, [latestEvent, nowMs])

  return {
    latestEvent: latestFreshEvent,
    rawLatestEvent: latestEvent,
    events: eventsRef.current,
    eventCount,
  }
}

/**
 * Dispatch an activity event from the WS handler.
 * Called from use-websocket.ts when a unified event arrives.
 */
export function dispatchActivityEvent(data: Record<string, unknown>) {
  if (!data?.type || typeof data.type !== 'string') return
  if (!UNIFIED_EVENT_TYPES.has(data.type)) return

  const event: ActivityEvent = {
    type: data.type as string,
    scope: (data.scope as 'system' | 'conversation') || 'system',
    message: (data.message as string) || '',
    data: (data.data as Record<string, unknown>) || {},
    ts: (data.ts as number) || Math.floor(Date.now() / 1000),
  }

  window.dispatchEvent(new CustomEvent('oqim:activity', { detail: event }))
}
