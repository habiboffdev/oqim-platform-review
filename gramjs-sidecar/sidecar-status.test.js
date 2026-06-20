import assert from 'node:assert/strict';
import test from 'node:test';

import {
  liveUpdateHealth,
  queueSizeForWorkspace,
  runtimeLabel,
  serializeRuntimeStatus,
  summarizeLiveUpdateHealth,
} from './sidecar-status.js';

test('runtimeLabel identifies workspace, temp, and bootstrap runtimes', () => {
  assert.equal(runtimeLabel({ workspaceId: 42 }), 'workspace 42');
  assert.equal(runtimeLabel({ tempSessionId: 'tmp-1' }), 'temp tmp-1');
  assert.equal(runtimeLabel({}), 'bootstrap');
});

test('queueSizeForWorkspace is workspace scoped and failure safe', async () => {
  const eventOutbox = {
    pendingCount: async (workspaceId) => workspaceId + 2,
  };

  assert.equal(await queueSizeForWorkspace(eventOutbox, 5), 7);
  assert.equal(await queueSizeForWorkspace(eventOutbox, 0), 0);
  assert.equal(
    await queueSizeForWorkspace({ pendingCount: async () => { throw new Error('db down'); } }, 5),
    0,
  );
});

test('liveUpdateHealth distinguishes fresh, idle, online, and disconnected sessions', () => {
  const nowMs = Date.parse('2026-05-29T10:02:00.000Z');
  assert.deepEqual(
    liveUpdateHealth({
      connectionState: 'connected',
      handlersRegisteredAt: '2026-05-29T09:59:00.000Z',
      lastLiveInboundHotPathAt: '2026-05-29T10:01:00.000Z',
    }, { nowMs, staleAfterSeconds: 120 }),
    {
      state: 'fresh',
      healthy: true,
      stale: false,
      idle: false,
      ageSeconds: 60,
      staleAfterSeconds: 120,
      lastObservedAt: '2026-05-29T10:01:00.000Z',
    },
  );
  assert.equal(
    liveUpdateHealth({
      connectionState: 'connected',
      handlersRegisteredAt: '2026-05-29T09:59:00.000Z',
      lastLiveInboundHotPathAt: '2026-05-29T09:50:00.000Z',
    }, { nowMs, staleAfterSeconds: 120 }).state,
    'idle',
  );
  assert.equal(
    liveUpdateHealth({
      connectionState: 'connected',
      handlersRegisteredAt: '2026-05-29T09:59:00.000Z',
      lastLiveInboundHotPathAt: '2026-05-29T09:50:00.000Z',
    }, { nowMs, staleAfterSeconds: 120 }).healthy,
    true,
  );
  assert.equal(
    liveUpdateHealth({
      connectionState: 'connected',
      handlersRegisteredAt: '2026-05-29T09:59:00.000Z',
    }, { nowMs }).state,
    'online',
  );
  assert.equal(
    liveUpdateHealth({
      connectionState: 'connected',
      handlersRegisteredAt: '2026-05-29T09:59:00.000Z',
    }, { nowMs }).healthy,
    true,
  );
  assert.equal(
    liveUpdateHealth({ connectionState: 'disconnected' }, { nowMs }).state,
    'disconnected',
  );
});

test('summarizeLiveUpdateHealth counts live update states', () => {
  const nowMs = Date.parse('2026-05-29T10:02:00.000Z');
  const summary = summarizeLiveUpdateHealth([
    {
      connectionState: 'connected',
      handlersRegisteredAt: '2026-05-29T09:59:00.000Z',
      lastLiveInboundHotPathAt: '2026-05-29T10:01:00.000Z',
    },
    {
      connectionState: 'connected',
      handlersRegisteredAt: '2026-05-29T09:59:00.000Z',
      lastLiveInboundHotPathAt: '2026-05-29T09:50:00.000Z',
    },
    {
      connectionState: 'connected',
      handlersRegisteredAt: null,
    },
  ], { nowMs, staleAfterSeconds: 120 });

  assert.deepEqual(summary, {
    fresh: 1,
    idle: 1,
    stale: 0,
    online: 0,
    handlersMissing: 1,
    disconnected: 0,
    healthy: 2,
    total: 3,
  });
});

test('serializeRuntimeStatus projects public sidecar health fields', async () => {
  const status = await serializeRuntimeStatus(
    {
      workspaceId: 7,
      tempSessionId: null,
      connectionState: 'connected',
      latestMe: { id: 123n, phone: '998901234567' },
      reconnectAttempts: 2,
      lastError: null,
      lastCatchUpAt: '2026-04-26T08:00:00.000Z',
      lastCatchUpCount: 4,
      lastInboundHotPathAt: '2026-05-29T10:00:00.000Z',
      lastInboundHotPathLatencyMs: 42,
      lastInboundHotPathSource: 'history_sync',
      lastLiveInboundHotPathAt: '2026-05-29T10:01:00.000Z',
      lastLiveInboundHotPathLatencyMs: 31,
      handlersRegisteredAt: '2026-05-29T09:59:00.000Z',
      catchUpScheduledAt: '2026-05-29T09:59:01.000Z',
      catchUpStartedAt: '2026-05-29T09:59:02.000Z',
      telegramState: {
        users: new Map([['1', {}]]),
        chats: new Map([['2', {}]]),
        messages: new Map([['2:3', {}]]),
      },
      telegramConnector: {
        seenMessages: new Map([['7:2:3', 123]]),
        cursors: new Map([['global', { pts: 10 }]]),
        duplicatesSkipped: 1,
        gapsDetected: 2,
        lastGapAt: '2026-05-29T10:00:01.000Z',
        lastGap: {
          cursorKey: 'global',
          previousPts: 10,
          currentPts: 14,
        },
        lastDecision: {
          action: 'forward',
          reason: 'forward_with_gap_repair',
        },
      },
      telegramDurableStateCounts: {
        peers: 2,
        dialogs: 1,
        messages: 10,
        mediaRefs: 1,
        cursors: 1,
      },
      lastDurableStateError: null,
      telegramSyncJobCounts: {
        started: 5,
        succeeded: 3,
        failed: 1,
        paused: 1,
      },
      lastSyncJobError: 'FLOOD_WAIT_11',
      lastSyncJobResumeAt: '2026-05-29T12:00:00.000Z',
      lastSyncJobResumeCount: 3,
      lastSyncJobResumeActions: 2,
      lastSyncJobResumeError: null,
    },
    { pendingCount: async () => 3 },
    {
      summaryForWorkspace: async (workspaceId) => ({
        running: workspaceId === 7 ? 2 : 0,
        paused: 1,
        failed: 0,
        succeeded: 5,
      }),
    },
    {
      summaryForWorkspace: async (workspaceId) => ({
        peers: workspaceId === 7 ? 4 : 0,
        dialogs: 3,
        messages: 20,
        mediaRefs: 6,
        cursors: 2,
      }),
      cursorFreshnessForWorkspace: async (workspaceId) => ({
        latestReceivedAt: workspaceId === 7 ? 400 : null,
        latestAppliedAt: 410,
        maxAgeSeconds: 90,
        stale: false,
        cursors: [{
          scope: 'hot_path',
          channelId: '',
          pts: 10,
          seq: 20,
          qts: null,
          telegramDate: 1_700_000_000,
          receivedAt: 400,
          appliedAt: 410,
          ageSeconds: 90,
          stale: false,
          degradedState: {},
        }],
      }),
    },
    {
      nowMs: Date.parse('2026-05-29T10:02:00.000Z'),
      staleAfterSeconds: 120,
    },
  );

  assert.deepEqual(status, {
    workspaceId: 7,
    tempSessionId: null,
    state: 'connected',
    userId: '123',
    phone: '+998901234567',
    reconnectAttempts: 2,
    queueSize: 3,
    lastError: null,
    lastCatchUpAt: '2026-04-26T08:00:00.000Z',
    lastCatchUpCount: 4,
    lastInboundHotPathAt: '2026-05-29T10:00:00.000Z',
    lastInboundHotPathLatencyMs: 42,
    lastInboundHotPathSource: 'history_sync',
    lastLiveInboundHotPathAt: '2026-05-29T10:01:00.000Z',
    lastLiveInboundHotPathLatencyMs: 31,
    handlersRegisteredAt: '2026-05-29T09:59:00.000Z',
    liveUpdateHealth: {
      state: 'fresh',
      healthy: true,
      stale: false,
      idle: false,
      ageSeconds: 60,
      staleAfterSeconds: 120,
      lastObservedAt: '2026-05-29T10:01:00.000Z',
    },
    catchUpScheduledAt: '2026-05-29T09:59:01.000Z',
    catchUpStartedAt: '2026-05-29T09:59:02.000Z',
    telegramStateCounts: {
      users: 1,
      chats: 1,
      messages: 1,
    },
    telegramConnector: {
      seenMessages: 1,
      cursors: 1,
      duplicatesSkipped: 1,
      gapsDetected: 2,
      lastGapAt: '2026-05-29T10:00:01.000Z',
      lastGap: {
        cursorKey: 'global',
        previousPts: 10,
        currentPts: 14,
      },
      lastDecision: {
        action: 'forward',
        reason: 'forward_with_gap_repair',
      },
    },
    telegramDurableState: {
      peers: 2,
      dialogs: 1,
      messages: 10,
      mediaRefs: 1,
      cursors: 1,
      persisted: {
        peers: 4,
        dialogs: 3,
        messages: 20,
        mediaRefs: 6,
        cursors: 2,
      },
      cursorFreshness: {
        latestReceivedAt: 400,
        latestAppliedAt: 410,
        maxAgeSeconds: 90,
        stale: false,
        cursors: [{
          scope: 'hot_path',
          channelId: '',
          pts: 10,
          seq: 20,
          qts: null,
          telegramDate: 1_700_000_000,
          receivedAt: 400,
          appliedAt: 410,
          ageSeconds: 90,
          stale: false,
          degradedState: {},
        }],
      },
      lastError: null,
    },
    telegramSyncJobs: {
      started: 5,
      succeeded: 3,
      failed: 1,
      paused: 1,
      persisted: {
        running: 2,
        paused: 1,
        failed: 0,
        succeeded: 5,
      },
      lastError: 'FLOOD_WAIT_11',
      lastResumeAt: '2026-05-29T12:00:00.000Z',
      lastResumeCount: 3,
      lastResumeActions: 2,
      lastResumeError: null,
    },
    telegramFloodWaits: [],
  });
});

test('serializeRuntimeStatus exposes paused Telegram method queues', async () => {
  const status = await serializeRuntimeStatus(
    {
      workspaceId: 7,
      connectionState: 'connected',
      telegramMethodQueues: {
        floodWaits: new Map([
          ['dialog_sync', {
            methodClass: 'dialog_sync',
            priority: 3,
            label: 'GET_DIALOGS_7',
            retryAfter: 11,
            pausedUntilMs: Date.now() + 11_000,
            lastError: 'FLOOD_WAIT_11',
          }],
        ]),
      },
    },
    { pendingCount: async () => 0 },
  );

  assert.equal(status.telegramFloodWaits.length, 1);
  assert.equal(status.telegramFloodWaits[0].methodClass, 'dialog_sync');
  assert.equal(status.telegramFloodWaits[0].priority, 3);
  assert.equal(status.telegramFloodWaits[0].label, 'GET_DIALOGS_7');
  assert.equal(status.telegramFloodWaits[0].retryAfter, 11);
  assert.equal(status.telegramFloodWaits[0].lastError, 'FLOOD_WAIT_11');
  assert.ok(status.telegramFloodWaits[0].pausedForMs > 0);
  assert.ok(status.telegramFloodWaits[0].pausedForMs <= 11_000);
});
