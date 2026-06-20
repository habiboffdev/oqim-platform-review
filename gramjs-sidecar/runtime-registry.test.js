import assert from 'node:assert/strict';
import test from 'node:test';

import { createRuntime, createRuntimeRegistry } from './runtime-registry.js';

test('createRuntime initializes explicit disconnected runtime state', () => {
  assert.deepEqual(createRuntime('ws:7', 7), {
    key: 'ws:7',
    workspaceId: 7,
    tempSessionId: null,
    client: null,
    connectionState: 'disconnected',
    reconnectAttempts: 0,
    latestMe: null,
    telegramState: null,
    telegramConnector: null,
    telegramMethodQueues: null,
    sessionString: null,
    lastError: null,
    lastCatchUpAt: null,
    lastCatchUpCount: 0,
    lastCatchUpSuccessAt: null,
    lastInboundHotPathAt: null,
    lastInboundHotPathLatencyMs: null,
    lastInboundHotPathSource: null,
    lastLiveInboundHotPathAt: null,
    lastLiveInboundHotPathLatencyMs: null,
    handlersRegisteredAt: null,
    catchUpScheduledAt: null,
    catchUpStartedAt: null,
    catchUpFailureCount: 0,
    catchUpInFlight: false,
    catchUpTimer: null,
    gapRepairInFlight: false,
    gapRepairTimer: null,
    connectPromise: null,
    reconnectTimer: null,
    pendingPhoneCodeHash: null,
    handlersRegistered: false,
    qrAuthRunning: false,
    qrAuthStatus: 'idle',
    qrAuthError: null,
    qrAuthUser: null,
    latestQR: null,
    twoFaResolve: null,
    twoFaTimer: null,
  });
});

test('runtime registry scopes bootstrap, workspace, and temp runtimes', () => {
  const registry = createRuntimeRegistry('__bootstrap__');

  const bootstrap = registry.getBootstrap();
  const workspace = registry.getWorkspace(42);
  const temp = registry.getTemp('tmp-1');

  assert.equal(registry.getBootstrap(), bootstrap);
  assert.equal(registry.getWorkspace(42), workspace);
  assert.equal(registry.getTemp('tmp-1'), temp);
  assert.equal(registry.size(), 3);
  assert.deepEqual(
    registry.list().map((runtime) => runtime.key),
    ['__bootstrap__', 'ws:42', 'temp:tmp-1'],
  );
});

test('runtime registry deletes runtimes by key without touching other tenants', () => {
  const registry = createRuntimeRegistry('__bootstrap__');

  registry.getWorkspace(1);
  registry.getWorkspace(2);

  assert.equal(registry.deleteByKey('ws:1'), true);
  assert.equal(registry.getByKey('ws:1'), undefined);
  assert.equal(registry.getByKey('ws:2').workspaceId, 2);
  assert.equal(registry.size(), 1);
});

test('runtime registry lists workspace runtimes missing from active ids', () => {
  const registry = createRuntimeRegistry('__bootstrap__');

  registry.getBootstrap();
  registry.getWorkspace(1);
  registry.getWorkspace(2);
  registry.getWorkspace(3);
  registry.getTemp('tmp-1');

  assert.deepEqual(
    registry.staleWorkspaceRuntimes([1, 3]).map((runtime) => runtime.workspaceId),
    [2],
  );
});
