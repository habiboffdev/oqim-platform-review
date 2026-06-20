import { connectorRuntimeStatus } from './telegram-connector-runtime.js';

export function runtimeLabel(runtime) {
  if (runtime.workspaceId) return `workspace ${runtime.workspaceId}`;
  if (runtime.tempSessionId) return `temp ${runtime.tempSessionId}`;
  return 'bootstrap';
}

export async function queueSizeForWorkspace(eventOutbox, workspaceId) {
  if (!workspaceId) return 0;
  try {
    return await eventOutbox.pendingCount(workspaceId);
  } catch {
    return 0;
  }
}

function emptySyncJobSummary() {
  return {
    running: 0,
    paused: 0,
    failed: 0,
    succeeded: 0,
  };
}

function emptyDurableStateSummary() {
  return {
    peers: 0,
    dialogs: 0,
    messages: 0,
    mediaRefs: 0,
    cursors: 0,
  };
}

function emptyCursorFreshness() {
  return {
    latestReceivedAt: null,
    latestAppliedAt: null,
    maxAgeSeconds: null,
    stale: false,
    cursors: [],
  };
}

export const DEFAULT_LIVE_UPDATE_STALE_AFTER_SECONDS = 120;

function parseTimestampMs(value) {
  if (!value) return null;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : null;
}

export function liveUpdateHealth(
  runtime,
  {
    nowMs = Date.now(),
    staleAfterSeconds = DEFAULT_LIVE_UPDATE_STALE_AFTER_SECONDS,
  } = {},
) {
  if (runtime?.connectionState !== 'connected') {
    return {
      state: 'disconnected',
      healthy: false,
      stale: false,
      ageSeconds: null,
      staleAfterSeconds,
      lastObservedAt: runtime?.lastLiveInboundHotPathAt || null,
    };
  }
  if (!runtime?.handlersRegisteredAt) {
    return {
      state: 'handlers_missing',
      healthy: false,
      stale: false,
      ageSeconds: null,
      staleAfterSeconds,
      lastObservedAt: runtime?.lastLiveInboundHotPathAt || null,
    };
  }

  const observedAtMs = parseTimestampMs(runtime.lastLiveInboundHotPathAt);
  if (!observedAtMs) {
    return {
      state: 'online',
      healthy: true,
      stale: false,
      ageSeconds: null,
      staleAfterSeconds,
      lastObservedAt: runtime.lastLiveInboundHotPathAt || null,
    };
  }

  const ageSeconds = Math.max(0, Math.floor((nowMs - observedAtMs) / 1000));
  const idle = ageSeconds > staleAfterSeconds;
  return {
    state: idle ? 'idle' : 'fresh',
    healthy: true,
    stale: false,
    idle,
    ageSeconds,
    staleAfterSeconds,
    lastObservedAt: runtime.lastLiveInboundHotPathAt,
  };
}

export function summarizeLiveUpdateHealth(runtimes, options = {}) {
  const summary = {
    fresh: 0,
    idle: 0,
    stale: 0,
    online: 0,
    handlersMissing: 0,
    disconnected: 0,
    healthy: 0,
    total: 0,
  };
  for (const runtime of runtimes || []) {
    const health = liveUpdateHealth(runtime, options);
    summary.total += 1;
    if (health.healthy) summary.healthy += 1;
    if (health.state === 'fresh') summary.fresh += 1;
    else if (health.state === 'idle') summary.idle += 1;
    else if (health.state === 'online') summary.online += 1;
    else if (health.state === 'stale') summary.stale += 1;
    else if (health.state === 'handlers_missing') summary.handlersMissing += 1;
    else if (health.state === 'disconnected') summary.disconnected += 1;
  }
  return summary;
}

export async function syncJobSummaryForWorkspace(syncJobStore, workspaceId) {
  if (!syncJobStore || !workspaceId) {
    return emptySyncJobSummary();
  }
  try {
    return await syncJobStore.summaryForWorkspace(workspaceId);
  } catch {
    return emptySyncJobSummary();
  }
}

export async function durableStateSummaryForWorkspace(durableStateStore, workspaceId) {
  if (!durableStateStore || !workspaceId) {
    return emptyDurableStateSummary();
  }
  try {
    return await durableStateStore.summaryForWorkspace(workspaceId);
  } catch {
    return emptyDurableStateSummary();
  }
}

export async function durableStateCursorFreshnessForWorkspace(durableStateStore, workspaceId) {
  if (!durableStateStore?.cursorFreshnessForWorkspace || !workspaceId) {
    return emptyCursorFreshness();
  }
  try {
    return await durableStateStore.cursorFreshnessForWorkspace(workspaceId);
  } catch {
    return emptyCursorFreshness();
  }
}

export async function serializeRuntimeStatus(
  runtime,
  eventOutbox,
  syncJobStore = null,
  durableStateStore = null,
  options = {},
) {
  const telegramState = runtime.telegramState || null;
  const durableState = runtime.telegramDurableStateCounts || {};
  const syncJobs = runtime.telegramSyncJobCounts || {};
  const persistedSyncJobs = await syncJobSummaryForWorkspace(syncJobStore, runtime.workspaceId);
  const persistedDurableState = await durableStateSummaryForWorkspace(
    durableStateStore,
    runtime.workspaceId,
  );
  const cursorFreshness = await durableStateCursorFreshnessForWorkspace(
    durableStateStore,
    runtime.workspaceId,
  );
  const floodWaits = [...(runtime.telegramMethodQueues?.floodWaits?.values?.() || [])]
    .map((entry) => ({
      methodClass: entry.methodClass,
      priority: entry.priority,
      label: entry.label,
      retryAfter: entry.retryAfter,
      pausedForMs: Math.max(0, entry.pausedUntilMs - Date.now()),
      lastError: entry.lastError,
    }));
  return {
    workspaceId: runtime.workspaceId || 0,
    tempSessionId: runtime.tempSessionId || null,
    state: runtime.connectionState,
    userId: runtime.latestMe?.id ? String(runtime.latestMe.id) : null,
    phone: runtime.latestMe?.phone ? `+${runtime.latestMe.phone}` : null,
    reconnectAttempts: runtime.reconnectAttempts,
    queueSize: await queueSizeForWorkspace(eventOutbox, runtime.workspaceId),
    lastError: runtime.lastError,
    lastCatchUpAt: runtime.lastCatchUpAt,
    lastCatchUpCount: runtime.lastCatchUpCount,
    lastInboundHotPathAt: runtime.lastInboundHotPathAt || null,
    lastInboundHotPathLatencyMs: runtime.lastInboundHotPathLatencyMs ?? null,
    lastInboundHotPathSource: runtime.lastInboundHotPathSource || null,
    lastLiveInboundHotPathAt: runtime.lastLiveInboundHotPathAt || null,
    lastLiveInboundHotPathLatencyMs: runtime.lastLiveInboundHotPathLatencyMs ?? null,
    handlersRegisteredAt: runtime.handlersRegisteredAt || null,
    liveUpdateHealth: liveUpdateHealth(runtime, options),
    catchUpScheduledAt: runtime.catchUpScheduledAt || null,
    catchUpStartedAt: runtime.catchUpStartedAt || null,
    telegramStateCounts: {
      users: telegramState?.users?.size || 0,
      chats: telegramState?.chats?.size || 0,
      messages: telegramState?.messages?.size || 0,
    },
    telegramConnector: connectorRuntimeStatus(runtime),
    telegramDurableState: {
      peers: durableState.peers || 0,
      dialogs: durableState.dialogs || 0,
      messages: durableState.messages || 0,
      mediaRefs: durableState.mediaRefs || 0,
      cursors: durableState.cursors || 0,
      persisted: persistedDurableState,
      cursorFreshness,
      lastError: runtime.lastDurableStateError || null,
    },
    telegramSyncJobs: {
      started: syncJobs.started || 0,
      succeeded: syncJobs.succeeded || 0,
      failed: syncJobs.failed || 0,
      paused: syncJobs.paused || 0,
      persisted: persistedSyncJobs,
      lastError: runtime.lastSyncJobError || null,
      lastResumeAt: runtime.lastSyncJobResumeAt || null,
      lastResumeCount: runtime.lastSyncJobResumeCount || 0,
      lastResumeActions: runtime.lastSyncJobResumeActions || 0,
      lastResumeError: runtime.lastSyncJobResumeError || null,
    },
    telegramFloodWaits: floodWaits,
  };
}
