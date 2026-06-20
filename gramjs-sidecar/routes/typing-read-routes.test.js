import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import test from 'node:test';

import {
  createOnlineRouteHandler,
  createReadRouteHandler,
  createTypingRouteHandler,
} from './typing-read-routes.js';

function makeJsonRequest(payload) {
  const req = new EventEmitter();
  req.headers = { 'content-type': 'application/json' };
  process.nextTick(() => {
    req.emit('data', Buffer.from(JSON.stringify(payload)));
    req.emit('end');
  });
  return req;
}

function makeResponse() {
  return {
    statusCode: null,
    headers: {},
    body: null,
    setHeader(name, value) {
      this.headers[name] = value;
    },
    writeHead(status, headers = {}) {
      this.statusCode = status;
      Object.assign(this.headers, headers);
    },
    end(body) {
      this.body = body ? JSON.parse(body) : null;
    },
  };
}

test('typing route validates chatId and reports success', async () => {
  let invoked = false;
  const handler = createTypingRouteHandler({
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        invoke: async () => {
          invoked = true;
        },
      },
    }),
    withRpcTimeout: (promise) => promise,
    markRuntimeRpcFailure: async () => {},
    runtimeLabel: () => 'workspace:7',
  });

  const bad = makeResponse();
  await handler(makeJsonRequest({ workspaceId: 7 }), bad, new URL('http://localhost/typing'));
  assert.equal(bad.statusCode, 400);

  const ok = makeResponse();
  await handler(makeJsonRequest({ workspaceId: 7, chatId: 123 }), ok, new URL('http://localhost/typing'));
  assert.equal(invoked, true);
  assert.deepEqual(ok.body, { ok: true, typing: true });
});

test('typing route resolves chatId before setting typing action', async () => {
  const resolvedPeer = { className: 'InputPeerUser', userId: '123', accessHash: '999' };
  let invokePeer = null;
  let resolveArgs = null;
  const handler = createTypingRouteHandler({
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        invoke: async (request) => {
          invokePeer = request.peer;
        },
      },
    }),
    withRpcTimeout: (promise) => promise,
    markRuntimeRpcFailure: async () => {},
    runtimeLabel: () => 'workspace:7',
    resolvePeer: async (runtime, chatId, options) => {
      resolveArgs = { runtime, chatId, options };
      return resolvedPeer;
    },
  });

  const res = makeResponse();
  await handler(makeJsonRequest({ workspaceId: 7, chatId: 123 }), res, new URL('http://localhost/typing'));

  assert.equal(resolveArgs.chatId, 123);
  assert.deepEqual(resolveArgs.options, { workspaceId: 7, purpose: 'typing' });
  assert.equal(invokePeer, resolvedPeer);
  assert.equal(res.body.ok, true);
  assert.equal(res.body.typing, true);
});

test('typing route can cancel the typing action', async () => {
  let actionName = null;
  const handler = createTypingRouteHandler({
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        invoke: async (request) => {
          actionName = request.action.className;
        },
      },
    }),
    withRpcTimeout: (promise) => promise,
    markRuntimeRpcFailure: async () => {},
    runtimeLabel: () => 'workspace:7',
  });

  const res = makeResponse();
  await handler(
    makeJsonRequest({ workspaceId: 7, chatId: 123, typing: false }),
    res,
    new URL('http://localhost/typing'),
  );

  assert.equal(actionName, 'SendMessageCancelAction');
  assert.deepEqual(res.body, { ok: true, typing: false });
});

test('read route validates chatId and passes maxId to client', async () => {
  let readArgs = null;
  const handler = createReadRouteHandler({
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        markAsRead: async (chatId, maxId) => {
          readArgs = { chatId, maxId };
        },
      },
    }),
    withRpcTimeout: (promise) => promise,
    markRuntimeRpcFailure: async () => {},
    runtimeLabel: () => 'workspace:7',
  });

  const bad = makeResponse();
  await handler(makeJsonRequest({ workspaceId: 7 }), bad, new URL('http://localhost/read'));
  assert.equal(bad.statusCode, 400);

  const ok = makeResponse();
  await handler(
    makeJsonRequest({ workspaceId: 7, chatId: 123, maxId: 99 }),
    ok,
    new URL('http://localhost/read'),
  );
  assert.deepEqual(readArgs, { chatId: 123, maxId: 99 });
  assert.deepEqual(ok.body, { ok: true });

  readArgs = null;
  const disabled = createReadRouteHandler({
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        markAsRead: async (chatId, maxId) => {
          readArgs = { chatId, maxId };
        },
      },
    }),
    withRpcTimeout: (promise) => promise,
    markRuntimeRpcFailure: async () => {},
    runtimeLabel: () => 'workspace:7',
    readReceiptsEnabled: false,
  });
  const skipped = makeResponse();
  await disabled(
    makeJsonRequest({ workspaceId: 7, chatId: 123, maxId: 99 }),
    skipped,
    new URL('http://localhost/read'),
  );
  assert.equal(readArgs, null);
  assert.deepEqual(skipped.body, { ok: false, skipped: true, warning: 'read_receipts_disabled' });

  const explicit = makeResponse();
  await disabled(
    makeJsonRequest({ workspaceId: 7, chatId: 123, maxId: 99, allowReadReceipt: true }),
    explicit,
    new URL('http://localhost/read'),
  );
  assert.deepEqual(readArgs, { chatId: 123, maxId: 99 });
  assert.deepEqual(explicit.body, { ok: true });
});

test('read route resolves chatId before marking messages read', async () => {
  const resolvedPeer = { className: 'InputPeerUser', userId: '123', accessHash: '999' };
  let readArgs = null;
  let resolveArgs = null;
  const handler = createReadRouteHandler({
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        markAsRead: async (chatId, maxId) => {
          readArgs = { chatId, maxId };
        },
      },
    }),
    withRpcTimeout: (promise) => promise,
    markRuntimeRpcFailure: async () => {},
    runtimeLabel: () => 'workspace:7',
    resolvePeer: async (runtime, chatId, options) => {
      resolveArgs = { runtime, chatId, options };
      return resolvedPeer;
    },
  });

  const res = makeResponse();
  await handler(
    makeJsonRequest({ workspaceId: 7, chatId: 123, maxId: 99 }),
    res,
    new URL('http://localhost/read'),
  );

  assert.equal(resolveArgs.chatId, 123);
  assert.deepEqual(resolveArgs.options, { workspaceId: 7, purpose: 'read' });
  assert.deepEqual(readArgs, { chatId: resolvedPeer, maxId: 99 });
  assert.deepEqual(res.body, { ok: true });
});

test('read route returns soft warning on Telegram failure', async () => {
  let recordedFailure = false;
  const handler = createReadRouteHandler({
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        markAsRead: async () => {
          throw new Error('RPC failed');
        },
      },
    }),
    withRpcTimeout: (promise) => promise,
    markRuntimeRpcFailure: async () => {
      recordedFailure = true;
    },
    runtimeLabel: () => 'workspace:7',
  });

  const res = makeResponse();
  await handler(
    makeJsonRequest({ workspaceId: 7, chatId: 123, allowReadReceipt: true }),
    res,
    new URL('http://localhost/read'),
  );

  assert.equal(recordedFailure, true);
  assert.deepEqual(res.body, { ok: false, warning: 'RPC failed' });
});

test('online route invokes account update status when connected', async () => {
  let invoked = false;
  const handler = createOnlineRouteHandler({
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        invoke: async (request) => {
          invoked = true;
          assert.equal(request.offline, false);
        },
      },
    }),
    withRpcTimeout: (promise) => promise,
    markRuntimeRpcFailure: async () => {},
    runtimeLabel: () => 'workspace:7',
  });

  const res = makeResponse();
  await handler(makeJsonRequest({ workspaceId: 7 }), res, new URL('http://localhost/online'));

  assert.equal(invoked, true);
  assert.deepEqual(res.body, { ok: true });

  invoked = false;
  const disabled = createOnlineRouteHandler({
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        invoke: async (request) => {
          invoked = true;
          assert.equal(request.offline, false);
        },
      },
    }),
    withRpcTimeout: (promise) => promise,
    markRuntimeRpcFailure: async () => {},
    runtimeLabel: () => 'workspace:7',
    onlinePresenceEnabled: false,
  });
  const skipped = makeResponse();
  await disabled(makeJsonRequest({ workspaceId: 7 }), skipped, new URL('http://localhost/online'));

  assert.equal(invoked, false);
  assert.deepEqual(skipped.body, { ok: false, skipped: true, warning: 'online_presence_disabled' });

  const explicit = makeResponse();
  await disabled(
    makeJsonRequest({ workspaceId: 7, allowOnlinePresence: true }),
    explicit,
    new URL('http://localhost/online'),
  );

  assert.equal(invoked, true);
  assert.deepEqual(explicit.body, { ok: true });
});
