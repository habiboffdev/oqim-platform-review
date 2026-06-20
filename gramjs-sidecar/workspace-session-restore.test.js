import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildWorkspaceRestoreTransports,
  restoreWorkspaceSession,
} from './workspace-session-restore.js';

function makeSessionStore(events) {
  return {
    snapshotSession: async (client) => `snapshot:${client.transport}`,
    saveSessionString: async (workspaceId, sessionString, metadata) => {
      events.push({ type: 'save', workspaceId, sessionString, metadata });
    },
    persistenceTargetForRuntime: (runtime) => `workspace:${runtime.workspaceId}`,
    retargetRuntimeSession: (runtime, persistTarget) => {
      events.push({ type: 'retarget', persistTarget });
    },
  };
}

function makeClient(transport, behavior) {
  const invocations = [];
  return {
    transport,
    invocations,
    getMe: async () => {
      if (behavior.getMeError) throw behavior.getMeError;
      return behavior.me ?? null;
    },
    isUserAuthorized: async () => behavior.authorized ?? false,
    invoke: async (request) => {
      invocations.push(request.className || request.constructor?.name || 'unknown');
      if (behavior.invokeError) throw behavior.invokeError;
      return behavior.invokeResult ?? true;
    },
  };
}

function makeRestoreDeps({ clientsByTransport, events }) {
  return {
    createClient: async (sessionString, persistTarget, options) => {
      events.push({
        type: 'createClient',
        sessionString,
        persistTarget,
        transport: options.transport,
      });
      return clientsByTransport[options.transport];
    },
    connectWithTimeout: async () => {},
    withRpcTimeout: async (value) => value,
    destroyRuntimeClient: async (runtime) => {
      events.push({ type: 'destroy', transport: runtime.client?.transport ?? null });
      runtime.client = null;
    },
    registerEventHandlers: (runtime) => {
      runtime.handlersRegistered = true;
      events.push({ type: 'register', transport: runtime.transport });
    },
    scheduleCatchUp: (runtime, delayMs) => {
      events.push({
        type: 'catchUp',
        transport: runtime.transport,
        delayMs,
        handlersRegistered: runtime.handlersRegistered === true,
      });
    },
    scheduleSyncJobResume: (runtime, delayMs) => {
      events.push({
        type: 'syncJobResume',
        transport: runtime.transport,
        delayMs,
        handlersRegistered: runtime.handlersRegistered === true,
      });
    },
    scheduleGapRepair: (runtime, delayMs) => {
      events.push({
        type: 'gapRepair',
        transport: runtime.transport,
        delayMs,
        handlersRegistered: runtime.handlersRegistered === true,
      });
    },
    scheduleMediaHydration: (runtime, delayMs) => {
      events.push({
        type: 'mediaHydration',
        transport: runtime.transport,
        delayMs,
        handlersRegistered: runtime.handlersRegistered === true,
      });
    },
    scheduleReconnect: (runtime) => {
      events.push({ type: 'reconnect', workspaceId: runtime.workspaceId });
    },
    normalizeTelegramAuthError: (err) => ({
      code: err?.code || (String(err?.message || '').includes('AUTH_KEY') ? 'SESSION_REVOKED' : 'UNKNOWN'),
    }),
    runtimeLabel: (runtime) => `workspace:${runtime.workspaceId}`,
  };
}

test('buildWorkspaceRestoreTransports dedupes stored, default, and tcp candidates', () => {
  assert.deepEqual(buildWorkspaceRestoreTransports('web', 'web'), ['web', 'tcp']);
  assert.deepEqual(buildWorkspaceRestoreTransports('tcp', 'web'), ['tcp', 'web']);
  assert.deepEqual(buildWorkspaceRestoreTransports(null, 'web'), ['web', 'tcp']);
});

test('restoreWorkspaceSession is read-only and tries every transport before reporting revoked', async () => {
  const events = [];
  const runtime = { workspaceId: 7, client: null, reconnectAttempts: 3 };
  const clientsByTransport = {
    web: makeClient('web', { me: null, authorized: false }),
    tcp: makeClient('tcp', { me: null, authorized: false }),
  };

  const restored = await restoreWorkspaceSession({
    workspaceId: 7,
    runtime,
    sessionRecord: { sessionString: 'stored-session', transport: 'web' },
    transportCandidates: buildWorkspaceRestoreTransports('web', 'web'),
    sessionStore: makeSessionStore(events),
    ...makeRestoreDeps({ clientsByTransport, events }),
  });

  assert.equal(restored, false);
  assert.equal(runtime.connectionState, 'disconnected');
  assert.equal(runtime.lastError, 'SESSION_REVOKED');
  assert.deepEqual(
    events.filter((event) => event.type === 'createClient').map(({ transport, persistTarget }) => ({ transport, persistTarget })),
    [
      { transport: 'web', persistTarget: undefined },
      { transport: 'tcp', persistTarget: undefined },
    ],
  );
  assert.deepEqual(events.filter((event) => event.type === 'save'), []);
  assert.deepEqual(events.filter((event) => event.type === 'retarget'), []);
  assert.deepEqual(events.filter((event) => event.type === 'reconnect'), []);
});

test('restoreWorkspaceSession persists only after a later transport proves authorization', async () => {
  const events = [];
  const runtime = { workspaceId: 9, client: null, reconnectAttempts: 2 };
  const revoked = new Error('AUTH_KEY_UNREGISTERED');
  const clientsByTransport = {
    web: makeClient('web', { getMeError: revoked }),
    tcp: makeClient('tcp', { me: { id: 123 }, authorized: true }),
  };

  const restored = await restoreWorkspaceSession({
    workspaceId: 9,
    runtime,
    sessionRecord: { sessionString: 'stored-session', transport: 'web' },
    transportCandidates: buildWorkspaceRestoreTransports('web', 'web'),
    sessionStore: makeSessionStore(events),
    ...makeRestoreDeps({ clientsByTransport, events }),
  });

  assert.equal(restored, true);
  assert.equal(runtime.connectionState, 'connected');
  assert.equal(runtime.lastError, null);
  assert.equal(runtime.transport, 'tcp');
  assert.deepEqual(
    events.filter((event) => event.type === 'createClient').map(({ transport, persistTarget }) => ({ transport, persistTarget })),
    [
      { transport: 'web', persistTarget: undefined },
      { transport: 'tcp', persistTarget: undefined },
    ],
  );
  assert.deepEqual(events.filter((event) => event.type === 'save'), [
    {
      type: 'save',
      workspaceId: 9,
      sessionString: 'snapshot:tcp',
      metadata: { transport: 'tcp' },
    },
  ]);
  assert.deepEqual(events.filter((event) => event.type === 'retarget'), [
    { type: 'retarget', persistTarget: 'workspace:9' },
  ]);
  assert.deepEqual(events.filter((event) => event.type === 'register'), [
    { type: 'register', transport: 'tcp' },
  ]);
  assert.deepEqual(clientsByTransport.tcp.invocations, ['updates.GetState']);
  assert.deepEqual(events.filter((event) => event.type === 'catchUp'), [
    { type: 'catchUp', transport: 'tcp', delayMs: 1000, handlersRegistered: true },
  ]);
  assert.deepEqual(events.filter((event) => event.type === 'syncJobResume'), [
    {
      type: 'syncJobResume',
      transport: 'tcp',
      delayMs: 1500,
      handlersRegistered: true,
    },
  ]);
  assert.deepEqual(events.filter((event) => event.type === 'gapRepair'), [
    { type: 'gapRepair', transport: 'tcp', delayMs: 1800, handlersRegistered: true },
  ]);
  assert.deepEqual(events.filter((event) => event.type === 'mediaHydration'), [
    {
      type: 'mediaHydration',
      transport: 'tcp',
      delayMs: 2200,
      handlersRegistered: true,
    },
  ]);
  assert.deepEqual(
    events
      .filter((event) => [
        'register',
        'catchUp',
        'syncJobResume',
        'gapRepair',
        'mediaHydration',
      ].includes(event.type))
      .map((event) => event.type),
    ['register', 'catchUp', 'syncJobResume', 'gapRepair', 'mediaHydration'],
  );
});
