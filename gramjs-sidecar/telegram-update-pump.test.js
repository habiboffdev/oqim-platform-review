import assert from 'node:assert/strict';
import test from 'node:test';

import { Api } from 'telegram';

import { startTelegramUpdatePump } from './telegram-update-pump.js';

function makeRuntime(overrides = {}) {
  const handlers = [];
  return {
    workspaceId: 1,
    connectionState: 'connected',
    handlersRegistered: false,
    handlersRegisteredAt: null,
    client: {
      addEventHandler: (handler, builder) => handlers.push({ handler, builder }),
    },
    handlers,
    ...overrides,
  };
}

function makeDeps(events) {
  return {
    forwardInboundMessage: async (runtime, msg, options) => {
      events.push({ type: 'inbound', runtime, msg, options });
    },
    forwardEditedMessage: async (runtime, msg) => {
      events.push({ type: 'edited', runtime, msg });
    },
    forwardDeletedMessage: async (runtime, event) => {
      events.push({ type: 'deleted', runtime, event });
    },
    scheduleReconnect: (runtime) => {
      events.push({ type: 'reconnect', runtime });
    },
    runtimeLabel: (runtime) => `workspace ${runtime.workspaceId}`,
    nowSeconds: () => 1_780_000_000,
  };
}

test('startTelegramUpdatePump registers the live update handlers once', () => {
  const events = [];
  const runtime = makeRuntime();

  assert.equal(startTelegramUpdatePump(runtime, makeDeps(events)), true);
  assert.equal(startTelegramUpdatePump(runtime, makeDeps(events)), false);
  assert.equal(runtime.handlers.length, 4);
  assert.equal(runtime.handlersRegistered, true);
  assert.ok(runtime.handlersRegisteredAt);
  assert.equal(runtime.updatePumpStartedAt, runtime.handlersRegisteredAt);
});

test('new message handler forwards immediately with live timestamp', async () => {
  const events = [];
  const runtime = makeRuntime();
  startTelegramUpdatePump(runtime, makeDeps(events));

  await runtime.handlers[0].handler({ message: { id: 10, text: 'hello' } });

  assert.equal(events.length, 1);
  assert.equal(events[0].type, 'inbound');
  assert.deepEqual(events[0].msg, { id: 10, text: 'hello' });
  assert.deepEqual(events[0].options, { telegramUpdateReceivedAt: 1_780_000_000 });
});

test('edit and delete handlers forward append-only action events', async () => {
  const events = [];
  const runtime = makeRuntime();
  startTelegramUpdatePump(runtime, makeDeps(events));

  await runtime.handlers[1].handler({ message: { id: 11, text: 'edited' } });
  await runtime.handlers[2].handler({ deletedIds: [11] });

  assert.deepEqual(events.map((event) => event.type), ['edited', 'deleted']);
});

test('UpdatesTooLong reconnects because live update sequence is broken', () => {
  const events = [];
  const runtime = makeRuntime();
  startTelegramUpdatePump(runtime, makeDeps(events));

  runtime.handlers[3].handler(new Api.UpdatesTooLong({}));

  assert.equal(runtime.connectionState, 'disconnected');
  assert.deepEqual(events.map((event) => event.type), ['reconnect']);
});
