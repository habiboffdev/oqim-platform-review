// Ingest liveness decides what background source-sync work a connected runtime
// needs. Live updates are owned by the Telegram update pump; stale catch-up,
// dialog sync, history sync, or media sync is degraded freshness, not a reason
// to tear down the account.

// Re-run catch-up if a connected session has had no SUCCESSFUL catch-up in this
// long. (A failed attempt must not reset this — see lastCatchUpSuccessAt.)
export const INGEST_STALE_MS = 120_000;

// Kept as a telemetry threshold for callers/tests that surface degraded
// freshness. It no longer authorizes reconnecting a connected runtime.
export const MAX_CATCHUP_FAILURES = 3;

/**
 * @returns {'ok'|'catch_up'}
 */
export function nextIngestAction(runtime, {
  now = Date.now(),
  staleMs = INGEST_STALE_MS,
  maxFailures = MAX_CATCHUP_FAILURES,
} = {}) {
  void maxFailures;
  if (!runtime || !runtime.workspaceId) return 'ok';
  // Non-connected states are owned by the reconnect logic; don't double-drive.
  if (runtime.connectionState !== 'connected') return 'ok';
  if (runtime.catchUpInFlight && runtime.catchUpStartedAt) {
    const startedAt = Date.parse(runtime.catchUpStartedAt);
    if (Number.isFinite(startedAt) && now - startedAt >= staleMs) {
      return 'ok';
    }
  }
  // Use the last SUCCESSFUL catch-up; a failed attempt must never look "fresh".
  const lastOk = runtime.lastCatchUpSuccessAt ? Date.parse(runtime.lastCatchUpSuccessAt) : 0;
  if (!Number.isFinite(lastOk) || lastOk <= 0) return 'catch_up';
  if (now - lastOk >= staleMs) return 'catch_up';
  return 'ok';
}
