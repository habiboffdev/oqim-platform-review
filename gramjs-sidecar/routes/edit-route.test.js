import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import test from 'node:test';

import { createSendIdempotencyCache } from '../send-idempotency-cache.js';
import { createEditRouteHandler } from './edit-route.js';

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

function makeHandler(overrides = {}) {
  return createEditRouteHandler({
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        editMessage: async () => ({ id: 42, date: 1710000000 }),
      },
    }),
    withRpcTimeout: (promise) => promise,
    markRuntimeRpcFailure: async () => {},
    runtimeLabel: () => 'workspace:7',
    telegramApiError: (res, err, fallback) => {
      res.writeHead(502, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: err.message || fallback }));
    },
    editIdempotencyCache: createSendIdempotencyCache({ ttlMs: 1000, now: () => 1000 }),
    ...overrides,
  });
}

test('edit route validates required fields', async () => {
  const handler = makeHandler();
  const res = makeResponse();

  await handler(makeJsonRequest({ workspaceId: 7 }), res, new URL('http://localhost/edit'));

  assert.equal(res.statusCode, 400);
  assert.deepEqual(res.body, {
    error: 'chatId, messageId, text, and idempotencyKey required',
  });
});

test('edit route edits once and replays idempotent result', async () => {
  let edits = 0;
  let editCall = null;
  const cache = createSendIdempotencyCache({ ttlMs: 1000, now: () => 1000 });
  const handler = makeHandler({
    editIdempotencyCache: cache,
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        editMessage: async (chatId, payload) => {
          edits += 1;
          editCall = { chatId, payload };
          return { id: 42, date: 1710000000 };
        },
      },
    }),
  });

  const request = {
    workspaceId: 7,
    chatId: 123,
    messageId: 42,
    text: 'yangilangan javob',
    idempotencyKey: 'edit-k1',
  };
  const first = makeResponse();
  await handler(makeJsonRequest(request), first, new URL('http://localhost/edit'));

  const replay = makeResponse();
  await handler(makeJsonRequest(request), replay, new URL('http://localhost/edit'));

  assert.equal(edits, 1);
  assert.deepEqual(editCall, {
    chatId: 123,
    payload: { message: 42, text: 'yangilangan javob' },
  });
  assert.deepEqual(first.body, {
    externalMessageId: 42,
    chatId: '123',
    date: 1710000000,
  });
  assert.deepEqual(replay.body, {
    externalMessageId: 42,
    chatId: '123',
    date: 1710000000,
    idempotentReplay: true,
  });
});

test('edit route resolves chatId before mutating Telegram message', async () => {
  const resolvedPeer = { className: 'InputPeerUser', userId: '123', accessHash: '999' };
  let editCall = null;
  let resolveArgs = null;
  const handler = makeHandler({
    resolvePeer: async (runtime, chatId, options) => {
      resolveArgs = { runtime, chatId, options };
      return resolvedPeer;
    },
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        editMessage: async (chatId, payload) => {
          editCall = { chatId, payload };
          return { id: 42, date: 1710000000 };
        },
      },
    }),
  });

  const res = makeResponse();
  await handler(
    makeJsonRequest({
      workspaceId: 7,
      chatId: 123,
      messageId: 42,
      text: 'yangilangan javob',
      idempotencyKey: 'edit-resolved',
    }),
    res,
    new URL('http://localhost/edit'),
  );

  assert.equal(resolveArgs.chatId, 123);
  assert.deepEqual(resolveArgs.options, { workspaceId: 7, purpose: 'edit' });
  assert.deepEqual(editCall, {
    chatId: resolvedPeer,
    payload: { message: 42, text: 'yangilangan javob' },
  });
  assert.deepEqual(res.body, {
    externalMessageId: 42,
    chatId: '123',
    date: 1710000000,
  });
});

test('edit route maps expired edit window as conflict', async () => {
  const handler = makeHandler({
    ensureAuthorizedRuntime: async () => ({
      connectionState: 'connected',
      client: {
        editMessage: async () => {
          throw new Error('MESSAGE_EDIT_TIME_EXPIRED');
        },
      },
    }),
  });
  const res = makeResponse();

  await handler(
    makeJsonRequest({
      workspaceId: 7,
      chatId: 123,
      messageId: 42,
      text: 'kech qoldi',
      idempotencyKey: 'edit-expired',
    }),
    res,
    new URL('http://localhost/edit'),
  );

  assert.equal(res.statusCode, 409);
  assert.deepEqual(res.body, { error: 'Message edit window expired' });
});
